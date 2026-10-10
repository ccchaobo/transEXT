"""Translation orchestration: what is sent, what is reused, and what is refused.

The provider here is a stub, and that is the point of the file: these tests are about the
orchestration — cache identity, glossary versioning, the spend guard, and how an unknown
result is recorded — none of which a real service is needed to exercise.  **No test in this
file is evidence about translation quality, and none of them lets the project claim the
real-service acceptance item.**  That item stays PENDING until a real key exists
(limitations.md 2).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from document_core.models import BlockType
from rr_adapters.translation import (
    TranslationOutcome,
    TranslationResult,
    TranslationUnit,
)
from rr_contracts import ErrorCode
from rr_services.translation import (
    GLOSSARY_VERSION_NONE,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_UNKNOWN,
    TranslationBudgetExceeded,
    TranslationNotConfigured,
    TranslationService,
    cache_key,
    error_code_for,
)
from rr_storage import (
    Block,
    Document,
    ParseRevision,
    Term,
    Translation,
    create_database,
    utcnow,
)


class StubProvider:
    """A provider that answers deterministically and can be told to misbehave."""

    name = "stub"
    model = "stub-model-v1"

    def __init__(self, *, script: dict[str, TranslationResult] | None = None) -> None:
        self.script = script or {}
        self.calls: list[list[TranslationUnit]] = []
        self.glossaries: list[list[tuple[str, str]]] = []

    def translate(self, units, *, glossary=(), source_language="en", target_language="zh", options=None):
        self.calls.append(list(units))
        self.glossaries.append(list(glossary))
        results = []
        for unit in units:
            if unit.block_id in self.script:
                results.append(self.script[unit.block_id])
            else:
                results.append(
                    TranslationResult(
                        block_id=unit.block_id,
                        text=f"[{target_language}] {unit.text}",
                        status="ok",
                        usage={"total_tokens": len(unit.text)},
                        model=self.model,
                    )
                )
        return TranslationOutcome(
            results=results,
            provider=self.name,
            model=self.model,
            usage={"requests": len(units)},
            started_at=1.0,
            finished_at=2.0,
        )


@pytest.fixture()
def db(tmp_path):
    database = create_database(tmp_path / "data")
    database.create_all()
    return database


@pytest.fixture()
def session(db):
    with db.session() as session:
        yield session


def make_document(session, *, blocks, document_id="doc_1", revision_id="rev_1"):
    session.add(
        Document(
            id=document_id,
            filename="paper.pdf",
            format="pdf",
            size_bytes=1,
            sha256="hash",
            source_path="x.pdf",
            active_revision_id=revision_id,
            page_count=1,
            title="t",
            parse_status="succeeded",
        )
    )
    session.flush()
    session.add(
        ParseRevision(
            id=revision_id,
            document_id=document_id,
            parser="test",
            parser_version="1",
            status="succeeded",
        )
    )
    session.flush()
    for index, (block_type, text) in enumerate(blocks):
        session.add(
            Block(
                id=f"blk_{index}",
                block_key=f"0:{index:05d}",
                document_id=document_id,
                revision_id=revision_id,
                page_index=0,
                order_index=index,
                type=block_type,
                raw_text=text,
                clean_text=text,
                text_hash=cache_key(
                    text_hash=text,
                    revision_id=revision_id,
                    source_language="en",
                    target_language="zh",
                    provider="p",
                    model="m",
                    prompt_version="v1",
                    glossary_version="g",
                )[:16],
            )
        )
    session.flush()


class Settings:
    max_translation_units = 100


class TestPlanning:
    def test_only_translatable_blocks_are_planned(self, session) -> None:
        make_document(
            session,
            blocks=[
                (BlockType.PARAGRAPH.value, "Real prose to translate."),
                (BlockType.TITLE.value, "A title."),
                (BlockType.FORMULA.value, "S(x, y) = a + b"),
                (BlockType.REFERENCE.value, "[1] A. Author, 2019."),
                (BlockType.PAGE_NUMBER.value, "12"),
                (BlockType.HEADER.value, "Running head"),
            ],
        )
        plan = TranslationService(Settings(), provider=StubProvider()).plan(session, "doc_1")
        assert [u.block_type for u in plan.units] == ["paragraph", "title"]
        assert plan.skipped_non_translatable == 4

    def test_an_empty_block_is_not_sent(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "   ")])
        plan = TranslationService(Settings(), provider=StubProvider()).plan(session, "doc_1")
        assert plan.units == []

    def test_every_planned_unit_carries_the_section_context(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose.")])
        session.execute(
            Block.__table__.update().where(Block.id == "blk_0").values(section_path=["3. Method", "3.1 Data"])
        )
        session.flush()
        plan = TranslationService(Settings(), provider=StubProvider()).plan(session, "doc_1")
        assert plan.units[0].context == "3. Method > 3.1 Data"

    def test_a_context_already_inside_the_text_is_dropped(self, session) -> None:
        # Measured: the paper title is treated as a level-1 section, so every block's section
        # path was the title and the title block's context was the title itself.  Sending that
        # pays for the same string twice and tells the provider nothing.
        make_document(session, blocks=[(BlockType.TITLE.value, "Reading Order Recovery")])
        session.execute(
            Block.__table__.update().where(Block.id == "blk_0").values(section_path=["Reading Order Recovery"])
        )
        session.flush()
        plan = TranslationService(Settings(), provider=StubProvider()).plan(session, "doc_1")
        assert plan.units[0].context is None

    def test_no_section_path_means_no_context(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose.")])
        plan = TranslationService(Settings(), provider=StubProvider()).plan(session, "doc_1")
        assert plan.units[0].context is None


class TestCacheReuse:
    def test_the_second_run_sends_nothing(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose one.")])
        provider = StubProvider()
        service = TranslationService(Settings(), provider=provider)
        first = service.plan(session, "doc_1")
        service.execute(session, first)
        session.flush()

        second = service.plan(session, "doc_1")
        assert second.pending_units == []
        assert len(second.reusable_units) == 1

        run = service.execute(session, second)
        assert run.translated == 0
        assert run.reused == 1
        assert len(provider.calls) == 1  # no second request was made

    def test_a_changed_glossary_invalidates_the_cache(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose one.")])
        provider = StubProvider()
        service = TranslationService(Settings(), provider=provider)
        service.execute(session, service.plan(session, "doc_1"))
        session.flush()

        session.add(
            Term(
                id="term_1",
                source="reading order",
                target="阅读顺序",
                target_language="zh",
                scope="document",
                document_id="doc_1",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.flush()

        plan = service.plan(session, "doc_1")
        assert plan.glossary_version != GLOSSARY_VERSION_NONE
        assert plan.pending_units, "a glossary change must re-translate with the new terms"
        assert plan.glossary == [("reading order", "阅读顺序")]

    def test_bumping_a_term_version_without_changing_text_does_not_invalidate(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose one.")])
        session.add(
            Term(
                id="term_1",
                source="a",
                target="b",
                target_language="zh",
                scope="document",
                document_id="doc_1",
                version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.flush()
        service = TranslationService(Settings(), provider=StubProvider())
        service.execute(session, service.plan(session, "doc_1"))
        session.flush()

        term = session.get(Term, "term_1")
        term.version = 2
        session.flush()

        assert service.plan(session, "doc_1").pending_units == []

    def test_a_failed_translation_is_not_reused(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose one.")])
        provider = StubProvider(
            script={
                "blk_0": TranslationResult(
                    block_id="blk_0", status="failed", error_code=ErrorCode.TRANSLATION_FAILED.value
                )
            }
        )
        service = TranslationService(Settings(), provider=provider)
        service.execute(session, service.plan(session, "doc_1"))
        session.flush()
        assert service.plan(session, "doc_1").pending_units

    def test_the_cache_key_separates_every_input_that_changes_the_output(self) -> None:
        base = dict(
            text_hash="h",
            revision_id="r",
            source_language="en",
            target_language="zh",
            provider="p",
            model="m",
            prompt_version="v1",
            glossary_version="g",
            params={"temperature": 0.2},
        )
        baseline = cache_key(**base)
        for field_name, value in (
            ("text_hash", "h2"),
            ("revision_id", "r2"),
            ("target_language", "ja"),
            ("provider", "p2"),
            ("model", "m2"),
            ("prompt_version", "v2"),
            ("glossary_version", "g2"),
            ("params", {"temperature": 0.9}),
        ):
            assert cache_key(**{**base, field_name: value}) != baseline, field_name

    def test_params_order_does_not_change_the_key(self) -> None:
        common = dict(
            text_hash="h",
            revision_id="r",
            source_language="en",
            target_language="zh",
            provider="p",
            model="m",
            prompt_version="v1",
            glossary_version="g",
        )
        assert cache_key(**common, params={"a": 1, "b": 2}) == cache_key(
            **common, params={"b": 2, "a": 1}
        )


class TestTheSpendGuard:
    def test_an_oversized_plan_is_refused_before_anything_is_sent(self, session) -> None:
        make_document(
            session,
            blocks=[(BlockType.PARAGRAPH.value, f"Prose number {i}.") for i in range(5)],
        )
        provider = StubProvider()
        service = TranslationService(Settings(), provider=provider)
        plan = service.plan(session, "doc_1")
        with pytest.raises(TranslationBudgetExceeded) as info:
            service.execute(session, plan, max_units=3)
        assert info.value.requested == 5
        assert info.value.limit == 3
        assert provider.calls == [], "the guard must fire before any request"

    def test_no_provider_is_refused_before_anything_is_sent(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose.")])
        service = TranslationService(Settings(), provider=None)
        plan = service.plan(session, "doc_1")
        with pytest.raises(TranslationNotConfigured) as info:
            service.execute(session, plan)
        message = str(info.value)
        assert "mock" in message, "the refusal must say no placeholder text is produced"
        # The machine-readable code travels in its own channel.  It used to be prefixed onto
        # the message as well, and every report that showed both then read
        # "translation_not_configured: translation_not_configured: ...".  This pins the fix.
        assert error_code_for(info.value) == ErrorCode.TRANSLATION_NOT_CONFIGURED
        assert not message.startswith(ErrorCode.TRANSLATION_NOT_CONFIGURED.value), (
            "the message must not repeat the error code"
        )

    def test_planning_never_needs_a_provider(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose.")])
        service = TranslationService(Settings(), provider=None)
        plan = service.plan(session, "doc_1")
        assert len(plan.pending_units) == 1
        assert plan.provider == "unconfigured"


class TestPersistedRows:
    def test_one_row_per_unit_with_the_full_identity(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose one.")])
        service = TranslationService(Settings(), provider=StubProvider())
        run = service.execute(session, service.plan(session, "doc_1"))
        session.flush()

        assert len(run.saved) == 1
        row = run.saved[0]
        assert row.status == STATUS_OK
        assert row.is_current is True
        assert row.stale is False
        assert row.origin == "machine"
        assert row.version == 1
        assert row.text == "[zh] Prose one."
        assert row.provider == "stub"
        assert row.model == "stub-model-v1"
        assert row.prompt_version and row.glossary_version
        assert row.source_hash and row.cache_key

    def test_a_second_translation_supersedes_the_first_without_deleting_it(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose one.")])
        service = TranslationService(Settings(), provider=StubProvider())
        service.execute(session, service.plan(session, "doc_1"))
        session.flush()

        # Change the text, which changes the hash and so the cache key.
        session.execute(
            Block.__table__.update().where(Block.id == "blk_0").values(
                clean_text="Prose one, corrected.", text_hash="different"
            )
        )
        session.flush()

        service.execute(session, service.plan(session, "doc_1"))
        session.flush()

        rows = list(
            session.scalars(
                select(Translation).where(Translation.block_id == "blk_0").order_by(Translation.version)
            )
        )
        assert [r.version for r in rows] == [1, 2]
        assert [r.is_current for r in rows] == [False, True]
        assert rows[1].text == "[zh] Prose one, corrected."

    def test_an_unknown_result_is_recorded_as_unknown(self, session) -> None:
        # The ambiguous case: the request timed out, so the service may or may not have
        # processed the text.  Recording it as a failure would be a lie in the other
        # direction; recording it as done would be a lie in this one.
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose one.")])
        provider = StubProvider(
            script={
                "blk_0": TranslationResult(
                    block_id="blk_0",
                    status="unknown",
                    error_code=ErrorCode.EXTERNAL_UNKNOWN_RESULT.value,
                )
            }
        )
        run = TranslationService(Settings(), provider=provider).execute(
            session, TranslationService(Settings(), provider=provider).plan(session, "doc_1")
        )
        session.flush()
        assert run.saved[0].status == STATUS_UNKNOWN
        assert run.unknown and run.partial

    def test_a_failed_result_is_recorded_as_failed_and_not_current(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose one.")])
        provider = StubProvider(
            script={
                "blk_0": TranslationResult(
                    block_id="blk_0",
                    status="failed",
                    error_code=ErrorCode.EXTERNAL_RATE_LIMITED.value,
                    error_message="429 after 3 attempts",
                )
            }
        )
        service = TranslationService(Settings(), provider=provider)
        run = service.execute(session, service.plan(session, "doc_1"))
        session.flush()
        assert run.saved[0].status == STATUS_FAILED
        assert run.saved[0].is_current is False
        assert run.saved[0].error_message == "429 after 3 attempts"
        assert run.partial

    def test_a_unit_the_provider_never_answers_about_is_reported(self, session) -> None:
        make_document(
            session,
            blocks=[
                (BlockType.PARAGRAPH.value, "One."),
                (BlockType.PARAGRAPH.value, "Two."),
            ],
        )

        class Forgetful(StubProvider):
            def translate(self, units, **kwargs):
                outcome = super().translate(units, **kwargs)
                outcome.results = outcome.results[:1]
                return outcome

        service = TranslationService(Settings(), provider=Forgetful())
        run = service.execute(session, service.plan(session, "doc_1"))
        session.flush()
        assert [u.block_id for u in run.unanswered] == ["blk_1"]
        assert run.partial


class TestSummary:
    def test_the_summary_reports_every_quantity_separately(self, session) -> None:
        make_document(
            session,
            blocks=[
                (BlockType.PARAGRAPH.value, "One."),
                (BlockType.PARAGRAPH.value, "Two."),
                (BlockType.FORMULA.value, "x = y"),
            ],
        )
        provider = StubProvider(
            script={
                "blk_1": TranslationResult(
                    block_id="blk_1", status="failed", error_code=ErrorCode.TRANSLATION_FAILED.value
                )
            }
        )
        service = TranslationService(Settings(), provider=provider)
        run = service.execute(session, service.plan(session, "doc_1"))
        summary = run.summary()
        assert summary["requested"] == 2
        assert summary["translated"] == 1
        assert summary["failed"] == 1
        assert summary["unknown"] == 0
        assert summary["skipped_non_translatable"] == 1
        assert summary["provider"] == "stub"

    def test_glossary_reaches_the_provider(self, session) -> None:
        make_document(session, blocks=[(BlockType.PARAGRAPH.value, "Prose.")])
        session.add(
            Term(
                id="term_1",
                source="a",
                target="b",
                target_language="zh",
                scope="document",
                document_id="doc_1",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.flush()
        provider = StubProvider()
        service = TranslationService(Settings(), provider=provider)
        service.execute(session, service.plan(session, "doc_1"))
        assert provider.glossaries[0] == [("a", "b")]
