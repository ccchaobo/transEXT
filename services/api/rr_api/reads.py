"""Read models: the shapes the reader UI consumes, built to the frozen wire contract.

Payloads are assembled here rather than serialised from ORM rows, because three decisions have
to be made somewhere and this is the right place:

* **Coordinates stay in the display frame.**  ``SourceSpanRow.bbox`` is display-normalised
  (0..1, origin top-left of the page as shown).  That is the frozen contract in the spec; the
  browser draws overlays in that space and never sees raw PDF units.  ``raw_bbox`` travels
  alongside it for anything that needs the source space.
* **Page numbers are unambiguous.**  The internal index is 0-based and is reported as ``index``;
  the number the reader counts with is 1-based and is reported as ``page_number``.  They are
  never conflated, because a UI that shows "page 3" for index 3 is wrong by one.
* **One translation per block is chosen here.**  A block can have many translation rows
  (versions, targets, providers).  Picking the right one is a decision, so it is made once and
  reported with its provenance; the UI is never asked to guess.

The contract's ``TranslationWire.status`` only admits ``ok``/``failed``/``partial``, but the
service also has ``unknown`` — the outcome where the provider may or may not have processed the
text.  That is mapped to ``partial`` and *also* reported as an issue with the
``EXTERNAL_UNKNOWN_RESULT`` code, so the ambiguity is never lost just because the enum has no
slot for it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_core.models import BlockType
from rr_contracts import ErrorCode
from rr_storage import (
    Block,
    Document,
    DocumentLabel,
    Page,
    ParseRevision,
    SourceSpanRow,
    Translation,
)

#: Rotation is stored as a degree value; only quarter turns are representable, and the wire
#: contract says so.  Anything else would be an AffineTransform, not a rotation (ADR 0008).
QUARTER_TURNS = (0, 90, 180, 270)

#: Statuses the service uses that the wire enum does not name, and the nearest honest value.
_TRANSLATION_STATUS_MAP = {"ok": "ok", "failed": "failed", "unknown": "partial"}

#: The same mapping for the block-level indicator, whose vocabulary is narrower still
#: (``none``/``pending``/``ok``/``failed``/``stale``).  An ambiguous translation becomes
#: ``pending`` — "not settled" — rather than ``failed``, because it may well have succeeded;
#: the exact state, and the reason, are on the block's own translation payload.
_BLOCK_TRANSLATION_STATUS = {"ok": "ok", "failed": "failed", "unknown": "pending"}


def active_revision(session: Session, document: Document) -> ParseRevision | None:
    """The revision the document is currently read at.

    A document with no revision has never been parsed.  That is reported as no revision rather
    than as an empty revision, so the reader can say "not parsed yet" instead of showing a
    blank page that looks like a successful parse of nothing.
    """
    if not document.active_revision_id:
        return None
    return session.get(ParseRevision, document.active_revision_id)


def _display_size(page: Page) -> tuple[float, float]:
    """The size of the page as displayed, after rotation and cropping."""
    width, height = page.width_pt, page.height_pt
    if page.crop and len(page.crop) == 4:
        width = page.crop[2] - page.crop[0]
        height = page.crop[3] - page.crop[1]
    if page.rotation in (90, 270):
        width, height = height, width
    return width, height


def document_summary(session: Session, document: Document) -> dict[str, Any]:
    block_count = session.scalar(
        select(func.count()).select_from(Block).where(Block.document_id == document.id)
    )
    translated = session.scalar(
        select(func.count(func.distinct(Translation.block_id))).where(
            Translation.document_id == document.id
        )
    )
    revision = active_revision(session, document)
    labels = [
        row.label
        for row in session.scalars(
            select(DocumentLabel).where(DocumentLabel.document_id == document.id)
        ).all()
    ]
    warnings = list(revision.warnings or []) if revision else []
    if revision is not None:
        # Page warnings count too.  A scanned page carries "no text layer on this page; OCR is
        # required" on the page row, not on the revision, so a library card built from revision
        # warnings alone showed **0 warnings** for a three-page scan whose every page was
        # warning about missing text - the reader would have had no signal that anything was
        # found wanting until they opened each page.
        for page_warnings in session.scalars(
            select(Page.warnings).where(Page.revision_id == revision.id)
        ).all():
            warnings.extend(page_warnings or [])
    return {
        "id": document.id,
        "filename": document.filename,
        "format": document.format,
        "size_bytes": document.size_bytes,
        "sha256": document.sha256,
        "page_count": document.page_count,
        "imported_at": document.imported_at,
        "processing_mode": document.processing_mode,
        "active_revision_id": document.active_revision_id,
        "parse_status": document.parse_status,
        "translate_status": document.translate_status,
        "progress": document.progress or 0.0,
        "warning_count": len(warnings),
        "error_count": len(revision.issues or []) if revision else 0,
        "last_opened_at": document.last_opened_at,
        "labels": labels,
        "title": document.title,
        # block_count and translated_block_count are declared on the contract's DocumentSummary.
        # They were being returned here before the contract knew about them, and FastAPI silently
        # dropped them from the response - which is why a test now asserts they arrive.
        # second request to get them would be worse.  Reported under names of their own.
        "block_count": block_count or 0,
        "translated_block_count": translated or 0,
    }


def page_summary(page: Page) -> dict[str, Any]:
    width, height = _display_size(page)
    rotation = page.rotation if page.rotation in QUARTER_TURNS else 0
    return {
        "index": page.page_index,
        "page_number": page.page_index + 1,
        "printed_label": page.printed_label,
        "width_pt": page.width_pt,
        "height_pt": page.height_pt,
        "rotation": rotation,
        "display_width": width,
        "display_height": height,
        "source_kind": page.source_kind,
        "ocr_applied": page.ocr_applied,
        "text_chars": page.text_chars,
        "warnings": list(page.warnings or []),
        "preview_url": f"/api/v1/documents/{page.document_id}/pages/{page.page_index}/preview.png",
        "thumbnail_url": f"/api/v1/documents/{page.document_id}/pages/{page.page_index}/thumbnail.png",
    }


def latest_translations(
    session: Session, block_ids: list[str], *, target_language: str | None = None
) -> dict[str, Translation]:
    """The translation to show for each block: usable rows first, then highest version.

    A failed or ambiguous row is still returned when it is all a block has — the reader has to
    be able to see it — but it never displaces a good one.
    """
    if not block_ids:
        return {}
    statement = select(Translation).where(Translation.block_id.in_(block_ids))
    if target_language:
        statement = statement.where(Translation.target_language == target_language)
    rows = session.scalars(
        statement.order_by(
            Translation.block_id,
            (Translation.status != "ok"),
            Translation.version.desc(),
            Translation.id.desc(),
        )
    ).all()
    chosen: dict[str, Translation] = {}
    for row in rows:
        chosen.setdefault(row.block_id, row)
    return chosen


def translation_payload(row: Translation) -> dict[str, Any]:
    """One translation row in wire shape.

    ``stale`` and the stored ``issues`` come from the row itself.  Both were being hardcoded —
    ``stale: False`` and an empty issue list — which would have hidden exactly the two things
    this contract has fields for: a translation whose source text has since changed, and
    whatever problem the service recorded about it.
    """
    issues: list[dict[str, Any]] = [
        {
            "code": (issue or {}).get("code") or ErrorCode.TRANSLATION_FAILED.value,
            "severity": (issue or {}).get("severity") or "warning",
            "message": (issue or {}).get("message") or "",
            "page_index": (issue or {}).get("page_index"),
            "block_id": row.block_id,
            "details": dict((issue or {}).get("details") or {}),
        }
        for issue in (row.issues or [])
    ]
    status = _TRANSLATION_STATUS_MAP.get(row.status, "failed")
    if row.status == "unknown":
        # The wire enum has no slot for "ambiguous", so the value is mapped to `partial` and the
        # ambiguity is carried here instead.  Losing it would mean telling the reader a block is
        # definitively untranslated when nobody knows whether it was processed.
        issues.append(
            {
                "code": ErrorCode.EXTERNAL_UNKNOWN_RESULT.value,
                "severity": "warning",
                "message": (
                    "the service may or may not have processed this block; the result is "
                    "ambiguous and it was not retried automatically"
                ),
                "page_index": None,
                "block_id": row.block_id,
                "details": {},
            }
        )
    elif row.status not in _TRANSLATION_STATUS_MAP:
        issues.append(
            {
                "code": row.error_code or ErrorCode.TRANSLATION_FAILED.value,
                "severity": "error",
                "message": row.error_message or f"translation status {row.status!r}",
                "page_index": None,
                "block_id": row.block_id,
                "details": {},
            }
        )
    return {
        "id": row.id,
        "block_id": row.block_id,
        "text": row.text,
        "language": row.source_language,
        "target_language": row.target_language,
        "provider": row.provider,
        "model": row.model,
        "version": row.version,
        "status": status,
        "origin": row.origin,
        "stale": bool(row.stale),
        "error_code": row.error_code,
        "created_at": row.created_at,
        "usage": dict(row.usage or {}),
        "issues": issues,
        # Beyond the contract, and needed by the UI: the raw status is what separates an
        # ambiguous result from a failed one, and whether a human wrote this.
        "raw_status": row.status,
        "is_current": bool(row.is_current),
        "created_by": row.created_by,
        "prompt_version": row.prompt_version,
        "glossary_version": row.glossary_version,
        "is_human": row.origin == "human",
        "error_message": row.error_message,
    }


def block_payload(
    block: Block,
    spans: list[SourceSpanRow],
    translation: Translation | None,
) -> dict[str, Any]:
    try:
        block_type = BlockType(block.type)
    except ValueError:
        # An unrecognised type is reported as unknown rather than dropped: silently removing a
        # block would lose text the reader paid to have parsed.
        block_type = BlockType.UNKNOWN

    pages = sorted({span.page_index for span in spans}) or [block.page_index]
    if translation is None:
        translation_status = "none"
    else:
        translation_status = _BLOCK_TRANSLATION_STATUS.get(translation.status, "failed")

    return {
        "id": block.id,
        "page_index": block.page_index,
        "order": block.order_index,
        "type": block.type,
        "text": block.clean_text or block.raw_text,
        "raw_text": block.raw_text,
        "translatable": block_type.is_translatable and bool((block.clean_text or block.raw_text).strip()),
        "is_body": block_type.is_body,
        "confidence": {
            "score": block.confidence_score,
            "level": block.confidence_level,
            "source": block.confidence_source,
        },
        "spans": [
            {
                "page_index": span.page_index,
                "bbox": list(span.bbox),
                "raw_bbox": list(span.raw_bbox) if span.raw_bbox else None,
                "raw_space": span.raw_space,
                "polygon": span.polygon,
                "text_start": span.text_start,
                "text_end": span.text_end,
            }
            for span in sorted(spans, key=lambda s: s.ordinal)
        ],
        "pages": pages,
        "section_path": list(block.section_path or []),
        "section_id": block.section_id,
        "level": block.level,
        "cross_page": len(pages) > 1,
        "human_edited": bool(block.ocr_revision),
        "table": block.table_data,
        "metadata": dict(block.extra or {}),
        "translation_status": translation_status,
        # Beyond the contract: the reader draws a block highlight from this, and deriving it in
        # the browser would duplicate the union logic in a second place.
        "bbox": _union([list(span.bbox) for span in spans if span.page_index == block.page_index]),
        "translation": translation_payload(translation) if translation else None,
    }


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def page_payload(
    session: Session,
    page: Page,
    *,
    target_language: str | None = None,
) -> dict[str, Any]:
    """Everything needed to render one page, in one request."""
    blocks = session.scalars(
        select(Block)
        .where(Block.revision_id == page.revision_id, Block.page_index == page.page_index)
        .order_by(Block.order_index)
    ).all()
    block_ids = [block.id for block in blocks]
    spans: dict[str, list[SourceSpanRow]] = {block_id: [] for block_id in block_ids}
    if block_ids:
        for span in session.scalars(
            select(SourceSpanRow).where(SourceSpanRow.block_id.in_(block_ids))
        ).all():
            spans.setdefault(span.block_id, []).append(span)
    translations = latest_translations(session, block_ids, target_language=target_language)
    return {
        "page": page_summary(page),
        "document_id": page.document_id,
        "revision_id": page.revision_id,
        "blocks": [
            block_payload(block, spans.get(block.id, []), translations.get(block.id))
            for block in blocks
        ],
    }


def outline(session: Session, revision_id: str) -> list[dict[str, Any]]:
    """The section outline: the blocks the classifier typed as a heading or the title.

    Selecting on ``level IS NOT NULL`` returned **nothing at all** for the whole corpus, because
    the layout stage never writes that column — it tracks sections in ``section_path`` instead.
    The outline is therefore built from the block *type*, which is populated and measured at
    235/235 on the corpus, and the nesting level is taken from the section path's own depth when
    the column is absent.  An outline that silently came back empty would have looked like a
    document with no sections rather than a query against a column nobody fills.

    A document whose headings were not recognised still yields one entry or none; the caller
    flags that rather than presenting it as navigation.
    """
    rows = session.execute(
        select(
            Block.id,
            Block.page_index,
            Block.order_index,
            Block.type,
            Block.level,
            Block.section_id,
            Block.section_path,
            Block.clean_text,
            Block.raw_text,
        )
        .where(
            Block.revision_id == revision_id,
            (Block.type.in_(("title", "heading"))) | (Block.level.isnot(None)),
        )
        .order_by(Block.order_index)
    ).all()
    entries = []
    for row in rows:
        text = (row.clean_text or row.raw_text or "").strip()
        path = list(row.section_path or [])
        level = row.level
        if level is None:
            # Depth in the section path is what the layout actually knows: ["<title>"] is one
            # level, ["<title>", "2. Related Work"] is two.
            level = max(len(path), 1) if row.type != "title" else 1
        entries.append(
            {
                "block_id": row.id,
                "page_index": row.page_index,
                "page_number": row.page_index + 1,
                "order": row.order_index,
                "type": row.type,
                "level": level,
                "section_id": row.section_id,
                "section_path": path,
                "title": text[:200],
            }
        )
    return entries


def revision_payload(revision: ParseRevision, *, is_active: bool) -> dict[str, Any]:
    return {
        "id": revision.id,
        "parser": revision.parser,
        "parser_version": revision.parser_version,
        "status": revision.status,
        "created_at": revision.created_at,
        "parameters": dict(revision.parameters or {}),
        "warnings": list(revision.warnings or []),
        "stats": dict(revision.stats or {}),
        "is_active": is_active,
    }


__all__ = [
    "QUARTER_TURNS",
    "active_revision",
    "block_payload",
    "document_summary",
    "latest_translations",
    "outline",
    "page_payload",
    "page_summary",
    "revision_payload",
    "translation_payload",
]
