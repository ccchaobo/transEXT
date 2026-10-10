"""Paragraph translation: planning, cache reuse, and persistence.

The spec (12.1, 13) asks for three things that shape this module:

* a **real** replaceable service, never a mock and never a key in the frontend;
* a cache key that is complete, so a hit is always safe to reuse;
* an explicit outcome for every block, including the ambiguous one.

The module is split into planning and execution on purpose.  ``plan`` reads the database and
decides what *would* be sent and what can be reused; it makes no network call and costs
nothing.  ``execute`` sends that plan.  The API layer calls both back to back, and the worker
calls ``plan`` first so it can refuse an oversized job before any money is spent, which is
the guard the spec asks for.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from document_core.models import BlockType
from rr_adapters.translation import (
    PROMPT_VERSION,
    TranslationOutcome,
    TranslationProvider,
    TranslationUnit,
)
from rr_contracts import ErrorCode
from rr_storage import Block, Document, Term, Translation, utcnow

#: Bumped whenever the *meaning* of a cache key input changes, so that old rows can never be
#: mistaken for new ones even though every field still matches.
CACHE_KEY_VERSION = "1"

GLOSSARY_VERSION_NONE = "none"
DEFAULT_SOURCE_LANGUAGE = "en"
DEFAULT_TARGET_LANGUAGE = "zh"

#: ``unknown`` is not a job status and not a translation status either.  It is stored in
#: ``Translation.status`` as its own value because the spec requires the ambiguous case to be
#: visible rather than folded into a failure: the service may or may not have processed the
#: text, and we must not claim either.
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"


class TranslationNotConfigured(RuntimeError):
    """No provider is configured.  Raised before any spend, never after."""


class TranslationBudgetExceeded(RuntimeError):
    """The plan is larger than the configured per-job ceiling.  Raised before any spend."""

    def __init__(self, requested: int, limit: int) -> None:
        super().__init__(
            f"plan covers {requested} units but the limit is {limit}; "
            "refusing before any request is sent"
        )
        self.requested = requested
        self.limit = limit


# ---------------------------------------------------------------------------
# cache identity
# ---------------------------------------------------------------------------


def cache_key(
    *,
    text_hash: str,
    revision_id: str,
    source_language: str,
    target_language: str,
    provider: str,
    model: str | None,
    prompt_version: str,
    glossary_version: str,
    params: dict[str, Any] | None = None,
) -> str:
    """The identity of one translation.

    Every input that can change the *output* is in here, which is what makes a hit safe to
    reuse.  ``revision_id`` is included because the spec requires it, and the consequence is
    recorded rather than hidden: a re-parse of an unchanged document invalidates the cache and
    the text is paid for again (limitations.md 19).  ``text_hash`` alone would be enough for
    correctness; the revision makes the key stricter, not looser.
    """
    payload = {
        "v": CACHE_KEY_VERSION,
        "text_hash": text_hash,
        "revision_id": revision_id,
        "source_language": source_language,
        "target_language": target_language,
        "provider": provider,
        "model": model or "",
        "prompt_version": prompt_version,
        "glossary_version": glossary_version,
        "params": _canonical(params or {}),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


def _canonical(value: Any) -> Any:
    """Make a dict safe to hash: sorted keys, no non-JSON values."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlannedUnit:
    """One block, and what is already known about translating it."""

    block_id: str
    page_index: int
    order_index: int
    block_type: str
    text: str
    context: str | None
    cache_key: str
    #: an existing, current, successful translation for this exact key
    reusable: Translation | None = None

    @property
    def needs_work(self) -> bool:
        return self.reusable is None


@dataclass(slots=True)
class TranslationPlan:
    document_id: str
    revision_id: str
    source_language: str
    target_language: str
    provider: str
    model: str | None
    prompt_version: str
    glossary_version: str
    glossary: list[tuple[str, str]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    units: list[PlannedUnit] = field(default_factory=list)
    #: blocks skipped because their type is not translatable (formulas, references, furniture)
    skipped_non_translatable: int = 0

    @property
    def reusable_units(self) -> list[PlannedUnit]:
        return [u for u in self.units if u.reusable is not None]

    @property
    def pending_units(self) -> list[PlannedUnit]:
        return [u for u in self.units if u.needs_work]

    @property
    def is_empty(self) -> bool:
        return not self.units


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


class TranslationService:
    """Plan and run translation for one document revision."""

    def __init__(
        self,
        settings: Any,
        workspace: Any = None,
        queue: Any = None,
        provider: TranslationProvider | None = None,
    ) -> None:
        self.settings = settings
        self.workspace = workspace
        self.queue = queue
        self.provider = provider

    # -- glossary ---------------------------------------------------------

    def glossary(
        self, session: Session, document_id: str, target_language: str
    ) -> tuple[list[tuple[str, str]], str]:
        """The term list for a document, and a version string that identifies it.

        The version is a hash of the *contents*, because that is what changes the translation.
        A term row whose ``version`` was bumped without changing the text must not invalidate
        every cached translation in the document.
        """
        rows = list(
            session.scalars(
                select(Term).where(
                    Term.target_language == target_language,
                    (Term.scope == "global") | (Term.document_id == document_id),
                )
            )
        )
        rows.sort(key=lambda t: (-t.priority, t.source))
        pairs = [(t.source, t.target) for t in rows if t.source and t.target]
        if not pairs:
            return [], GLOSSARY_VERSION_NONE
        blob = json.dumps(pairs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return pairs, hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    # -- planning ---------------------------------------------------------

    def plan(
        self,
        session: Session,
        document_id: str,
        *,
        source_language: str = DEFAULT_SOURCE_LANGUAGE,
        target_language: str = DEFAULT_TARGET_LANGUAGE,
        params: dict[str, Any] | None = None,
        only_block_ids: Iterable[str] | None = None,
    ) -> TranslationPlan:
        """Decide what would be sent.  Reads only; makes no network call."""
        document = session.get(Document, document_id)
        if document is None:
            raise LookupError(f"no such document: {document_id}")
        revision_id = document.active_revision_id
        if not revision_id:
            raise LookupError(f"document {document_id} has no active revision")

        provider_name = getattr(self.provider, "name", None) or "unconfigured"
        model = getattr(self.provider, "model", None)
        glossary, glossary_version = self.glossary(session, document_id, target_language)
        params = dict(params or {})

        plan = TranslationPlan(
            document_id=document_id,
            revision_id=revision_id,
            source_language=source_language,
            target_language=target_language,
            provider=provider_name,
            model=model,
            prompt_version=PROMPT_VERSION,
            glossary_version=glossary_version,
            glossary=glossary,
            params=params,
        )

        wanted = set(only_block_ids) if only_block_ids is not None else None
        blocks = list(
            session.scalars(
                select(Block)
                .where(Block.revision_id == revision_id, Block.document_id == document_id)
                .order_by(Block.page_index, Block.order_index)
            )
        )

        candidates: list[PlannedUnit] = []
        for block in blocks:
            if wanted is not None and block.id not in wanted:
                continue
            if not _is_translatable(block.type):
                plan.skipped_non_translatable += 1
                continue
            text = (block.clean_text or block.raw_text or "").strip()
            if not text:
                plan.skipped_non_translatable += 1
                continue
            candidates.append(
                PlannedUnit(
                    block_id=block.id,
                    page_index=block.page_index,
                    order_index=block.order_index,
                    block_type=block.type,
                    text=text,
                    context=_context_for(block, text),
                    cache_key=cache_key(
                        text_hash=block.text_hash or _hash(text),
                        revision_id=revision_id,
                        source_language=source_language,
                        target_language=target_language,
                        provider=provider_name,
                        model=model,
                        prompt_version=PROMPT_VERSION,
                        glossary_version=glossary_version,
                        params=params,
                    ),
                )
            )

        # One query for every candidate key, rather than one per block.
        keys = {u.cache_key for u in candidates}
        existing: dict[str, Translation] = {}
        if keys:
            rows = session.scalars(
                select(Translation).where(
                    Translation.cache_key.in_(keys),
                    Translation.status == STATUS_OK,
                    Translation.is_current.is_(True),
                )
            )
            for row in rows:
                # If two rows share a key, keep the newest; the older one is superseded data.
                current = existing.get(row.cache_key)
                if current is None or (row.created_at and current.created_at and row.created_at > current.created_at):
                    existing[row.cache_key] = row

        for unit in candidates:
            unit.reusable = existing.get(unit.cache_key)
        plan.units = candidates
        return plan

    # -- execution --------------------------------------------------------

    def execute(
        self,
        session: Session,
        plan: TranslationPlan,
        *,
        lease: Any = None,
        max_units: int | None = None,
    ) -> "TranslationRun":
        """Send the plan's pending units and persist one row per unit.

        Nothing is sent until the size of the plan has been checked against the ceiling, and
        nothing is sent at all when no provider is configured.
        """
        if self.provider is None:
            raise TranslationNotConfigured(
                f"{ErrorCode.TRANSLATION_NOT_CONFIGURED.value}: no translation provider is "
                "configured.  Set RR_TRANSLATION_API_KEY (and RR_TRANSLATION_BASE_URL if the "
                "default is wrong) before translating.  No placeholder or mock text is "
                "produced, because a mock would make an unverified pipeline look finished."
            )

        pending = plan.pending_units
        limit = self.settings.max_translation_units if max_units is None else max_units
        if limit and len(pending) > limit:
            raise TranslationBudgetExceeded(len(pending), limit)

        run = TranslationRun(plan=plan)
        if not pending:
            return run

        units = [
            TranslationUnit(block_id=u.block_id, text=u.text, context=u.context)
            for u in pending
        ]
        outcome = self.provider.translate(
            units,
            glossary=plan.glossary,
            source_language=plan.source_language,
            target_language=plan.target_language,
            options=plan.params,
        )
        run.outcome = outcome

        by_id = {u.block_id: u for u in pending}
        for result in outcome.results:
            unit = by_id.get(result.block_id)
            if unit is None:
                continue
            row = self._store(session, plan, unit, result, lease=lease)
            run.saved.append(row)
            # `unknown` is a third outcome, not a flavour of failure.  A unit the provider
            # explicitly reports as unknown may or may not have been processed, so it is
            # surfaced as ambiguous rather than counted as done or as failed.
            if row.status == STATUS_UNKNOWN:
                run.unknown.append(unit)
        session.flush()

        # A unit the provider never answered about is ambiguous in the same way, and saying so
        # is the only honest option.  The spec forbids silently treating it as done or failed.
        answered = {r.block_id for r in outcome.results}
        run.unanswered = [u for u in pending if u.block_id not in answered]
        run.unknown.extend(run.unanswered)
        return run

    def _store(
        self,
        session: Session,
        plan: TranslationPlan,
        unit: PlannedUnit,
        result: Any,
        *,
        lease: Any = None,
    ) -> Translation:
        """Write one translation, superseding any earlier version of the same block."""
        status = STATUS_OK if result.ok else (result.status or STATUS_FAILED)
        if status == "unknown":
            status = STATUS_UNKNOWN

        previous = list(
            session.scalars(
                select(Translation).where(
                    Translation.block_id == unit.block_id,
                    Translation.is_current.is_(True),
                )
            )
        )
        version = 1 + max((row.version for row in previous), default=0)
        for row in previous:
            # The old row is kept so that a correction can be traced; it is only demoted.
            row.is_current = False

        row = Translation(
            id=f"tr_{_token()}",
            document_id=plan.document_id,
            block_id=unit.block_id,
            revision_id=plan.revision_id,
            version=version,
            source_hash=_hash(unit.text),
            source_language=plan.source_language,
            target_language=plan.target_language,
            provider=plan.provider,
            model=result.model or plan.model,
            prompt_version=plan.prompt_version,
            glossary_version=plan.glossary_version,
            params=plan.params,
            cache_key=unit.cache_key,
            text=result.text or "",
            status=status,
            origin="machine",
            error_code=result.error_code,
            error_message=result.error_message,
            usage=result.usage or {},
            issues=[],
            is_current=status == STATUS_OK,
            stale=False,
            created_at=utcnow(),
            created_by="worker" if lease is not None else "api",
        )
        session.add(row)
        return row


@dataclass(slots=True)
class TranslationRun:
    """What a run did, in a form the API can return and the worker can log."""

    plan: TranslationPlan
    outcome: TranslationOutcome | None = None
    saved: list[Translation] = field(default_factory=list)
    #: units the provider reported as `unknown`: it may or may not have processed them
    unknown: list[PlannedUnit] = field(default_factory=list)
    #: units the provider never mentioned at all
    unanswered: list[PlannedUnit] = field(default_factory=list)

    @property
    def requested(self) -> int:
        return len(self.plan.pending_units)

    @property
    def translated(self) -> int:
        return sum(1 for row in self.saved if row.status == STATUS_OK)

    @property
    def failed(self) -> int:
        return sum(1 for row in self.saved if row.status == STATUS_FAILED)

    @property
    def reused(self) -> int:
        return len(self.plan.reusable_units)

    @property
    def partial(self) -> bool:
        """True when the run must be reported as partial rather than complete."""
        return bool(self.unknown) or self.failed > 0

    def summary(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "translated": self.translated,
            "failed": self.failed,
            "unknown": len(self.unknown),
            "reused": self.reused,
            "skipped_non_translatable": self.plan.skipped_non_translatable,
            "usage": (self.outcome.usage if self.outcome else {}),
            "provider": self.plan.provider,
            "model": self.plan.model,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_translatable(block_type: str) -> bool:
    """Whether a stored block type should be sent to a translation service.

    A block type this code does not recognise is treated as *translatable*, because the
    expensive mistake is dropping real prose: a formula typed as a paragraph wastes a request,
    while a paragraph typed as a formula silently never gets translated.
    """
    try:
        return BlockType(block_type).is_translatable
    except ValueError:
        return True


def _context_for(block: Block, text: str) -> str | None:
    """Guidance handed to the provider alongside the text.

    The section path is enough to resolve pronouns and terminology that the sentence alone
    cannot.  It is never sent as text to be translated, so it cannot appear in the output.

    A context that is already *in* the text is dropped.  Measured on `pdf_b_two_column`: the
    paper title is treated as a level-1 section, so every block's section path was the title,
    and the title block's context was the title itself — sending it would pay for the same
    string twice and tell the provider nothing.  The section path is poor for a different
    reason (real section headings are merged into the paragraph below them, so they never
    reach the outline — limitations.md 15), and dropping the duplicate does not fix that.
    """
    path = block.section_path or []
    if not isinstance(path, list):
        return None
    joined = " > ".join(str(part) for part in path if part).strip()
    if not joined:
        return None
    if joined.casefold() in text.casefold():
        return None
    return joined


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]


def _token() -> str:
    import uuid

    return uuid.uuid4().hex[:24]


def error_code_for(exc: BaseException) -> str:
    """Map a service-level exception onto the shared error vocabulary."""
    if isinstance(exc, TranslationNotConfigured):
        return ErrorCode.TRANSLATION_NOT_CONFIGURED.value
    if isinstance(exc, TranslationBudgetExceeded):
        return ErrorCode.BUDGET_EXCEEDED.value
    return ErrorCode.TRANSLATION_FAILED.value


__all__ = [
    "CACHE_KEY_VERSION",
    "DEFAULT_SOURCE_LANGUAGE",
    "DEFAULT_TARGET_LANGUAGE",
    "GLOSSARY_VERSION_NONE",
    "PlannedUnit",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_UNKNOWN",
    "TranslationBudgetExceeded",
    "TranslationNotConfigured",
    "TranslationPlan",
    "TranslationRun",
    "TranslationService",
    "cache_key",
    "error_code_for",
]
