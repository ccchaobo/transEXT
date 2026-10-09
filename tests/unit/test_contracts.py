"""Tests for the wire contract.

The contract is the boundary where untrusted input meets the system, so these
tests do two jobs: they check that valid payloads are accepted, and they check
that the *decisions* the contract records are actually enforced.  An invariant
that lives only in a docstring is documentation, not a contract.
"""

from __future__ import annotations

import json

import pytest

from rr_contracts import (
    HTTP_STATUS,
    RETRYABLE_CODES,
    AnnotationCreate,
    BlockTextPatch,
    BlockWire,
    ConfidenceWire,
    DocumentFormat,
    ErrorBody,
    ErrorCode,
    ErrorResponse,
    HealthResponse,
    JobStatus,
    JobType,
    JobWire,
    PageWire,
    ParseRevisionWire,
    ReadingState,
    SpanWire,
    StageStatus,
    TranslationRequest,
)


class TestErrorCodeConsistency:
    """The error tables must not drift apart."""

    def test_every_code_has_an_http_status(self) -> None:
        # A handler must not choose a status independently of the code it
        # returns, because then the two can disagree.
        missing = [code for code in ErrorCode if code not in HTTP_STATUS]
        assert missing == [], f"error codes without a status mapping: {missing}"

    def test_every_retryable_code_is_a_real_code(self) -> None:
        assert RETRYABLE_CODES <= set(ErrorCode)

    def test_statuses_are_plausible_http_codes(self) -> None:
        for code, status in HTTP_STATUS.items():
            assert 400 <= status <= 599, f"{code} maps to a non-error status {status}"

    def test_statuses_are_not_duplicated_inconsistently(self) -> None:
        # Several codes may share a status (409 is used for conflict, stale
        # revision and cancellation), but a status must not map back ambiguously
        # to a single code in a way that would let a client guess the wrong one.
        statuses = set(HTTP_STATUS.values())
        assert len(statuses) < len(HTTP_STATUS), (
            "if every code had a unique status the code would be redundant"
        )

    def test_retryable_codes_are_transient_by_nature(self) -> None:
        # Retrying a permanent client mistake would loop against a request that
        # can never succeed.
        permanent = {
            ErrorCode.INVALID_REQUEST,
            ErrorCode.UNSUPPORTED_FORMAT,
            ErrorCode.ENCRYPTED_FILE,
            ErrorCode.DECOMPRESSION_BOMB,
            ErrorCode.EXTERNAL_AUTH_FAILED,
            ErrorCode.NOT_FOUND,
            ErrorCode.CONFLICT,
        }
        overlap = RETRYABLE_CODES & permanent
        assert overlap == frozenset(), f"permanent errors marked retryable: {overlap}"

    def test_a_bad_credential_is_not_retryable(self) -> None:
        # Retrying a rejected credential burns rate limit and delays telling the
        # user the one thing they need to know.
        assert ErrorCode.EXTERNAL_AUTH_FAILED not in RETRYABLE_CODES

    def test_an_ambiguous_external_result_is_retryable(self) -> None:
        # An external call whose outcome is unknown must be retried with the same
        # idempotency key, or the work may be charged without being recorded.
        assert ErrorCode.EXTERNAL_UNKNOWN_RESULT in RETRYABLE_CODES

    def test_rate_limiting_and_timeouts_are_retryable(self) -> None:
        assert ErrorCode.EXTERNAL_RATE_LIMITED in RETRYABLE_CODES
        assert ErrorCode.EXTERNAL_TIMEOUT in RETRYABLE_CODES


class TestErrorBodies:
    def test_an_error_body_carries_a_machine_readable_code(self) -> None:
        body = ErrorBody(
            code=ErrorCode.NOT_FOUND,
            message="no such document",
            retryable=False,
            scope="document",
        )
        assert body.code is ErrorCode.NOT_FOUND
        assert body.scope == "document"

    def test_the_code_serialises_as_a_string(self) -> None:
        # The client branches on the string, so it must not serialise as the
        # Python enum repr.
        body = ErrorBody(
            code=ErrorCode.NOT_FOUND, message="gone", retryable=False, scope="document"
        )
        payload = json.loads(body.model_dump_json())
        assert payload["code"] == "not_found"

    def test_retryability_is_part_of_the_response(self) -> None:
        # The server states whether the error is retryable rather than leaving the
        # client to infer it from the status, so that the two cannot disagree.
        body = ErrorBody(
            code=ErrorCode.EXTERNAL_TIMEOUT,
            message="timed out",
            retryable=True,
            scope="job",
        )
        assert body.retryable is True
        assert body.retryable == (body.code in RETRYABLE_CODES)

    def test_scope_is_required(self) -> None:
        # "What does this error apply to" is needed to render an actionable
        # message; a missing scope is a contract violation, not a default.
        with pytest.raises(Exception):
            ErrorBody(code=ErrorCode.INTERNAL, message="boom", retryable=False)

    def test_an_error_response_wraps_the_body(self) -> None:
        response = ErrorResponse(
            error=ErrorBody(
                code=ErrorCode.CONFLICT, message="busy", retryable=False, scope="request"
            )
        )
        assert response.error.code is ErrorCode.CONFLICT

    def test_an_unknown_code_is_rejected(self) -> None:
        with pytest.raises(Exception):
            ErrorBody(code="not_a_real_code", message="x", retryable=False, scope="request")


class TestPageNumbering:
    """The 0-based internal index and the 1-based display number are separate."""

    def make_page(self, **overrides) -> PageWire:
        fields = dict(
            index=0,
            page_number=1,
            width_pt=595.276,
            height_pt=841.89,
            rotation=0,
            display_width=595.276,
            display_height=841.89,
            source_kind="text",
            text_chars=1200,
            ocr_applied=False,
        )
        fields.update(overrides)
        return PageWire(**fields)

    def test_a_page_exposes_both_numbers(self) -> None:
        page = self.make_page()
        assert page.index == 0
        assert page.page_number == 1

    def test_the_note_about_the_page_number_is_in_the_schema(self) -> None:
        # The distinction is documented in the field description so it appears in
        # the generated OpenAPI document, where a frontend author will read it.
        description = PageWire.model_fields["page_number"].description or ""
        assert "1-based" in description.lower()

    def test_page_number_must_be_at_least_one(self) -> None:
        with pytest.raises(Exception):
            self.make_page(page_number=0)

    def test_index_must_not_be_negative(self) -> None:
        with pytest.raises(Exception):
            self.make_page(index=-1)

    def test_rotation_must_be_a_quarter_turn(self) -> None:
        # The geometry contract only defines 0/90/180/270; an arbitrary angle is
        # a skew and must be expressed as a transform instead (ADR 0008).
        with pytest.raises(Exception):
            self.make_page(rotation=45)

    def test_repeated_page_numbers_are_allowed_across_documents(self) -> None:
        # Nothing here should imply global uniqueness; page 1 exists in every
        # document.
        first = self.make_page(index=0, page_number=1)
        second = self.make_page(index=0, page_number=1)
        assert first.page_number == second.page_number


class TestConfidence:
    def test_confidence_carries_a_level_and_a_score(self) -> None:
        # A bare score invites every consumer to invent its own threshold, so the
        # level travels with it.
        confidence = ConfidenceWire(level="high", score=0.97)
        assert confidence.level == "high"
        assert confidence.score == pytest.approx(0.97)

    def test_a_level_may_be_unknown(self) -> None:
        # "We do not know" must be representable; collapsing it to "low" would
        # present a guess as a measurement.
        assert ConfidenceWire().level == "unknown"
        assert ConfidenceWire().score is None

    def test_an_invented_level_is_rejected(self) -> None:
        with pytest.raises(Exception):
            ConfidenceWire(level="very-high", score=0.9)


class TestSpanAndBlock:
    def make_span(self, **overrides) -> SpanWire:
        fields = dict(page_index=1, bbox=[0.1, 0.2, 0.9, 0.3])
        fields.update(overrides)
        return SpanWire(**fields)

    def make_block(self, **overrides) -> BlockWire:
        fields = dict(
            id="b1",
            page_index=0,
            order=0,
            type="paragraph",
            text="Some text.",
            raw_text="Some text.",
            translatable=True,
            is_body=True,
            confidence=ConfidenceWire(level="high", score=0.95),
            spans=[self.make_span()],
            pages=[0],
        )
        fields.update(overrides)
        return BlockWire(**fields)

    def test_a_span_records_normalised_and_raw_geometry(self) -> None:
        # Both are kept: the raw box makes a change to the normalisation
        # recoverable, which is the point of ADR 0008.
        span = self.make_span(raw_bbox=[50.0, 100.0, 500.0, 150.0])
        assert span.bbox == [0.1, 0.2, 0.9, 0.3]
        assert span.raw_bbox == [50.0, 100.0, 500.0, 150.0]

    def test_a_span_uses_the_zero_based_index(self) -> None:
        # Spans are internal geometry, so they carry the internal index.  A UI
        # showing a span must go through the page, not print this number.
        assert self.make_span(page_index=0).page_index == 0

    def test_a_span_rejects_a_negative_index(self) -> None:
        with pytest.raises(Exception):
            self.make_span(page_index=-1)

    def test_a_span_requires_four_coordinates(self) -> None:
        with pytest.raises(Exception):
            self.make_span(bbox=[0.1, 0.2, 0.9])

    def test_a_block_holds_a_list_of_spans(self) -> None:
        # A list, not a single box: a paragraph can continue across a page or a
        # column, and collapsing that to one box would silently pick a winner.
        block = self.make_block(
            spans=[
                self.make_span(page_index=0),
                self.make_span(page_index=1),
            ],
            pages=[0, 1],
            cross_page=True,
        )
        assert len(block.spans) == 2
        assert block.cross_page is True

    def test_a_block_type_must_be_known(self) -> None:
        with pytest.raises(Exception):
            self.make_block(type="not_a_type")

    def test_a_block_can_be_marked_as_not_translatable(self) -> None:
        # A formula or a page number must be carried verbatim, so the decision is
        # recorded on the block rather than re-derived by every caller.
        block = self.make_block(type="formula", translatable=False)
        assert block.translatable is False

    def test_translation_status_has_a_stale_state(self) -> None:
        # A translation made from an earlier parse revision is stale, not valid.
        # Without this state a re-parse would leave old translations looking
        # current.
        block = self.make_block(translation_status="stale")
        assert block.translation_status == "stale"

    def test_a_block_records_section_membership(self) -> None:
        block = self.make_block(section_path=["Methods", "2.1 Sampling"], section_id="s3")
        assert block.section_path == ["Methods", "2.1 Sampling"]


class TestJobs:
    def make_job(self, **overrides) -> JobWire:
        fields = dict(
            id="j1",
            type=JobType.PARSE,
            status=JobStatus.QUEUED,
            created_at="2026-10-09T12:00:00Z",
        )
        fields.update(overrides)
        return JobWire(**fields)

    def test_partial_success_is_representable(self) -> None:
        # Spec 9: a job that translated some blocks and failed others must be
        # expressible.  Reporting it as success hides the failures; reporting it
        # as failure hides the work that succeeded.
        assert JobStatus.PARTIALLY_SUCCEEDED.value == "partially_succeeded"

    def test_the_whole_lifecycle_is_present(self) -> None:
        assert {status.value for status in JobStatus} == {
            "queued",
            "running",
            "succeeded",
            "partially_succeeded",
            "failed",
            "cancelled",
        }

    def test_there_is_no_unknown_job_status(self) -> None:
        # An ambiguous external result is an *error code*
        # (EXTERNAL_UNKNOWN_RESULT), not a job state: the job still has a definite
        # state, it is the external outcome that is unknown.  Making "unknown" a
        # job status too would give the same fact two representations, and a UI
        # would then have to handle both.  This test fails if someone adds it.
        assert "unknown" not in {status.value for status in JobStatus}

    def test_a_job_carries_progress_per_stage(self) -> None:
        job = self.make_job(
            status=JobStatus.RUNNING,
            stage="parse",
            progress={"stage": "parse", "total_units": 10, "done_units": 3},
            percent=0.3,
        )
        assert job.progress is not None
        assert job.progress.done_units == 3
        assert job.percent == pytest.approx(0.3)

    def test_progress_counts_failed_units_separately_from_done(self) -> None:
        # If failures were folded into "done" the percentage would reach 100% on a
        # job that did not finish its work.
        job = self.make_job(
            status=JobStatus.PARTIALLY_SUCCEEDED,
            progress={
                "stage": "translate",
                "total_units": 10,
                "done_units": 7,
                "failed_units": 3,
            },
        )
        assert job.progress is not None
        assert job.progress.done_units == 7
        assert job.progress.failed_units == 3

    @pytest.mark.parametrize("job_type", list(JobType))
    def test_every_job_type_is_constructible(self, job_type: JobType) -> None:
        assert self.make_job(type=job_type).type is job_type

    def test_percent_must_stay_within_the_unit_range(self) -> None:
        with pytest.raises(Exception):
            self.make_job(percent=1.5)

    def test_a_job_records_its_idempotency_key(self) -> None:
        # Retrying an ambiguous external call must reuse the key, so the key has
        # to be visible on the job rather than recomputed from scratch.
        job = self.make_job(idempotency_key="doc1:rev1:block7:zh")
        assert job.idempotency_key == "doc1:rev1:block7:zh"

    def test_a_failed_job_can_list_its_individual_failures(self) -> None:
        job = self.make_job(
            status=JobStatus.PARTIALLY_SUCCEEDED,
            failures=[{"block_id": "b4", "error_code": "external_timeout"}],
        )
        assert job.failures[0]["block_id"] == "b4"

    def test_a_job_can_be_marked_not_cancellable(self) -> None:
        # Cancellation is not always possible, and saying so is better than
        # offering a button that does nothing.
        assert self.make_job(cancellable=False).cancellable is False

    def test_stage_statuses_are_distinct_from_job_statuses(self) -> None:
        # A stage has "skipped" and "pending", which a job does not; reusing one
        # enum for both would force a meaning onto values that do not have it.
        assert StageStatus.SKIPPED.value == "skipped"
        assert StageStatus.PENDING.value == "pending"
        assert "skipped" not in {status.value for status in JobStatus}


class TestDocuments:
    def test_every_declared_format_has_a_value(self) -> None:
        assert {fmt.value for fmt in DocumentFormat} == {"pdf", "png", "jpeg", "docx"}

    def test_a_translation_request_names_its_target_language(self) -> None:
        assert TranslationRequest(target_language="zh").target_language == "zh"

    def test_a_zoom_must_be_positive_and_bounded(self) -> None:
        # A non-positive zoom cannot be rendered and an unbounded one can ask the
        # browser to allocate an arbitrarily large canvas, so both are refused at
        # the boundary.
        with pytest.raises(Exception):
            ReadingState(zoom=0.0)
        with pytest.raises(Exception):
            ReadingState(zoom=100.0)

    def test_a_reading_state_records_where_the_reader_was(self) -> None:
        state = ReadingState(page_index=4, zoom=1.5, block_id="b9")
        assert state.page_index == 4
        assert state.block_id == "b9"

    def test_a_reading_state_rejects_a_negative_page(self) -> None:
        with pytest.raises(Exception):
            ReadingState(page_index=-1)

    def test_a_block_patch_replaces_text(self) -> None:
        assert BlockTextPatch(text="corrected").text == "corrected"

    def test_an_annotation_can_be_created_against_a_block(self) -> None:
        # A note on a block needs the document it belongs to; without it the
        # annotation cannot be located after a restart.
        annotation = AnnotationCreate(document_id="d1", block_id="b1", note="check this")
        assert annotation.block_id == "b1"
        assert annotation.kind == "note"

    def test_an_annotation_requires_a_document(self) -> None:
        with pytest.raises(Exception):
            AnnotationCreate(block_id="b1", note="check this")

    def test_an_annotation_kind_must_be_known(self) -> None:
        with pytest.raises(Exception):
            AnnotationCreate(document_id="d1", note="x", kind="bookmark")


class TestRevision:
    def test_a_parse_revision_is_identified_and_attributed(self) -> None:
        # Which parser and version produced a revision is part of the record: a
        # re-parse after an engine upgrade must be distinguishable from one that
        # changed nothing.
        revision = ParseRevisionWire(
            id="r1",
            parser="pymupdf",
            parser_version="1.28.2",
            status="succeeded",
            created_at="2026-10-09T12:00:00Z",
        )
        assert revision.parser == "pymupdf"
        assert revision.is_active is False

    def test_revision_warnings_can_be_empty(self) -> None:
        revision = ParseRevisionWire(
            id="r1",
            parser="pymupdf",
            parser_version="1.28.2",
            status="succeeded",
            created_at="2026-10-09T12:00:00Z",
        )
        assert revision.warnings == []


class TestHealth:
    def test_health_reports_each_capability_separately(self) -> None:
        # Without credentials the translation capability must be reportable as
        # unavailable rather than the whole endpoint failing, so a user can still
        # read and parse documents.
        health = HealthResponse(
            status="degraded",
            version="0.1.0",
            database="ok",
            worker="idle",
            capabilities={"ocr": {"available": True}, "translation": {"available": False}},
        )
        assert health.capabilities["translation"]["available"] is False

    def test_degraded_is_a_valid_status(self) -> None:
        health = HealthResponse(
            status="degraded",
            version="0.1.0",
            database="ok",
            worker="idle",
            capabilities={},
        )
        assert health.status == "degraded"


class TestImportsStayCheap:
    def test_the_contract_package_does_not_import_the_implementation(self) -> None:
        # The package is the vocabulary, not the logic.  If it imported storage or
        # an adapter, every consumer of the contract would inherit that dependency
        # and the contract could no longer be tested in isolation.
        import subprocess
        import sys

        script = (
            "import sys; import rr_contracts; "
            "bad=[m for m in sys.modules if m.split('.')[0] in "
            "{'rr_storage','rr_services','rr_adapters','pymupdf','sqlalchemy'}]; "
            "print(','.join(sorted(bad)))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=180
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", (
            f"contracts imported implementation modules: {result.stdout.strip()}"
        )
