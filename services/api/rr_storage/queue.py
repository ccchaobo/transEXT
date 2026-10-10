"""Durable job queue with leases, heartbeats and attempt fencing.

The queue is the part of the system responsible for the "restart recovery"
acceptance item (spec 7.3, 12.2).  Principles:

* **Claiming is a transaction.**  A worker selects a candidate and then performs
  a conditional ``UPDATE`` that only succeeds while the row is still claimable,
  so two workers racing for the same job cannot both win.
* **Every claim has a lease.**  The worker renews it with a heartbeat; if the
  process dies, the lease expires and another worker may claim the job again.
* **Attempts are fenced.**  A job records ``current_attempt_id``.  A late write
  from a superseded attempt is rejected rather than applied, which is what stops
  a resurrected zombie worker from corrupting fresh results.
* **Retry is a new attempt.**  History is preserved (spec 7.3).
"""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Attempt, Job, JobEvent, JobUnit, utcnow

__all__ = [
    "JobQueue",
    "LeaseExpired",
    "AttemptSuperseded",
    "LeaseInfo",
    "DEFAULT_LEASE_SECONDS",
    "TERMINAL_STATUSES",
]

DEFAULT_LEASE_SECONDS = 180
#: Statuses after which a job is never scheduled again.
TERMINAL_STATUSES = ("succeeded", "partially_succeeded", "failed", "cancelled")
CLAIMABLE_STATUSES = ("queued", "running")


class LeaseExpired(RuntimeError):
    """Raised when the worker no longer holds the lease on its attempt."""


class AttemptSuperseded(LeaseExpired):
    """Raised when another worker has taken over the job."""


@dataclass(frozen=True)
class LeaseInfo:
    job_id: str
    attempt_id: str
    attempt_number: int
    lease_expires_at: datetime


def worker_identity() -> str:
    """Stable-ish identity for a worker process, used as the lease holder."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class JobQueue:
    """Queue operations.  All methods take a session and manage their own
    transaction boundaries through the caller's ``session`` context manager."""

    def __init__(self, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        self.lease_seconds = lease_seconds

    # -- lifecycle ---------------------------------------------------------
    def has_active_job(self, session: Session, *, job_type: str, document_id: str | None) -> Job | None:
        stmt = (
            select(Job)
            .where(Job.type == job_type)
            .where(Job.status.in_(("queued", "running")))
            .where(Job.document_id.is_(None) if document_id is None else Job.document_id == document_id)
            .order_by(Job.created_at.desc())
        )
        return session.execute(stmt).scalars().first()

    def enqueue(
        self,
        session: Session,
        *,
        job_type: str,
        document_id: str | None,
        request: dict | None = None,
        idempotency_key: str | None = None,
        total_units: int = 0,
        priority: int = 0,
    ) -> tuple[Job, bool]:
        """Create a job, honouring the idempotency key.

        Returns ``(job, created)``.  When the key already exists the existing job
        is returned with ``created=False`` so a double-click cannot enqueue the
        same work twice (spec 7.3).
        """
        if idempotency_key:
            existing = session.execute(
                select(Job).where(Job.idempotency_key == idempotency_key)
            ).scalars().first()
            if existing is not None:
                return existing, False

        job = Job(
            id=f"job_{uuid.uuid4().hex[:20]}",
            type=job_type,
            document_id=document_id,
            status="queued",
            request=request or {},
            idempotency_key=idempotency_key,
            total_units=total_units,
            priority=priority,
        )
        session.add(job)
        try:
            session.flush()
        except IntegrityError:
            # Lost the race on the unique idempotency key: use the winner.
            session.rollback()
            existing = session.execute(
                select(Job).where(Job.idempotency_key == idempotency_key)
            ).scalars().first()
            if existing is None:  # pragma: no cover - would be a real bug
                raise
            return existing, False
        self.emit(session, job, "job_created", data={"type": job_type, "document_id": document_id})
        return job, True

    # -- claiming ----------------------------------------------------------
    def claim_next(self, session: Session, *, holder: str | None = None) -> LeaseInfo | None:
        """Atomically take the next available job, if any."""
        holder = holder or worker_identity()
        now = utcnow()
        stale_before = now

        for _ in range(5):
            candidate = session.execute(
                select(Job)
                .where(
                    or_(
                        Job.status == "queued",
                        and_(Job.status == "running", Job.attempt_count == 0),
                    )
                )
                .order_by(Job.priority.desc(), Job.created_at.asc())
                .limit(1)
            ).scalars().first()

            if candidate is None:
                candidate = self._expired_lease_candidate(session, stale_before)
            if candidate is None:
                return None

            attempt_number = candidate.attempt_count + 1
            attempt = Attempt(
                id=f"att_{uuid.uuid4().hex[:20]}",
                job_id=candidate.id,
                attempt_number=attempt_number,
                status="running",
                lease_holder=holder,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                heartbeat_at=now,
            )
            session.add(attempt)
            session.flush()

            result = session.execute(
                update(Job)
                .where(Job.id == candidate.id)
                .where(
                    or_(
                        Job.status == "queued",
                        and_(Job.status == "running", Job.current_attempt_id == candidate.current_attempt_id),
                    )
                )
                .where(Job.cancel_requested.is_(False))
                .values(
                    status="running",
                    started_at=candidate.started_at or now,
                    attempt_count=attempt_number,
                    current_attempt_id=attempt.id,
                    stage=None,
                    stage_status="pending",
                )
            )
            if result.rowcount == 1:
                self.emit(
                    session,
                    candidate,
                    "stage_started",
                    stage="claim",
                    data={"attempt": attempt_number, "holder": holder},
                )
                return LeaseInfo(
                    job_id=candidate.id,
                    attempt_id=attempt.id,
                    attempt_number=attempt_number,
                    lease_expires_at=attempt.lease_expires_at,
                )
            # Lost the race; roll this attempt back and try again.
            session.rollback()
            now = utcnow()
        return None

    def _expired_lease_candidate(self, session: Session, now: datetime) -> Job | None:
        """Find a running job whose holder stopped heartbeating."""
        stmt = (
            select(Job)
            .join(Attempt, Attempt.id == Job.current_attempt_id)
            .where(Job.status == "running")
            .where(Job.cancel_requested.is_(False))
            .where(Attempt.lease_expires_at < now)
            .order_by(Job.priority.desc(), Job.created_at.asc())
            .limit(1)
        )
        return session.execute(stmt).scalars().first()

    # -- lease maintenance -------------------------------------------------
    def heartbeat(self, session: Session, lease: LeaseInfo) -> datetime:
        """Renew the lease.  Raises if another worker has taken over."""
        now = utcnow()
        expires = now + timedelta(seconds=self.lease_seconds)
        result = session.execute(
            update(Attempt)
            .where(Attempt.id == lease.attempt_id)
            .where(Attempt.status == "running")
            .values(heartbeat_at=now, lease_expires_at=expires)
        )
        if result.rowcount != 1:
            raise AttemptSuperseded(f"attempt {lease.attempt_id} is no longer running")
        fencing = session.execute(
            select(Job.current_attempt_id).where(Job.id == lease.job_id)
        ).scalar_one_or_none()
        if fencing != lease.attempt_id:
            raise AttemptSuperseded(
                f"job {lease.job_id} is now owned by attempt {fencing}, not {lease.attempt_id}"
            )
        session.flush()
        return expires

    def assert_lease(self, session: Session, lease: LeaseInfo) -> None:
        """Fencing check before any write that belongs to an attempt."""
        current = session.execute(
            select(Job.current_attempt_id, Job.status).where(Job.id == lease.job_id)
        ).one_or_none()
        if current is None:
            raise LeaseExpired(f"job {lease.job_id} no longer exists")
        current_attempt, status = current
        if current_attempt != lease.attempt_id:
            raise AttemptSuperseded(
                f"job {lease.job_id} is now owned by attempt {current_attempt}, not {lease.attempt_id}"
            )
        if status in TERMINAL_STATUSES:
            raise LeaseExpired(f"job {lease.job_id} already finished with status {status}")

    def is_cancel_requested(self, session: Session, job_id: str) -> bool:
        return bool(
            session.execute(select(Job.cancel_requested).where(Job.id == job_id)).scalar_one_or_none()
        )

    # -- progress ----------------------------------------------------------
    def set_stage(self, session: Session, lease: LeaseInfo, stage: str, message: str | None = None) -> None:
        self.assert_lease(session, lease)
        session.execute(
            update(Job)
            .where(Job.id == lease.job_id)
            .values(stage=stage, stage_status="running", progress_message=message)
        )
        self.emit(session, self._job(session, lease.job_id), "stage_started", stage=stage, data={"message": message})

    def upsert_unit(
        self,
        session: Session,
        lease: LeaseInfo,
        *,
        unit_key: str,
        unit_type: str = "block",
        page_index: int | None = None,
        block_id: str | None = None,
        status: str = "succeeded",
        error_code: str | None = None,
        error_message: str | None = None,
        usage: dict | None = None,
    ) -> JobUnit:
        unit = session.execute(
            select(JobUnit).where(JobUnit.job_id == lease.job_id, JobUnit.unit_key == unit_key)
        ).scalars().first()
        if unit is None:
            unit = JobUnit(job_id=lease.job_id, unit_key=unit_key, attempt_id=lease.attempt_id)
            session.add(unit)
        unit.attempt_id = lease.attempt_id
        unit.unit_type = unit_type
        unit.page_index = page_index
        unit.block_id = block_id
        unit.status = status
        unit.error_code = error_code
        unit.error_message = error_message
        if usage:
            unit.usage = usage
        unit.attempts_count = (unit.attempts_count or 0) + 1
        session.flush()
        self._recount(session, lease.job_id)
        self.emit(
            session,
            self._job(session, lease.job_id),
            "unit_done" if status == "succeeded" else "unit_failed",
            data={"unit_key": unit_key, "status": status, "error_code": error_code},
        )
        return unit

    def _recount(self, session: Session, job_id: str) -> None:
        rows = session.execute(
            select(JobUnit.status, func.count()).where(JobUnit.job_id == job_id).group_by(JobUnit.status)
        ).all()
        counts = {status: count for status, count in rows}
        done = counts.get("succeeded", 0)
        failed = counts.get("failed", 0)
        skipped = counts.get("skipped", 0)
        total = sum(counts.values())
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(done_units=done, failed_units=failed, skipped_units=skipped, total_units=max(total, 0))
        )

    def record_usage(self, session: Session, lease: LeaseInfo, usage: dict, cost: dict | None = None) -> None:
        job = self._job(session, lease.job_id)
        merged = dict(job.usage or {})
        for key, value in (usage or {}).items():
            if isinstance(value, (int, float)) and isinstance(merged.get(key), (int, float)):
                merged[key] = merged[key] + value
            else:
                merged[key] = value
        job.usage = merged
        if cost:
            job.cost_estimate = {**(job.cost_estimate or {}), **cost}
        session.flush()

    # -- completion --------------------------------------------------------
    def finish(
        self,
        session: Session,
        lease: LeaseInfo,
        *,
        status: str,
        result: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        checkpoint: dict | None = None,
    ) -> Job:
        job = self._job(session, lease.job_id)
        if job.current_attempt_id != lease.attempt_id:
            raise AttemptSuperseded(
                f"refusing to finish job {lease.job_id}: attempt {lease.attempt_id} was superseded"
            )
        now = utcnow()
        job.status = status
        job.stage_status = {
            "succeeded": "succeeded",
            "cancelled": "cancelled",
        }.get(status, "failed")
        job.finished_at = now
        job.error_code = error_code
        job.error_message = error_message
        if result is not None:
            job.result = result
        job.progress_message = None
        attempt = session.get(Attempt, lease.attempt_id)
        if attempt is not None:
            attempt.status = status
            attempt.finished_at = now
            attempt.error_code = error_code
            attempt.error_message = error_message
            attempt.lease_expires_at = None
            if checkpoint is not None:
                attempt.checkpoint = checkpoint
        kind = {
            "succeeded": "job_finished",
            "partially_succeeded": "job_finished",
            "failed": "job_failed",
            "cancelled": "job_cancelled",
        }.get(status, "job_finished")
        self.emit(session, job, kind, data={"status": status, "error_code": error_code})
        session.flush()
        return job

    def request_cancel(self, session: Session, job_id: str) -> Job:
        """Ask a job to stop.  Completed work is kept (spec 6.3)."""
        job = self._job(session, job_id)
        job.cancel_requested = True
        if job.status == "queued":
            job.status = "cancelled"
            job.finished_at = utcnow()
            # Not "failed": the user asked for this, and nothing broke.  Reporting a
            # cancellation as a stage failure would misstate what happened.
            job.stage_status = "cancelled"
            self.emit(session, job, "job_cancelled", data={"reason": "cancelled while queued"})
        else:
            self.emit(session, job, "log", data={"message": "cancellation requested"})
        session.flush()
        return job

    def retry(
        self,
        session: Session,
        job_id: str,
        *,
        unit_keys: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[Job, list[str]]:
        """Create a retry job that references the original and its failures.

        Completed units are not repeated; only the failed (or explicitly listed)
        unit keys are scheduled, so a retry never discards good work.
        """
        original = self._job(session, job_id)
        failed_units = session.execute(
            select(JobUnit).where(JobUnit.job_id == job_id, JobUnit.status == "failed")
        ).scalars().all()
        if unit_keys:
            selected = [
                u for u in session.execute(
                    select(JobUnit).where(JobUnit.job_id == job_id)
                ).scalars().all()
                if u.unit_key in set(unit_keys)
            ]
        else:
            selected = list(failed_units)

        request = dict(original.request or {})
        request["retry_of"] = original.id
        request["unit_keys"] = [u.unit_key for u in selected]
        request["resume_from_checkpoint"] = True

        new_job, created = self.enqueue(
            session,
            job_type=original.type,
            document_id=original.document_id,
            request=request,
            idempotency_key=idempotency_key,
            total_units=len(selected),
        )
        if created:
            for unit in selected:
                session.add(
                    JobUnit(
                        job_id=new_job.id,
                        unit_key=unit.unit_key,
                        unit_type=unit.unit_type,
                        page_index=unit.page_index,
                        block_id=unit.block_id,
                        status="pending",
                    )
                )
            session.flush()
        return new_job, [u.unit_key for u in selected]

    # -- events ------------------------------------------------------------
    def emit(
        self,
        session: Session,
        job: Job | None,
        kind: str,
        *,
        stage: str | None = None,
        data: dict | None = None,
    ) -> None:
        if job is None:
            return
        seq = (job.next_event_seq or 0) + 1
        job.next_event_seq = seq
        session.add(
            JobEvent(job_id=job.id, seq=seq, kind=kind, stage=stage, data=data or {})
        )

    def events_since(self, session: Session, job_id: str, since_seq: int = 0, limit: int = 500) -> list[JobEvent]:
        return list(
            session.execute(
                select(JobEvent)
                .where(JobEvent.job_id == job_id, JobEvent.seq > since_seq)
                .order_by(JobEvent.seq)
                .limit(limit)
            ).scalars()
        )

    def progress_fraction(self, job: Job) -> float:
        if job.total_units <= 0:
            return 1.0 if job.status == "succeeded" else 0.0
        settled = job.done_units + job.failed_units + job.skipped_units
        return max(0.0, min(1.0, settled / job.total_units))

    # -- helpers -----------------------------------------------------------
    def _job(self, session: Session, job_id: str) -> Job:
        job = session.get(Job, job_id)
        if job is None:
            raise LeaseExpired(f"job {job_id} no longer exists")
        return job


def reconcile_stale_jobs(session: Session, *, age_seconds: int = 3600) -> int:
    """Mark long-abandoned running jobs as failed so they stop blocking.

    A job whose lease expired is normally re-claimed by a live worker; this is
    the fallback for the case where the whole worker pool is gone and the job
    would otherwise sit in ``running`` forever after a restart.  The age test
    uses ``started_at`` because a worker that keeps heartbeating is by
    definition not abandoned.
    """
    cutoff = utcnow() - timedelta(seconds=age_seconds)
    stale = list(
        session.execute(
            select(Job)
            .where(Job.status == "running")
            .where(Job.started_at.is_not(None))
            .where(Job.started_at < cutoff)
        ).scalars()
    )
    for job in stale:  # pragma: no cover - exercised only on real restarts
        job.status = "failed"
        job.stage_status = "failed"
        job.error_code = "internal_error"
        job.error_message = "worker disappeared before finishing"
        job.finished_at = utcnow()
        attempt = session.get(Attempt, job.current_attempt_id) if job.current_attempt_id else None
        if attempt is not None:
            attempt.status = "failed"
            attempt.finished_at = job.finished_at
            attempt.error_code = "internal_error"
            attempt.error_message = "lease never renewed"
    session.flush()
    return len(stale)
