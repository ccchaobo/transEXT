"""Repository helpers: converting between ORM rows and the wire contracts.

Keeping this separate from the routers means the conversion rules (page number
is 1-based, bbox is display-normalised, translation staleness) live in one
place and are unit-testable without an HTTP client.
"""

from __future__ import annotations

from typing import Any, Iterable

from rr_contracts import (
    BlockWire,
    ConfidenceWire,
    DocumentDetail,
    DocumentSummary,
    IssueWire,
    PageWire,
    ParseRevisionWire,
    SpanWire,
    TermWire,
    TranslationWire,
)
from rr_storage.models import Block, Document, Page, ParseRevision, Term, Translation

__all__ = [
    "page_to_wire",
    "block_to_wire",
    "revision_to_wire",
    "document_to_summary",
    "document_to_detail",
    "translation_to_wire",
    "term_to_wire",
    "issues_to_wire",
]


def page_to_wire(page: Page, *, preview_url: str | None, thumbnail_url: str | None) -> PageWire:
    from rr_storage.models import utcnow  # local import keeps the module light

    rotation = page.rotation % 360
    width = page.width_pt
    height = page.height_pt
    display_width, display_height = (height, width) if rotation in (90, 270) else (width, height)
    return PageWire(
        index=page.page_index,
        page_number=page.page_index + 1,
        printed_label=page.printed_label,
        width_pt=width,
        height_pt=height,
        rotation=rotation,
        display_width=display_width,
        display_height=display_height,
        source_kind=page.source_kind,
        preview_url=preview_url,
        thumbnail_url=thumbnail_url,
        text_chars=page.text_chars,
        ocr_applied=page.ocr_applied,
        warnings=list(page.warnings or []),
    )


def block_to_wire(
    block: Block,
    *,
    translation_status: str = "none",
) -> BlockWire:
    spans = [
        SpanWire(
            page_index=span.page_index,
            bbox=list(span.bbox),
            raw_bbox=list(span.raw_bbox) if span.raw_bbox else None,
            raw_space=span.raw_space,
            polygon=span.polygon,
            text_start=span.text_start,
            text_end=span.text_end,
        )
        for span in sorted(block.spans, key=lambda s: (s.page_index, s.ordinal))
    ]
    pages: list[int] = []
    for span in spans:
        if span.page_index not in pages:
            pages.append(span.page_index)
    from rr_contracts import BlockTypeWire  # noqa: F401  (typing only)

    return BlockWire(
        id=block.id,
        page_index=block.page_index,
        order=block.order_index,
        type=block.type,  # type: ignore[arg-type]
        text=block.clean_text or block.raw_text,
        raw_text=block.raw_text,
        translatable=_translatable(block),
        is_body=_is_body(block.type),
        confidence=ConfidenceWire(
            score=block.confidence_score,
            level=block.confidence_level,  # type: ignore[arg-type]
            source=block.confidence_source,
        ),
        spans=spans,
        pages=pages,
        section_path=list(block.section_path or []),
        section_id=block.section_id,
        level=block.level,
        cross_page=len(pages) > 1,
        human_edited=(block.ocr_revision or 0) > 0,
        table=block.table_data,
        metadata=dict(block.extra or {}),
        translation_status=translation_status,  # type: ignore[arg-type]
    )


_TRANSLATABLE_TYPES = {
    "title", "abstract", "heading", "paragraph", "list_item", "caption",
    "table_cell", "footnote",
}
_BODY_TYPES = {
    "title", "author", "affiliation", "abstract", "heading", "paragraph",
    "list_item", "caption", "table", "table_cell", "footnote", "reference",
}


def _translatable(block: Block) -> bool:
    return block.type in _TRANSLATABLE_TYPES and bool((block.clean_text or block.raw_text).strip())


def _is_body(block_type: str) -> bool:
    return block_type in _BODY_TYPES


def revision_to_wire(revision: ParseRevision, *, is_active: bool) -> ParseRevisionWire:
    return ParseRevisionWire(
        id=revision.id,
        parser=revision.parser,
        parser_version=revision.parser_version,
        status=revision.status,
        created_at=revision.created_at,
        parameters=dict(revision.parameters or {}),
        warnings=list(revision.warnings or []),
        stats=dict(revision.stats or {}),
        is_active=is_active,
    )


def issues_to_wire(issues: Iterable[dict[str, Any]]) -> list[IssueWire]:
    out: list[IssueWire] = []
    for issue in issues or []:
        try:
            out.append(IssueWire(**issue))
        except Exception:
            out.append(
                IssueWire(
                    code=str(issue.get("code", "unknown")),
                    severity="info",
                    message=str(issue.get("message", "")),
                    page_index=issue.get("page_index"),
                    block_id=issue.get("block_id"),
                    details={"raw": issue},
                )
            )
    return out


def document_to_summary(
    document: Document,
    *,
    warning_count: int = 0,
    error_count: int = 0,
    labels: list[str] | None = None,
) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        filename=document.filename,
        format=document.format,  # type: ignore[arg-type]
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        page_count=document.page_count,
        imported_at=document.imported_at,
        processing_mode=document.processing_mode,  # type: ignore[arg-type]
        active_revision_id=document.active_revision_id,
        parse_status=document.parse_status,  # type: ignore[arg-type]
        translate_status=document.translate_status,  # type: ignore[arg-type]
        progress=float(document.progress or 0.0),
        warning_count=warning_count,
        error_count=error_count,
        last_opened_at=document.last_opened_at,
        labels=labels or [row.label for row in (document.labels or [])],
        title=document.title,
    )


def document_to_detail(
    document: Document,
    *,
    revisions: list[ParseRevision],
    issues: list[dict[str, Any]],
    translations_summary: dict[str, int],
    capabilities: dict[str, bool],
    source_url: str,
    warning_count: int = 0,
    error_count: int = 0,
) -> DocumentDetail:
    summary = document_to_summary(
        document, warning_count=warning_count, error_count=error_count
    )
    return DocumentDetail(
        **summary.model_dump(),
        revisions=[
            revision_to_wire(r, is_active=(r.id == document.active_revision_id)) for r in revisions
        ],
        issues=issues_to_wire(issues),
        translations_summary=translations_summary,
        capabilities=capabilities,
        source_url=source_url,
    )


def translation_to_wire(translation: Translation) -> TranslationWire:
    return TranslationWire(
        id=translation.id,
        block_id=translation.block_id,
        text=translation.text,
        language=translation.source_language,
        target_language=translation.target_language,
        provider=translation.provider,
        model=translation.model,
        version=translation.version,
        status=translation.status,  # type: ignore[arg-type]
        origin=translation.origin,  # type: ignore[arg-type]
        stale=bool(translation.stale),
        error_code=translation.error_code,
        created_at=translation.created_at,
        usage=dict(translation.usage or {}),
        issues=issues_to_wire(translation.issues or []),
    )


def term_to_wire(term: Term) -> TermWire:
    return TermWire(
        id=term.id,
        source=term.source,
        target=term.target,
        domain=term.domain,
        scope=term.scope,
        document_id=term.document_id,
        priority=term.priority,
        mandatory=term.mandatory,
        case_sensitive=term.case_sensitive,
        version=term.version,
        note=term.note,
        updated_at=term.updated_at,
    )
