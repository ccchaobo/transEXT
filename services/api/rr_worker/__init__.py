"""The worker: claim a job, run its stage, and finish it honestly.

The spec (7) asks for one worker process where every stage is a leased, fenced job.  The rules
this module implements, and why each one is here:

* **Claim, then lease.**  ``claim_next`` hands out a lease with an expiry; the worker heartbeats
  while it works.  A job whose lease expires is reclaimed by another attempt, so a worker that
  dies mid-document does not strand it.
* **Fence every write.**  ``assert_lease`` is called before each unit is recorded, and
  ``finish`` refuses an attempt that has been superseded.  Without that, a worker that had
  stalled could come back and overwrite the results of the worker that took over.
* **Cancellation is honoured between units, not before them.**  A translate job can cover
  thousands of blocks, so it is run in batches and the cancel flag is checked between batches.
  Cancelling reports ``cancelled``, which is a distinct outcome from ``failed``.
* **Partial is its own status.**  A translate job where some blocks came back ambiguous ends
  ``partially_succeeded``; the spec is explicit that a partial completion must be visible rather
  than rounded to success or failure.
* **Losing the race is not a failure.**  ``LeaseExpired`` and ``AttemptSuperseded`` mean another
  attempt owns the job now, so this worker stops without finishing anything.  Writing a failure
  there would mark a healthy job as broken.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from rr_contracts import ErrorCode, JobStatus, JobType, StageStatus
from rr_services.parsing import ParseService
from rr_services.translation import (
    TranslationBudgetExceeded,
    TranslationNotConfigured,
    TranslationPlan,
    TranslationService,
    error_code_for,
)
from rr_storage import (
    AttemptSuperseded,
    Document,
    Job,
    JobQueue,
    LeaseExpired,
    LeaseInfo,
    reconcile_stale_jobs,
)

log = logging.getLogger("rr_worker")

#: Blocks per translation batch.  The batch is what makes a long job cancellable and keeps the
#: lease alive, so it is bounded by the lease duration rather than by the provider's throughput.
DEFAULT_TRANSLATION_BATCH = 25

#: Fallback error code per job type, for an exception the service layer did not classify.
#: ``rr_services.translation.error_code_for`` falls back to ``translation_failed``, which is the
#: right word for a translation and the wrong one for anything else.  A DOCX whose parse is not
#: implemented was being recorded as a translation failure, which would send a reader looking for
#: a problem with their API key.  Found by importing a DOCX through the running server.
_FALLBACK_ERROR_CODE = {
    JobType.PARSE.value: ErrorCode.PARSE_FAILED.value,
    JobType.OCR.value: ErrorCode.OCR_FAILED.value,
    JobType.TRANSLATE.value: ErrorCode.TRANSLATION_FAILED.value,
    # The vocabulary has no export-specific code; producing the Markdown is a conversion step,
    # and inventing a code that is not in the contract would give the UI a value it cannot map.
    JobType.EXPORT.value: ErrorCode.CONVERSION_FAILED.value,
    JobType.CONVERT.value: ErrorCode.CONVERSION_FAILED.value,
}


def _error_code_for(exc: BaseException, job_type: str) -> str:
    """The error code for a failed job, in the vocabulary of that job's domain."""
    if isinstance(exc, NotImplementedError):
        # "parsing docx is not implemented yet" describes the format, not a fault.
        return ErrorCode.UNSUPPORTED_FORMAT.value
    code = error_code_for(exc)
    if code != ErrorCode.TRANSLATION_FAILED.value:
        # A classified error keeps its specific code whatever the job type.
        return code
    return _FALLBACK_ERROR_CODE.get(job_type, ErrorCode.PARSE_FAILED.value)


#: Job status to document stage status.  The two vocabularies agree on every value the worker
#: produces except that a job cancelled before it ran is not a stage failure.
_STAGE_STATUS = {
    JobStatus.SUCCEEDED.value: StageStatus.SUCCEEDED.value,
    JobStatus.PARTIALLY_SUCCEEDED.value: StageStatus.PARTIALLY_SUCCEEDED.value,
    JobStatus.FAILED.value: StageStatus.FAILED.value,
    JobStatus.CANCELLED.value: StageStatus.CANCELLED.value,
    JobStatus.RUNNING.value: StageStatus.RUNNING.value,
    JobStatus.QUEUED.value: StageStatus.PENDING.value,
}


@dataclass(slots=True)
class StageOutcome:
    """What a stage achieved.  Handlers return this; the worker turns it into a job status."""

    status: str = JobStatus.SUCCEEDED.value
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def cancelled(cls) -> "StageOutcome":
        return cls(status=JobStatus.CANCELLED.value)

    @classmethod
    def partial(cls, result: dict[str, Any], message: str, code: str | None = None) -> "StageOutcome":
        return cls(
            status=JobStatus.PARTIALLY_SUCCEEDED.value,
            result=result,
            error_code=code or ErrorCode.EXTERNAL_UNKNOWN_RESULT.value,
            error_message=message,
        )


@dataclass(slots=True)
class WorkResult:
    """The record of one ``run_once`` call, for logging and for tests."""

    job_id: str
    job_type: str
    status: str
    detail: dict[str, Any] = field(default_factory=dict)


class Handler(Protocol):
    def __call__(
        self, worker: "Worker", session: Session, lease: LeaseInfo, job: Job
    ) -> StageOutcome: ...


class Worker:
    """One process, one job at a time."""

    def __init__(
        self,
        settings: Any,
        workspace: Any = None,
        queue: JobQueue | None = None,
        *,
        database: Any = None,
        provider: Any = None,
        provider_factory: Callable[[], Any] | None = None,
        handlers: dict[str, Handler] | None = None,
    ) -> None:
        self.settings = settings
        self.workspace = workspace
        self.queue = queue or JobQueue(lease_seconds=getattr(settings, "lease_seconds", 120))
        self.database = database
        # A fixed ``provider`` is for tests, which inject a stub and want exactly that stub.
        # ``provider_factory`` is for the real worker, and it exists because of a defect: the
        # CLI constructed ``Worker(...)`` without either, so ``self.provider`` was None for the
        # whole run and *every* translate job ended in ``TranslationNotConfigured`` even with a
        # correct API key in the environment.  Following the 409 message's own instructions
        # produced a second, identical failure, and nothing said why.
        #
        # Resolving per job rather than once at startup is the point of the factory: the reader
        # can change the model or the endpoint in the settings panel, and the next job uses it.
        # A provider built at startup would pin the process to whatever the environment said
        # when the worker began, which is the behaviour that made ``.env`` the only way in.
        self.provider = provider
        self.provider_factory = provider_factory
        self.handlers: dict[str, Handler] = {
            JobType.PARSE.value: _parse_document,
            JobType.TRANSLATE.value: _translate_document,
        }
        if handlers:
            self.handlers.update(handlers)

    def resolve_provider(self) -> Any:
        """The provider to use for the job about to run, or ``None`` when unconfigured."""
        if self.provider is not None:
            return self.provider
        if self.provider_factory is None:
            return None
        return self.provider_factory()

    # -- one job ----------------------------------------------------------

    def run_once(self, session: Session) -> WorkResult | None:
        """Claim and run a single job.  Returns None when there was nothing to do."""
        lease = self.queue.claim_next(session)
        if lease is None:
            return None
        job = session.get(Job, lease.job_id)
        if job is None:  # pragma: no cover - the queue just created a lease for it
            return None

        if self.queue.is_cancel_requested(session, job.id):
            self.queue.finish(session, lease, status=JobStatus.CANCELLED.value)
            session.commit()
            return WorkResult(job.id, job.type, JobStatus.CANCELLED.value)

        handler = self.handlers.get(job.type)
        if handler is None:
            # An unsupported job type is a real failure: the document is waiting on work that
            # nothing in this process can do, and silence would look like success.
            outcome = StageOutcome(
                status=JobStatus.FAILED.value,
                error_code=ErrorCode.UNSUPPORTED_FORMAT.value,
                error_message=f"no handler is registered for job type {job.type!r}",
            )
        else:
            outcome = self._dispatch(handler, session, lease, job)

        try:
            self.queue.finish(
                session,
                lease,
                status=outcome.status,
                result=outcome.result,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
            )
            self._settle_document(session, job, outcome)
        except (AttemptSuperseded, LeaseExpired):
            # Another attempt owns this job now.  Recording anything here would corrupt it.
            log.warning("job %s was taken over before it could be finished", lease.job_id)
            session.rollback()
            return WorkResult(lease.job_id, job.type, "superseded")
        session.commit()
        return WorkResult(lease.job_id, job.type, outcome.status, outcome.result)

    def _settle_document(self, session: Session, job: Job, outcome: StageOutcome) -> None:
        """Record the outcome on the document, not only on the job.

        A document whose parse job failed kept ``parse_status="pending"`` forever, because only a
        successful parse wrote to it.  A reader looking at the library saw a document that was
        still waiting, with nothing left to wait for.  The status a reader sees has to reflect
        what actually happened, so a failed or cancelled stage is written here too.

        A ``partially_succeeded`` stage is recorded as partial: the spec requires partial
        completion to be visible rather than rounded to success or failure.
        """
        if not job.document_id:
            return
        document = session.get(Document, job.document_id)
        if document is None:
            return
        if job.type == JobType.PARSE.value:
            document.parse_status = _STAGE_STATUS.get(outcome.status, outcome.status)
            if outcome.status == JobStatus.FAILED.value:
                # Nothing was parsed, so there is no active revision and no page count to show.
                document.progress = 0.0
        elif job.type == JobType.TRANSLATE.value:
            document.translate_status = _STAGE_STATUS.get(outcome.status, outcome.status)

    def _dispatch(
        self, handler: Handler, session: Session, lease: LeaseInfo, job: Job
    ) -> StageOutcome:
        try:
            return handler(self, session, lease, job)
        except (LeaseExpired, AttemptSuperseded):
            raise
        except TranslationBudgetExceeded as exc:
            return StageOutcome(
                status=JobStatus.FAILED.value,
                error_code=ErrorCode.BUDGET_EXCEEDED.value,
                error_message=str(exc),
            )
        except TranslationNotConfigured as exc:
            return StageOutcome(
                status=JobStatus.FAILED.value,
                error_code=ErrorCode.TRANSLATION_NOT_CONFIGURED.value,
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - the worker must never die on one job
            log.exception("job %s failed", lease.job_id)
            return StageOutcome(
                status=JobStatus.FAILED.value,
                error_code=_error_code_for(exc, job.type),
                error_message=f"{type(exc).__name__}: {exc}",
            )

    # -- keep the lease alive ---------------------------------------------

    def beat(self, session: Session, lease: LeaseInfo, stage: str, message: str | None = None) -> bool:
        """Heartbeat and write the current stage.

        Returns False when the job has been cancelled, which is the worker's cue to stop at the
        next boundary.  The lease is asserted first, so a stalled worker discovers it has been
        taken over instead of continuing to write.
        """
        self.queue.assert_lease(session, lease)
        self.queue.heartbeat(session, lease)
        self.queue.set_stage(session, lease, stage, message)
        session.commit()
        return not self.queue.is_cancel_requested(session, lease.job_id)

    # -- loop -------------------------------------------------------------

    def serve(
        self,
        *,
        poll_seconds: float = 1.0,
        stop: Callable[[], bool] | None = None,
        max_jobs: int | None = None,
        exit_when_idle: bool = False,
        idle_grace: float = 1.0,
    ) -> int:
        """Run jobs until ``stop`` returns True, ``max_jobs`` is reached, or the queue drains.

        Reclaims lease-expired jobs first: a worker that was killed mid-document leaves jobs in
        ``running`` with a dead attempt, and they would otherwise never be retried.

        ``exit_when_idle`` is what makes this usable from a script.  ``max_jobs`` alone is not
        enough: it means "stop after N jobs", so a command line that asks for one job and finds
        an empty queue waits forever for work that is not coming.  That was a real hang, found by
        running the CLI rather than the tests.  ``idle_grace`` is how long the queue must stay
        empty before concluding that it has drained - a grace period, because a producer may
        enqueue the next job while this one is finishing, and exiting immediately would leave it
        for the next process.
        """
        if self.database is None:
            raise RuntimeError("serve() needs a database to open one session per job")
        done = 0
        idle_since: float | None = None
        with self.database.session() as session:
            reclaimed = recover(session)
            session.commit()
            if reclaimed:
                log.info("reclaimed %d job(s) whose lease had expired", reclaimed)

        while not (stop and stop()):
            if max_jobs is not None and done >= max_jobs:
                break
            with self.database.session() as session:
                result = self.run_once(session)
            if result is None:
                if exit_when_idle:
                    now = time.monotonic()
                    idle_since = now if idle_since is None else idle_since
                    if now - idle_since >= idle_grace:
                        break
                # An empty queue ends the loop when there is no poll interval.  Without this
                # branch `poll_seconds=0` skips the sleep and spins on an empty queue forever,
                # which is how a test of this function hung rather than failed.
                if poll_seconds and poll_seconds > 0:
                    time.sleep(poll_seconds)
                    continue
                break
            idle_since = None
            done += 1
            log.info("job %s (%s) -> %s", result.job_id, result.job_type, result.status)
        return done


def recover(session: Session, *, age_seconds: int = 3600) -> int:
    """Put lease-expired and long-stale jobs back into a runnable state."""
    return reconcile_stale_jobs(session, age_seconds=age_seconds)


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def _parse_document(worker: Worker, session: Session, lease: LeaseInfo, job: Job) -> StageOutcome:
    """Extract, lay out, type and persist one document."""
    if not job.document_id:
        return StageOutcome(
            status=JobStatus.FAILED.value,
            error_code=ErrorCode.INVALID_REQUEST.value,
            error_message="a parse job must name a document",
        )
    if not worker.beat(session, lease, "extract"):
        return StageOutcome.cancelled()

    service = ParseService(worker.settings, worker.workspace, worker.queue)
    outcome = service.run(session, lease, job.document_id)
    session.commit()

    if not outcome.succeeded:
        # The parse recorded what it could.  Reporting success here would tell the reader a
        # document is ready that is not.
        return StageOutcome(
            status=JobStatus.FAILED.value,
            error_code=ErrorCode.PARSE_FAILED.value,
            error_message="; ".join(str(w) for w in (outcome.warnings or [])) or "parse failed",
            result={"revision_id": outcome.revision_id, "stats": outcome.stats},
        )

    result = {
        "revision_id": outcome.revision_id,
        "pages": outcome.pages,
        "blocks": outcome.blocks,
        "translatable_blocks": outcome.translatable_blocks,
        "warnings": list(outcome.warnings or []),
        "stats": outcome.stats,
    }
    if outcome.translatable_blocks == 0 and outcome.blocks > 0:
        # A page with no usable text is not a failure, but it is not a clean parse either: the
        # document needs OCR, which does not exist yet, so saying "partial" is the honest call.
        return StageOutcome.partial(result, "no translatable text was found; OCR is required")
    return StageOutcome(status=JobStatus.SUCCEEDED.value, result=result)


def _translate_document(worker: Worker, session: Session, lease: LeaseInfo, job: Job) -> StageOutcome:
    """Translate a document's blocks in batches, reusing what is already cached."""
    if not job.document_id:
        return StageOutcome(
            status=JobStatus.FAILED.value,
            error_code=ErrorCode.INVALID_REQUEST.value,
            error_message="a translate job must name a document",
        )
    request = job.request or {}
    target_language = request.get("target_language") or "zh"
    source_language = request.get("source_language") or "en"
    batch_size = max(1, int(request.get("batch_units") or DEFAULT_TRANSLATION_BATCH))

    if not worker.beat(session, lease, "plan"):
        return StageOutcome.cancelled()

    service = TranslationService(
        worker.settings, worker.workspace, worker.queue, provider=worker.resolve_provider()
    )
    plan = service.plan(
        session,
        job.document_id,
        source_language=source_language,
        target_language=target_language,
    )
    session.commit()

    summary: dict[str, Any] = {
        "target_language": target_language,
        "translatable_blocks": len(plan.units),
        "reused": len(plan.reusable_units),
        "skipped_non_translatable": plan.skipped_non_translatable,
        "glossary_version": plan.glossary_version,
        "provider": plan.provider,
        "model": plan.model,
    }
    if plan.is_empty:
        return StageOutcome(status=JobStatus.SUCCEEDED.value, result={**summary, "requested": 0})

    # The guard the spec asks for: refuse an oversized job *before* the first request is sent,
    # so a mis-set limit costs nothing.
    limit = worker.settings.max_translation_units
    if limit and len(plan.pending_units) > limit:
        return StageOutcome(
            status=JobStatus.FAILED.value,
            error_code=ErrorCode.BUDGET_EXCEEDED.value,
            error_message=(
                f"plan covers {len(plan.pending_units)} units but the limit is {limit}; "
                "refusing before any request is sent"
            ),
            result=summary,
        )

    pending = plan.pending_units
    translated = failed = unknown = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        if not worker.beat(session, lease, "translate", f"{start + len(batch)}/{len(pending)}"):
            return StageOutcome(
                status=JobStatus.CANCELLED.value,
                result={
                    **summary,
                    "requested": len(pending),
                    "translated": translated,
                    "failed": failed,
                    "unknown": unknown,
                },
            )

        # The plan is narrowed rather than re-planned, so the cache decisions made above still
        # hold and no block is sent twice.
        run = service.execute(session, _narrow(plan, batch), lease=lease)
        session.commit()

        for row in run.saved:
            # Fencing: a stalled worker must not write a unit after another attempt took over.
            worker.queue.assert_lease(session, lease)
            worker.queue.upsert_unit(
                session,
                lease,
                unit_key=row.block_id,
                unit_type="block",
                block_id=row.block_id,
                status="succeeded" if row.status == "ok" else row.status,
                error_code=row.error_code,
                error_message=row.error_message,
                usage=row.usage,
            )
            if row.status == "ok":
                translated += 1
            elif row.status == "unknown":
                unknown += 1
            else:
                failed += 1
        if run.outcome is not None:
            worker.queue.record_usage(session, lease, run.outcome.usage or {})
        session.commit()

    result = {
        **summary,
        "requested": len(pending),
        "translated": translated,
        "failed": failed,
        "unknown": unknown,
    }
    if unknown:
        # Ambiguous results are never retried blindly and never reported as success.  The units
        # are recorded with their own status so the reader sees exactly which blocks are in doubt.
        return StageOutcome.partial(
            result,
            f"{unknown} block(s) may or may not have been processed by the service; "
            "they are marked unknown and were not retried",
        )
    if failed:
        return StageOutcome(
            status=JobStatus.PARTIALLY_SUCCEEDED.value,
            result=result,
            error_code=ErrorCode.TRANSLATION_FAILED.value,
            error_message=f"{failed} block(s) failed after retries",
        )
    return StageOutcome(status=JobStatus.SUCCEEDED.value, result=result)


def _narrow(plan: TranslationPlan, batch: list[Any]) -> TranslationPlan:
    """A copy of the plan covering only ``batch``, keeping its cache decisions."""
    wanted = {unit.block_id for unit in batch}
    return TranslationPlan(
        document_id=plan.document_id,
        revision_id=plan.revision_id,
        source_language=plan.source_language,
        target_language=plan.target_language,
        provider=plan.provider,
        model=plan.model,
        prompt_version=plan.prompt_version,
        glossary_version=plan.glossary_version,
        glossary=plan.glossary,
        params=plan.params,
        units=[unit for unit in plan.units if unit.block_id in wanted],
        skipped_non_translatable=plan.skipped_non_translatable,
    )


__all__ = [
    "DEFAULT_TRANSLATION_BATCH",
    "Handler",
    "StageOutcome",
    "WorkResult",
    "Worker",
    "recover",
]
