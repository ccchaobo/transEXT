"""Running a parse: a document file becomes pages, blocks and source spans.

The pipeline is ``extract -> analyse -> classify -> persist``.  Each stage already exists
and is tested; this module is the seam that joins them to storage and to the job queue.

Two things here are deliberate:

* **Progress is recorded per page, not per job.**  The queue already models units, so a
  200-page paper reports how far it has got and a failure names the page it failed on,
  instead of a spinner that means nothing.
* **A page that yields no text is recorded as such, not skipped.**  A scanned page inside
  an otherwise digital paper is the normal case for older articles, and a page that is
  silently absent looks like a bug in the reader.  It becomes a page with
  ``source_kind="scanned"`` and a warning naming the reason.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from document_core.blocks import body_font_size, classify_paragraph
from document_core.layout import LayoutResult, Paragraph, analyse
from document_core.models import ConfidenceLevel, ParseStatus
from rr_adapters.pdf_extract import PdfExtraction, PdfTextExtractor
from rr_storage import (
    Block,
    Document,
    JobQueue,
    LeaseInfo,
    Page,
    ParseRevision,
    SourceSpanRow,
    Workspace,
    utcnow,
)

from .settings import Settings

#: The parser identity recorded on the revision.  Bumping this invalidates an identical
#: re-parse, which is what should happen when the algorithm changes.
PARSER_NAME = "pdf_text"
PARSER_VERSION = "1.0.0"

#: Formats whose text can be read today.  An image needs OCR and a DOCX needs conversion;
#: both adapters exist but are not wired into this stage yet, and saying so plainly is
#: better than a parse that quietly produces nothing.
SUPPORTED_FORMATS = {"pdf"}


@dataclass
class ParseOutcome:
    revision_id: str
    status: str
    pages: int = 0
    blocks: int = 0
    translatable_blocks: int = 0
    body_blocks: int = 0
    warnings: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == ParseStatus.SUCCEEDED.value


class ParseService:
    """Turn a document's original file into a parse revision."""

    def __init__(self, settings: Settings, workspace: Workspace, queue: JobQueue) -> None:
        self.settings = settings
        self.workspace = workspace
        self.queue = queue
        self.extractor = PdfTextExtractor()

    # -- entry point -------------------------------------------------------

    def run(self, session: Session, lease: LeaseInfo, document_id: str) -> ParseOutcome:
        document = session.get(Document, document_id)
        if document is None:
            raise ValueError(f"no document {document_id}")
        if document.format not in SUPPORTED_FORMATS:
            # Explicit, not silent: the user is told which format is not wired up rather
            # than being shown an empty document.
            raise NotImplementedError(
                f"parsing {document.format} is not implemented yet; "
                f"supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
            )

        self.queue.set_stage(session, lease, "extract", "reading the text layer")
        source = self.workspace.resolve(document.source_path)
        extraction = self.extractor.extract(source)

        revision = self._create_revision(session, document, extraction)
        self.queue.set_stage(session, lease, "layout", "ordering blocks")
        layout = analyse(extraction.lines_by_page)

        outcome = ParseOutcome(revision_id=revision.id, status=ParseStatus.SUCCEEDED.value)
        outcome.warnings.extend(extraction.warnings)
        outcome.warnings.extend(layout.warnings)

        self.queue.set_stage(session, lease, "persist", "saving pages and blocks")
        self._persist_pages(session, lease, document, revision, extraction, outcome)
        self._persist_blocks(session, lease, document, revision, extraction, layout, outcome)

        self._finish_revision(session, document, revision, outcome)
        document.parse_status = outcome.status
        document.page_count = extraction.page_count
        document.active_revision_id = revision.id
        document.progress = 1.0
        session.flush()

        return outcome

    # -- revision ----------------------------------------------------------

    def _create_revision(
        self, session: Session, document: Document, extraction: PdfExtraction
    ) -> ParseRevision:
        # A new id every time, deliberately: a re-parse is a NEW revision, and reusing the
        # id would silently re-attach existing annotations to blocks that may have moved.
        revision = ParseRevision(
            id=f"rev_{uuid.uuid4().hex[:24]}",
            document_id=document.id,
            parser=PARSER_NAME,
            parser_version=PARSER_VERSION,
            status=ParseStatus.RUNNING.value,
            parameters={"source": document.source_path, "format": document.format},
            input_hash=document.sha256,
            warnings=[],
            issues=[],
            stats={},
        )
        session.add(revision)
        session.flush()
        return revision

    def _persist_pages(
        self,
        session: Session,
        lease: LeaseInfo,
        document: Document,
        revision: ParseRevision,
        extraction: PdfExtraction,
        outcome: ParseOutcome,
    ) -> None:
        for info in extraction.page_infos:
            geometry = extraction.geometry_by_page.get(info.index)
            lines = extraction.lines_by_page.get(info.index, [])
            warnings = list(info.reasons)
            source_kind = "scanned" if info.likely_scanned else "text"

            if info.likely_scanned and lines:
                source_kind = "mixed"
            if not lines:
                # A page with no text is recorded and named, never dropped.
                warnings.append(
                    "no text layer on this page"
                    + ("; OCR is required" if info.likely_scanned else "")
                )

            session.add(
                Page(
                    id=f"pg_{revision.id}_{info.index}",
                    document_id=document.id,
                    revision_id=revision.id,
                    page_index=info.index,
                    printed_label=None,
                    width_pt=geometry.media_width if geometry else info.width_pt,
                    height_pt=geometry.media_height if geometry else info.height_pt,
                    rotation=geometry.rotation if geometry else info.rotation,
                    crop=list(geometry.crop.as_tuple()) if geometry and geometry.crop else None,
                    dpi=72.0,
                    source_kind=source_kind,
                    # PDF.js renders pages in the browser, so no preview is stored.
                    preview_path=None,
                    thumbnail_path=None,
                    text_chars=info.text_chars,
                    ocr_applied=False,
                    warnings=warnings,
                )
            )
            outcome.pages += 1
            self.queue.upsert_unit(
                session,
                lease,
                unit_key=f"page:{info.index}",
                unit_type="page",
                page_index=info.index,
                status="succeeded",
                usage={"text_chars": info.text_chars, "lines": len(lines)},
            )

    # -- blocks ------------------------------------------------------------

    def _persist_blocks(
        self,
        session: Session,
        lease: LeaseInfo,
        document: Document,
        revision: ParseRevision,
        extraction: PdfExtraction,
        layout: LayoutResult,
        outcome: ParseOutcome,
    ) -> None:
        body_size = body_font_size(layout.paragraphs)

        # Furniture is ordered after the body so it never takes a body order slot, but it
        # is still stored: a running head is worth showing and worth knowing about.
        ordered: list[tuple[Paragraph, bool]] = [(p, False) for p in layout.paragraphs]
        ordered.extend((p, True) for p in layout.furniture)

        for order, (paragraph, _is_furniture) in enumerate(ordered):
            page_index = paragraph.page_indices[0] if paragraph.page_indices else 0
            decision = classify_paragraph(
                paragraph, position=order, page_index=page_index, body_size=body_size
            )

            block = self._build_block(
                revision=revision,
                document=document,
                paragraph=paragraph,
                decision=decision,
                order=order,
                page_index=page_index,
                extraction=extraction,
            )
            session.add(block)
            outcome.blocks += 1
            if decision.type.is_translatable:
                outcome.translatable_blocks += 1
            if decision.type.is_body:
                outcome.body_blocks += 1

            self._persist_spans(session, revision, block, paragraph, extraction)

        self.queue.upsert_unit(
            session,
            lease,
            unit_key="blocks",
            unit_type="document",
            status="succeeded",
            usage={"blocks": outcome.blocks, "translatable": outcome.translatable_blocks},
        )

    def _build_block(
        self,
        *,
        revision: ParseRevision,
        document: Document,
        paragraph: Paragraph,
        decision,
        order: int,
        page_index: int,
        extraction: PdfExtraction,
    ) -> Block:
        clean = paragraph.text
        raw = paragraph.raw_text or paragraph.text
        # Ids are derived from the revision and the reading order, so they are stable
        # within a revision and different in the next one.  That is what lets a re-parse
        # be recognised as a new revision rather than silently reusing anchors.
        digest = hashlib.sha1(
            f"{revision.id}|{page_index}|{order}|{clean}".encode("utf-8")
        ).hexdigest()[:20]
        text_hash = hashlib.sha1(clean.encode("utf-8")).hexdigest()[:40]

        return Block(
            id=f"blk_{digest}",
            block_key=f"{page_index}:{order:05d}",
            document_id=document.id,
            revision_id=revision.id,
            page_index=page_index,
            order_index=order,
            type=decision.type.value,
            raw_text=raw,
            clean_text=clean,
            text_hash=text_hash,
            ocr_revision=0,
            confidence_score=decision.confidence.score,
            confidence_level=decision.confidence.level.value
            if isinstance(decision.confidence.level, ConfidenceLevel)
            else str(decision.confidence.level),
            confidence_source=decision.confidence.source,
            section_id=paragraph.section_id,
            section_path=list(paragraph.section_path or []),
            level=paragraph.level,
            table_data=None,
            extra={
                "reasons": decision.reasons,
                # A paragraph that runs across a page boundary is flagged, because an
                # anchor for it is page-level and must not be described as an exact
                # paragraph position.
                "cross_page": len(paragraph.page_indices) > 1,
                "font_size": paragraph.font_size,
                "bold": paragraph.bold,
            },
        )

    def _persist_spans(
        self,
        session: Session,
        revision: ParseRevision,
        block: Block,
        paragraph: Paragraph,
        extraction: PdfExtraction,
    ) -> None:
        """One span per source line, which is what a highlight is drawn from.

        ``text_start``/``text_end`` are offsets into **``raw_text``**, not ``clean_text``:
        cleaning joins hyphenated words and normalises punctuation, so offsets into the
        cleaned text cannot be derived from the lines without tracking the transformation.
        Claiming them for ``clean_text`` would put highlights in the wrong place.
        """
        offset = 0
        for ordinal, line in enumerate(paragraph.lines):
            geometry = extraction.geometry_by_page.get(line.page_index)
            raw_box = None
            if geometry is not None:
                try:
                    raw_box = geometry.norm_to_raw_box(line.bbox)
                except Exception:  # pragma: no cover - defensive, geometry is validated
                    raw_box = None
            text = line.text or ""
            session.add(
                SourceSpanRow(
                    # The id is left to the database: source_spans uses an autoincrement
                    # surrogate key (unlike block/page/revision, which carry meaningful
                    # string ids), and a span is addressed by its block and ordinal.
                    block_id=block.id,
                    revision_id=revision.id,
                    page_index=line.page_index,
                    ordinal=ordinal,
                    bbox=list(line.bbox.as_tuple()),
                    raw_bbox=list(raw_box.as_tuple()) if raw_box else None,
                    raw_space="raw_pdf",
                    transform={},
                    extra_transforms=[],
                    polygon=None,
                    text_start=offset,
                    text_end=offset + len(text),
                )
            )
            offset += len(text) + 1  # the space the join inserted

    # -- completion --------------------------------------------------------

    def _finish_revision(
        self,
        session: Session,
        document: Document,
        revision: ParseRevision,
        outcome: ParseOutcome,
    ) -> None:
        revision.status = outcome.status
        revision.finished_at = utcnow()
        revision.warnings = list(dict.fromkeys(outcome.warnings))
        revision.issues = outcome.issues
        revision.stats = {
            "pages": outcome.pages,
            "blocks": outcome.blocks,
            "translatable_blocks": outcome.translatable_blocks,
            "body_blocks": outcome.body_blocks,
        }


__all__ = ["PARSER_NAME", "PARSER_VERSION", "SUPPORTED_FORMATS", "ParseOutcome", "ParseService"]
