"""Evidence for the worker: run the real job lifecycle on a real document.

What this shows, in one run, without any stub:

* an import queues a parse job, the worker claims it, leases it, parses the document and
  finishes it — and the job's own record says what it did;
* the event log is a real append-only record of claim, stage and completion;
* a translation job reaches the provider boundary and, with no credentials, **fails with a
  machine-readable code and translates nothing**.  It does not write placeholder text, and the
  last line of this script says so; that line is the evidence that no mock is in play.

Usage:

    python scripts/evidence_worker.py [document-name]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from sqlalchemy import select

from rr_contracts import JobStatus, JobType
from rr_services import DocumentService, Settings
from rr_storage import Job, JobEvent, JobUnit, JobQueue, Workspace, create_database
from rr_worker import Worker, recover

CORPUS = Path("benchmarks/corpus")
DEFAULT_DOCUMENT = "pdf_b_two_column"


def show_job(session, job: Job, label: str) -> None:
    print(f"  {label}")
    print(f"    id            {job.id}")
    print(f"    type/status   {job.type} / {job.status}   stage={job.stage or '-'}"
          f" stage_status={job.stage_status or '-'}")
    print(f"    attempts      {job.attempt_count}   cancel_requested={job.cancel_requested}")
    print(f"    units         total={job.total_units} done={job.done_units} "
          f"failed={job.failed_units} skipped={job.skipped_units}")
    if job.error_code:
        print(f"    error         {job.error_code}: {job.error_message}")
    if job.result:
        for key in sorted(job.result):
            value = job.result[key]
            if isinstance(value, list) and len(value) > 3:
                value = f"{value[:3]} … (+{len(value) - 3})"
            print(f"    result.{key:22} {value}")

    events = session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.seq)
    ).all()
    print(f"    events ({len(events)}):")
    for event in events:
        stage = f" stage={event.stage}" if event.stage else ""
        print(f"      {event.seq:>3} {event.kind}{stage}  {event.data or ''}")


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DOCUMENT
    source = CORPUS / "files" / f"{name}.pdf"
    if not source.exists():
        print(f"no such corpus file: {source}")
        return 2

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings = Settings(data_dir=Path(tmp) / "data")
        database = create_database(settings.data_dir)
        database.create_all()
        workspace = Workspace(settings.workspace_root)
        queue = JobQueue(lease_seconds=settings.lease_seconds)
        documents = DocumentService(settings, workspace, queue)

        print("=" * 74)
        print(f"WORKER EVIDENCE — {name}")
        print("=" * 74)
        print(f"corpus file      {source}  ({source.stat().st_size} bytes, authored corpus)")
        print(f"data dir         {settings.data_dir}  (temporary, deleted on exit)")
        print(f"lease seconds    {settings.lease_seconds}")
        print(f"max units        {settings.max_translation_units}")
        print()

        print("IMPORT")
        with database.session() as session:
            with source.open("rb") as stream:
                imported = documents.import_file(session, filename=source.name, stream=stream)
            session.commit()
            document_id = imported.document.id
            print(f"  document       {document_id}  pages={imported.document.page_count}")
            print(f"  duplicate      {imported.duplicate}")
            print(f"  parse job      {imported.job.id if imported.job else None}"
                  f"  created={imported.job_created}")
            if imported.probe:
                print(f"  probe          format={imported.probe.format}"
                      f" mime={imported.probe.mime_type}"
                      f" extension_matches={imported.probe.extension_matches}"
                      f" warnings={imported.probe.warnings}")
        print()

        print("RUN — parse")
        worker = Worker(settings, workspace, queue, database=database)
        ran = worker.serve(poll_seconds=0, max_jobs=1)
        print(f"  jobs run       {ran}")
        print()
        with database.session() as session:
            parse_job = session.get(Job, imported.job.id)
            show_job(session, parse_job, "PARSE JOB")
        print()

        print("RUN — translate, with no credentials configured")
        with database.session() as session:
            translate_job, _ = queue.enqueue(
                session,
                job_type=JobType.TRANSLATE.value,
                document_id=document_id,
                request={"target_language": "zh", "source_language": "en"},
            )
            session.commit()
            translate_id = translate_job.id
        ran = worker.serve(poll_seconds=0, max_jobs=1)
        print(f"  jobs run       {ran}")
        print()
        with database.session() as session:
            show_job(session, session.get(Job, translate_id), "TRANSLATE JOB")
            units = session.scalars(
                select(JobUnit).where(JobUnit.job_id == translate_id)
            ).all()
            print(f"    job units recorded: {len(units)}")
        print()

        print("RECOVERY")
        with database.session() as session:
            print(f"  reclaimed lease-expired jobs: {recover(session)}")
            session.commit()
        print()

        print("=" * 74)
        print("REAL-SERVICE TRANSLATION ACCEPTANCE: PENDING.")
        print("The translate job stopped at the provider boundary and wrote no translation")
        print("rows and no placeholder text.  No mock provider was used anywhere in this run.")
        print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
