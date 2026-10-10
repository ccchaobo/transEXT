"""What a reader sees when a job fails.

Every test here exists because of something found by running the real server rather than by
reading the code.  They are grouped because they share one requirement from the spec: a failure
must be *visible and correctly described*, not silent and not mislabelled.  A document that
quietly keeps saying "pending" after its parse failed, or a format problem reported as a
translation problem, both make the reader look in the wrong place.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rr_api import create_app
from rr_contracts import ErrorCode, JobStatus, StageStatus
from rr_services import Settings
from rr_worker import Worker, _error_code_for

#: A real DOCX whose format passes verification but whose parse is not implemented.  Fabricated
#: bytes do not reach the parse stage at all - the format check refuses them at import - so a
#: fake file would skip the very path under test rather than exercise it.
UNPARSED_DOCX = Path("benchmarks/corpus/files/docx_paper_a.docx")


@pytest.fixture()
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, serve_frontend=False)
    with TestClient(app) as test_client:
        test_client.app = app
        yield test_client


@pytest.fixture()
def unparsed_document(client) -> str:
    if not UNPARSED_DOCX.is_file():
        pytest.skip(f"{UNPARSED_DOCX} is missing; run benchmarks/rr_m0/corpus_generator.py")
    with UNPARSED_DOCX.open("rb") as stream:
        response = client.post(
            "/api/v1/documents",
            files={"file": (UNPARSED_DOCX.name, stream, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    assert response.status_code == 201, response.text
    return response.json()["document"]["id"]


def run_worker(client, *, max_jobs: int = 1) -> int:
    app = client.app
    worker = Worker(
        app.state.settings, app.state.workspace, app.state.queue, database=app.state.database
    )
    # ``exit_when_idle`` so an empty queue ends the loop instead of polling forever.  Without it
    # this helper hangs, which is how the CLI's ``--once`` was found to hang on an empty queue.
    return worker.serve(poll_seconds=1.0, max_jobs=max_jobs, exit_when_idle=True, idle_grace=0.0)


#: A real text PDF that parses into blocks.
PARSED_PDF = Path("benchmarks/corpus/files/pdf_b_two_column.pdf")


@pytest.fixture()
def parsed_document(client) -> str:
    if not PARSED_PDF.is_file():
        pytest.skip(f"{PARSED_PDF} is missing; run benchmarks/rr_m0/corpus_generator.py")
    with PARSED_PDF.open("rb") as stream:
        response = client.post(
            "/api/v1/documents", files={"file": (PARSED_PDF.name, stream, "application/pdf")}
        )
    assert response.status_code == 201, response.text
    document_id = response.json()["document"]["id"]
    assert run_worker(client) == 1
    return document_id


class TestAFailedParseIsVisible:
    def test_the_document_says_its_parse_failed_and_can_still_be_read(
        self, client, unparsed_document
    ) -> None:
        """A parse that fails must leave a readable document, not a 500.

        The detail endpoint raised ``AttributeError: 'NoneType' object has no attribute 'issues'``
        for any document with no parse revision, so a failed import returned 500 - the one case
        where the reader most needs the explanation.  The cause was a comprehension whose filter
        was meant to guard the iterable; the filter runs after the iterable has been built.
        """
        assert run_worker(client) == 1

        detail = client.get(f"/api/v1/documents/{unparsed_document}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        # Not "pending".  Only a successful parse used to write this field, so a document whose
        # parse had already failed kept claiming it was still waiting for one.
        assert body["parse_status"] == StageStatus.FAILED.value
        assert body["capabilities"]["readable"] is False
        assert body["capabilities"]["translatable"] is False
        assert body["block_count"] == 0
        assert body["issues"] == []

    def test_an_unsupported_format_is_not_reported_as_a_translation_problem(
        self, client, unparsed_document
    ) -> None:
        """The error code has to name the right subsystem.

        A DOCX whose parse is not implemented was recorded as ``translation_failed``: the worker
        used the translation service's error mapper for every job type, and its fallback is a
        translation code.  A reader seeing that would go looking at their API key.
        """
        run_worker(client)

        jobs = client.get(f"/api/v1/jobs?document_id={unparsed_document}").json()["jobs"]
        job = jobs[0]
        assert job["status"] == JobStatus.FAILED.value
        assert job["error_code"] == ErrorCode.UNSUPPORTED_FORMAT.value
        assert job["error_code"] != ErrorCode.TRANSLATION_FAILED.value
        # And the message must still explain, since the code is a vocabulary word and not prose.
        assert "not implemented" in job["error_message"]
        assert "docx" in job["error_message"]


class TestTheMostSpecificRefusalWins:
    """When two things are wrong, report the one the caller can act on.

    These were both found by talking to the running server.  Each is about the *order* of the
    checks rather than the checks themselves, which is why no test caught them: every code path
    was individually correct.
    """

    def test_a_missing_document_is_not_reported_as_a_missing_api_key(self, client) -> None:
        response = client.post(
            "/api/v1/documents/doc_does_not_exist/translate",
            json={"scope": "document", "target_language": "zh"},
        )
        assert response.status_code == 404, response.text
        assert response.json()["error_code"] == ErrorCode.NOT_FOUND.value
        assert "translation_not_configured" not in response.text

    def test_an_existing_document_without_a_provider_says_the_provider_is_missing(
        self, client, parsed_document
    ) -> None:
        response = client.post(
            f"/api/v1/documents/{parsed_document}/translate",
            json={"scope": "document", "target_language": "zh"},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error_code"] == (
            ErrorCode.TRANSLATION_NOT_CONFIGURED.value
        )
        assert "mock" in response.json()["detail"]["message"]


class TestTheErrorCodeNamesTheDomain:
    @pytest.mark.parametrize(
        ("job_type", "expected"),
        [
            ("parse", ErrorCode.PARSE_FAILED.value),
            ("ocr", ErrorCode.OCR_FAILED.value),
            ("translate", ErrorCode.TRANSLATION_FAILED.value),
            ("export", ErrorCode.CONVERSION_FAILED.value),
            ("convert", ErrorCode.CONVERSION_FAILED.value),
        ],
    )
    def test_an_unclassified_failure_uses_the_job_domains_code(self, job_type, expected) -> None:
        assert _error_code_for(ValueError("something broke"), job_type) == expected

    @pytest.mark.parametrize("job_type", ["parse", "translate", "ocr", "export"])
    def test_an_unimplemented_format_says_so_whatever_the_job(self, job_type) -> None:
        assert (
            _error_code_for(NotImplementedError("not yet"), job_type)
            == ErrorCode.UNSUPPORTED_FORMAT.value
        )

    def test_a_classified_error_keeps_its_specific_code(self) -> None:
        """Not-configured and over-budget are specific and must not be flattened."""
        from rr_services import TranslationNotConfigured

        assert (
            _error_code_for(TranslationNotConfigured("no key"), "translate")
            == ErrorCode.TRANSLATION_NOT_CONFIGURED.value
        )


class TestTheStageStatusVocabularyIsFaithful:
    def test_a_cancelled_job_is_not_recorded_as_a_failed_stage(self) -> None:
        from rr_worker import _STAGE_STATUS

        assert _STAGE_STATUS[JobStatus.CANCELLED.value] == StageStatus.CANCELLED.value
        assert _STAGE_STATUS[JobStatus.PARTIALLY_SUCCEEDED.value] == (
            StageStatus.PARTIALLY_SUCCEEDED.value
        )
        assert _STAGE_STATUS[JobStatus.QUEUED.value] == StageStatus.PENDING.value

    def test_every_job_status_the_worker_produces_has_a_stage_status(self) -> None:
        """A missing mapping would write a raw job status into a document field."""
        from rr_worker import _STAGE_STATUS

        for status in JobStatus:
            assert status.value in _STAGE_STATUS, status
        for mapped in _STAGE_STATUS.values():
            assert mapped in {member.value for member in StageStatus}
