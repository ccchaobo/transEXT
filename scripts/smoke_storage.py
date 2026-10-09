"""Ad-hoc storage smoke check used during development.

Run: ``python scripts/smoke_storage.py``
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from rr_storage import JobQueue, create_database
from rr_storage.models import Document, utcnow


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = create_database(Path(tmp) / "data")
        queue = JobQueue(lease_seconds=60)

        with db.session() as session:
            doc = Document(
                id="doc_test",
                filename="sample.pdf",
                format="pdf",
                size_bytes=1234,
                sha256="sha256:abc",
                source_path="originals/doc_test/abc.pdf",
            )
            session.add(doc)
            job, created = queue.enqueue(
                session,
                job_type="parse",
                document_id=doc.id,
                request={"pages": [0, 1]},
                idempotency_key="parse:doc_test:rev1",
                total_units=2,
            )
            assert created, "first enqueue must create"

        # Idempotency: a second enqueue with the same key returns the same job.
        with db.session() as session:
            same, created2 = queue.enqueue(
                session,
                job_type="parse",
                document_id="doc_test",
                idempotency_key="parse:doc_test:rev1",
            )
            assert not created2, "duplicate key must not create a second job"
            assert same.id == job.id

        # Claim it.
        holder = "worker-A"
        with db.session() as session:
            lease = queue.claim_next(session, holder=holder)
            assert lease is not None, "job should be claimable"
            print("claimed", lease)
            queue.set_stage(session, lease, "extract")
            queue.upsert_unit(session, lease, unit_key="page:0", page_index=0, status="succeeded")
            queue.upsert_unit(
                session, lease, unit_key="page:1", page_index=1,
                status="failed", error_code="parse_failed", error_message="boom",
            )
            queue.record_usage(session, lease, {"input_chars": 500, "output_chars": 0})
            queue.finish(session, lease, status="partially_succeeded", result={"pages": 2})

        # A second claim must find nothing.
        with db.session() as session:
            assert queue.claim_next(session, holder="worker-B") is None, "no job left"
            job_row = session.get(type(job), job.id)
            print("status", job_row.status, "done", job_row.done_units, "failed", job_row.failed_units)
            assert job_row.status == "partially_succeeded"
            assert (job_row.done_units, job_row.failed_units) == (1, 1)
            events = queue.events_since(session, job.id)
            print("events", [e.kind for e in events])

        # Retry only the failed unit.
        with db.session() as session:
            retry_job, keys = queue.retry(session, job.id, idempotency_key="retry:job_test:1")
            print("retry job", retry_job.id, "units", keys)
            assert keys == ["page:1"], keys

        # Lease expiry: a new worker may reclaim after the lease lapses.
        with db.session() as session:
            lease2 = queue.claim_next(session, holder="worker-C")
            assert lease2 is not None
            print("reclaimed", lease2.job_id, "attempt", lease2.attempt_number)
            # Simulate another worker stealing the job.
            from sqlalchemy import update as sa_update
            from rr_storage.models import Job
            session.execute(
                sa_update(Job).where(Job.id == lease2.job_id).values(current_attempt_id="att_someone_else")
            )
            try:
                queue.heartbeat(session, lease2)
                raise AssertionError("heartbeat should have been fenced out")
            except Exception as exc:
                print("fencing works:", type(exc).__name__)

        print("SMOKE OK")
        db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
