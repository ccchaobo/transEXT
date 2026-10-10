"""The worker: leasing, fencing, cancellation and the partial status.

These run against the real corpus file and a real database, because most of what the worker has
to get right is about *ordering* — claim, beat, fence, finish — and none of that is visible in a
stubbed queue.  The translation provider is a stub and is labelled as such: nothing here is
evidence about translation quality, and none of it lets the project claim the real-service
acceptance item, which stays PENDING.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from rr_adapters.translation import TranslationOutcome, TranslationResult
from rr_contracts import ErrorCode, JobStatus, JobType
from rr_services import DocumentService, Settings
from rr_services.translation import TranslationService
from rr_storage import (
    Attempt,
    AttemptSuperseded,
    Job,
    JobQueue,
    JobUnit,
    Workspace,
    create_database,
)
from rr_worker import StageOutcome, Worker

CORPUS = Path("benchmarks/corpus")
DOCUMENT = "pdf_b_two_column"


class StubProvider:
    name = "stub"
    model = "stub-model-v1"

    def __init__(self, *, unknown_for: set[str] | None = None) -> None:
        self.unknown_for = unknown_for or set()
        self.calls = 0

    def translate(self, units, *, glossary=(), source_language="en", target_language="zh", options=None):
        self.calls += 1
        results = []
        for unit in units:
            if unit.block_id in self.unknown_for:
                results.append(
                    TranslationResult(
                        block_id=unit.block_id,
                        status="unknown",
                        error_code=ErrorCode.EXTERNAL_UNKNOWN_RESULT.value,
                    )
                )
            else:
                results.append(
                    TranslationResult(
                        block_id=unit.block_id,
                        text=f"[{target_language}] {unit.text}",
                        status="ok",
                        usage={"total_tokens": 5},
                        model=self.model,
                    )
                )
        return TranslationOutcome(
            results=results, provider=self.name, model=self.model, usage={"requests": len(units)}
        )


@pytest.fixture()
def harness(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    database = create_database(settings.data_dir)
    database.create_all()
    workspace = Workspace(settings.workspace_root)
    queue = JobQueue(lease_seconds=120)
    service = DocumentService(settings, workspace, queue)
    return settings, database, workspace, queue, service


def import_document(harness, name: str = DOCUMENT):
    """Import a real corpus file and return its document id.

    ``queue_parse=False`` on purpose: importing normally enqueues a parse job as well, and then
    ``claim_next`` hands that job to the worker instead of the one a test meant to run.  Each
    test enqueues exactly the job it is about, so the job set is never ambiguous.
    """
    settings, database, workspace, queue, service = harness
    source = CORPUS / "files" / f"{name}.pdf"
    with database.session() as session:
        with source.open("rb") as stream:
            result = service.import_file(
                session, filename=source.name, stream=stream, queue_parse=False
            )
        session.commit()
        return result.document.id


def enqueue(harness, job_type: str, document_id: str, **kwargs) -> str:
    """Enqueue exactly one job and return its id."""
    _, database, _, queue, _ = harness
    with database.session() as session:
        job, _created = queue.enqueue(
            session, job_type=job_type, document_id=document_id, **kwargs
        )
        session.commit()
        return job.id


def run_job(harness, provider=None, *, settings=None, **worker_kwargs):
    """Run a single job and return the WorkResult."""
    base_settings, database, workspace, queue, _ = harness
    worker = Worker(
        settings or base_settings,
        workspace,
        queue,
        database=database,
        provider=provider,
        **worker_kwargs,
    )
    with database.session() as session:
        return worker.run_once(session)


def job_row(harness, job_type: str) -> Job:
    _, database, _, _, _ = harness
    with database.session() as session:
        return session.scalars(
            select(Job).where(Job.type == job_type).order_by(Job.created_at.desc())
        ).first()


class TestParseStage:
    def test_a_parse_job_parses_the_document(self, harness) -> None:
        document_id = import_document(harness)
        enqueue(harness, JobType.PARSE.value, document_id)
        result = run_job(harness)
        assert result is not None
        assert result.job_type == JobType.PARSE.value
        assert result.status == JobStatus.SUCCEEDED.value
        assert result.detail["blocks"] > 0
        assert result.detail["translatable_blocks"] > 0
        assert result.detail["pages"] == 2

    def test_the_document_records_its_active_revision(self, harness) -> None:
        document_id = import_document(harness)
        enqueue(harness, JobType.PARSE.value, document_id)
        run_job(harness)
        _, database, _, _, _ = harness
        with database.session() as session:
            from rr_storage import Document

            document = session.scalars(select(Document)).one()
            assert document.parse_status == "succeeded"
            assert document.active_revision_id


class TestUnsupportedWork:
    def test_an_unregistered_job_type_fails_rather_than_doing_nothing(self, harness) -> None:
        document_id = import_document(harness)
        enqueue(harness, JobType.EXPORT.value, document_id)
        result = run_job(harness)
        assert result.status == JobStatus.FAILED.value
        job = job_row(harness, JobType.EXPORT.value)
        assert job.error_code == ErrorCode.UNSUPPORTED_FORMAT.value
        assert "export" in (job.error_message or "")


class TestTranslateStage:
    def _parsed(self, harness):
        """Import the document and run its parse job, so translate tests start from real blocks."""
        document_id = import_document(harness)
        enqueue(harness, JobType.PARSE.value, document_id)
        run_job(harness)
        return document_id

    def test_a_translate_job_without_a_provider_fails_and_says_why(self, harness) -> None:
        document_id = self._parsed(harness)
        enqueue(harness, JobType.TRANSLATE.value, document_id)
        result = run_job(harness, provider=None)
        assert result.status == JobStatus.FAILED.value
        job = job_row(harness, JobType.TRANSLATE.value)
        assert job.error_code == ErrorCode.TRANSLATION_NOT_CONFIGURED.value
        assert "mock" in (job.error_message or "")

    def test_a_translate_job_translates_and_records_one_unit_per_block(self, harness) -> None:
        document_id = self._parsed(harness)
        enqueue(harness, JobType.TRANSLATE.value, document_id)
        provider = StubProvider()
        result = run_job(harness, provider=provider)
        assert result.status == JobStatus.SUCCEEDED.value
        assert result.detail["translated"] == result.detail["requested"] > 0
        assert result.detail["reused"] == 0

        job = job_row(harness, JobType.TRANSLATE.value)
        _, database, _, _, _ = harness
        with database.session() as session:
            units = list(session.scalars(select(JobUnit).where(JobUnit.job_id == job.id)))
        assert len(units) == result.detail["translated"]
        assert {u.status for u in units} == {"succeeded"}

    def test_a_second_translate_job_reuses_everything_and_sends_nothing(self, harness) -> None:
        document_id = self._parsed(harness)
        provider = StubProvider()

        enqueue(harness, JobType.TRANSLATE.value, document_id)
        first = run_job(harness, provider=provider)
        calls_after_first = provider.calls

        enqueue(harness, JobType.TRANSLATE.value, document_id)
        second = run_job(harness, provider=provider)

        assert second.detail["reused"] == first.detail["translated"]
        assert second.detail["requested"] == 0
        # The claim is that the second job adds no calls at all, which is what a cache hit
        # means.  Asserting an absolute call count here instead would be asserting the batch
        # size, which is a different thing and was wrong the first time this was written.
        assert provider.calls == calls_after_first

    def test_an_ambiguous_result_makes_the_job_partial_and_is_not_retried(self, harness) -> None:
        document_id = self._parsed(harness)
        settings, database, workspace, queue, _ = harness
        # Make one block ambiguous by asking the stub to report it as unknown.
        with database.session() as session:
            plan = TranslationService(settings, workspace, queue, provider=StubProvider()).plan(
                session, document_id
            )
            ambiguous = {plan.pending_units[0].block_id}

        enqueue(harness, JobType.TRANSLATE.value, document_id)
        provider = StubProvider(unknown_for=ambiguous)
        result = run_job(harness, provider=provider)

        assert result.status == JobStatus.PARTIALLY_SUCCEEDED.value
        assert result.detail["unknown"] == 1
        job = job_row(harness, JobType.TRANSLATE.value)
        assert job.error_code == ErrorCode.EXTERNAL_UNKNOWN_RESULT.value
        assert "unknown" in (job.error_message or "")
        with database.session() as session:
            unit = session.scalars(select(JobUnit).where(JobUnit.block_id.in_(ambiguous))).one()
        assert unit.status == "unknown"

    def test_an_oversized_plan_is_refused_before_any_request(self, harness) -> None:
        document_id = self._parsed(harness)
        settings = replace(harness[0], max_translation_units=2)
        enqueue(harness, JobType.TRANSLATE.value, document_id)
        provider = StubProvider()
        result = run_job(harness, provider=provider, settings=settings)
        assert result.status == JobStatus.FAILED.value
        assert provider.calls == 0, "the guard must fire before any request is sent"
        assert job_row(harness, JobType.TRANSLATE.value).error_code == ErrorCode.BUDGET_EXCEEDED.value

    def test_a_long_job_is_split_into_batches(self, harness) -> None:
        # Batching is what makes cancellation and heartbeats possible on a long document, so it
        # is asserted rather than assumed: 4 units per batch over >4 blocks must give >1 call.
        document_id = self._parsed(harness)
        enqueue(
            harness,
            JobType.TRANSLATE.value,
            document_id,
            request={"batch_units": 4},
        )
        provider = StubProvider()
        result = run_job(harness, provider=provider)
        assert result.status == JobStatus.SUCCEEDED.value
        assert result.detail["translated"] > 4
        assert provider.calls == -(-result.detail["translated"] // 4)


class TestCancellation:
    def test_a_job_cancelled_before_it_starts_is_never_claimed(self, harness) -> None:
        # The queue cancels a *queued* job outright rather than leaving it for a worker to
        # notice, so there is nothing to claim.  What matters is that it does not sit in
        # `queued` forever — that would leave the document looking busy with no work running.
        document_id = import_document(harness)
        job_id = enqueue(harness, JobType.PARSE.value, document_id)
        _, database, _, queue, _ = harness
        with database.session() as session:
            queue.request_cancel(session, job_id)
            session.commit()

        assert run_job(harness) is None

        job = job_row(harness, JobType.PARSE.value)
        assert job.status == JobStatus.CANCELLED.value
        assert job.cancel_requested is True
        # A cancellation is not a failure, and must not be recorded as one.
        assert job.stage_status == "cancelled"
        # Nothing was parsed: a cancelled job must not leave a half-written revision.
        assert job.result in (None, {})

    def test_a_job_cancelled_while_running_stops_at_the_next_boundary(self, harness) -> None:
        # A running job cannot be cancelled outright — its work is in flight — so the worker
        # discovers the request when it next beats, and stops at a unit boundary.
        document_id = import_document(harness)
        job_id = enqueue(harness, JobType.PARSE.value, document_id)
        _, database, workspace, queue, _ = harness
        worker = Worker(harness[0], workspace, queue, database=database)
        with database.session() as session:
            lease = queue.claim_next(session)
            session.commit()
        with database.session() as session:
            queue.request_cancel(session, job_id)
            session.commit()
            assert worker.beat(session, lease, "extract") is False


class TestFencing:
    def test_a_superseded_attempt_refuses_to_finish(self, harness) -> None:
        # A worker that stalled, was taken over, and then woke up must not overwrite the new
        # attempt's result.  This is the behaviour the whole lease design exists for.
        document_id = import_document(harness)
        enqueue(harness, JobType.PARSE.value, document_id)
        _, database, _, queue, _ = harness
        with database.session() as session:
            stale = queue.claim_next(session)
            session.commit()
        # Another attempt takes the job: the old lease is no longer current.
        with database.session() as session:
            job = session.get(Job, stale.job_id)
            job.current_attempt_id = "att_someone_else"
            session.commit()

        with database.session() as session:
            with pytest.raises(AttemptSuperseded):
                queue.finish(session, stale, status=JobStatus.SUCCEEDED.value)

    def test_beating_reports_cancellation(self, harness) -> None:
        document_id = import_document(harness)
        job_id = enqueue(harness, JobType.PARSE.value, document_id)
        _, database, workspace, queue, _ = harness
        settings = harness[0]
        worker = Worker(settings, workspace, queue, database=database)
        with database.session() as session:
            lease = queue.claim_next(session)
            assert worker.beat(session, lease, "extract") is True
            queue.request_cancel(session, lease.job_id)
            session.commit()
            assert worker.beat(session, lease, "extract") is False
        assert job_id == lease.job_id

    def test_a_stalled_worker_cannot_write_after_being_taken_over(self, harness) -> None:
        # The same rule at the unit level: a unit write is fenced, not just the finish.
        document_id = import_document(harness)
        enqueue(harness, JobType.PARSE.value, document_id)
        _, database, workspace, queue, _ = harness
        worker = Worker(harness[0], workspace, queue, database=database)
        with database.session() as session:
            lease = queue.claim_next(session)
            session.commit()
        with database.session() as session:
            job = session.get(Job, lease.job_id)
            job.current_attempt_id = "att_someone_else"
            session.commit()
        with database.session() as session:
            with pytest.raises(AttemptSuperseded):
                worker.queue.assert_lease(session, lease)


class TestIdempotency:
    def test_the_same_key_does_not_create_a_second_job(self, harness) -> None:
        document_id = import_document(harness)
        _, database, _, queue, _ = harness
        with database.session() as session:
            first, created_first = queue.enqueue(
                session,
                job_type=JobType.PARSE.value,
                document_id=document_id,
                idempotency_key="import:abc",
            )
            session.commit()
        with database.session() as session:
            second, created_second = queue.enqueue(
                session,
                job_type=JobType.PARSE.value,
                document_id=document_id,
                idempotency_key="import:abc",
            )
            session.commit()
        assert created_first is True
        assert created_second is False
        assert first.id == second.id


class TestServe:
    def test_max_jobs_bounds_the_loop(self, harness) -> None:
        document_id = import_document(harness)
        enqueue(harness, JobType.PARSE.value, document_id)
        settings, database, workspace, queue, _ = harness
        worker = Worker(settings, workspace, queue, database=database)
        assert worker.serve(poll_seconds=0, max_jobs=1) == 1

    def test_an_empty_queue_returns_zero_without_spinning(self, harness) -> None:
        settings, database, workspace, queue, _ = harness
        worker = Worker(settings, workspace, queue, database=database)
        # `poll_seconds=0` means "run what is available", so an empty queue must end the loop.
        # Before this was handled, the loop skipped the sleep and spun on the empty queue
        # forever, which is how this test hung rather than failed.
        assert worker.serve(poll_seconds=0, max_jobs=3) == 0

    def test_a_stop_callback_ends_the_loop(self, harness) -> None:
        settings, database, workspace, queue, _ = harness
        worker = Worker(settings, workspace, queue, database=database)
        assert worker.serve(poll_seconds=0, stop=lambda: True) == 0


class TestStageOutcomes:
    def test_partial_carries_an_error_code(self) -> None:
        outcome = StageOutcome.partial({"a": 1}, "some ambiguity")
        assert outcome.status == JobStatus.PARTIALLY_SUCCEEDED.value
        assert outcome.error_code == ErrorCode.EXTERNAL_UNKNOWN_RESULT.value
        assert outcome.error_message == "some ambiguity"

    def test_cancelled_is_distinct_from_failed(self) -> None:
        assert StageOutcome.cancelled().status == JobStatus.CANCELLED.value
        assert StageOutcome.cancelled().status != JobStatus.FAILED.value
