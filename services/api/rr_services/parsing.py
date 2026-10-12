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
from pathlib import Path

from sqlalchemy.orm import Session

from document_core.blocks import body_font_size, classify_paragraph
from document_core.layout import LayoutResult, Paragraph, analyse
from document_core.models import ConfidenceLevel, ParseStatus, BlockType, Confidence
from document_core.blocks import TypeDecision
from rr_adapters.pdf_extract import (
    PdfExtraction,
    PdfTextExtractor,
    render_page_images,
)
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
PARSER_VERSION = "1.0.5"

#: Formats whose text can be read today.  An image needs OCR and a DOCX needs conversion;
#: both adapters exist but are not wired into this stage yet, and saying so plainly is
#: better than a parse that quietly produces nothing.
SUPPORTED_FORMATS = {"pdf"}

#: Page images are rendered here, not in the browser (ADR 0016).  120 DPI is the value the
#: recorded first-screen and page-turn numbers were measured at, so those figures still
#: describe this path rather than a different one.
PREVIEW_DPI = 120
THUMBNAIL_MAX_WIDTH = 180


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

        self.queue.set_stage(session, lease, "layout", "ordering blocks")
        layout = analyse(extraction.lines_by_page)

        outcome = ParseOutcome(revision_id="", status=ParseStatus.SUCCEEDED.value)
        outcome.warnings.extend(extraction.warnings)
        outcome.warnings.extend(layout.warnings)
        grobid_types = {}
        if self.settings.grobid_url:
            from rr_adapters.grobid import GrobidClient
            from .grobid_assist import apply_structure
            import httpx
            self.queue.set_stage(session, lease, "layout", "checking optional GROBID structure")
            # 网络等待不持有 SQLite 写锁；活动修订仍然保持原值。
            session.commit()
            try:
                auxiliary = GrobidClient(self.settings.grobid_url, self.settings.grobid_timeout_seconds).analyse(source)
                grobid_types = apply_structure(layout, auxiliary)
                outcome.stats["grobid"] = {"status":"ok","matched_blocks":len(grobid_types),"hints":len(auxiliary.hints)}
            except (OSError, ValueError, httpx.HTTPError) as error:
                outcome.warnings.append(f"GROBID 辅助解析不可用（{type(error).__name__}），已使用原解析结果。")
                outcome.stats["grobid"] = {"status":"fallback","matched_blocks":0}
        else:
            outcome.stats["grobid"] = {"status":"disabled","matched_blocks":0}

        revision = self._create_revision(session, document, extraction)
        outcome.revision_id = revision.id
        self.queue.set_stage(session, lease, "persist", "saving pages and blocks")
        self._persist_pages(session, lease, document, revision, extraction, outcome)
        self._persist_blocks(session, lease, document, revision, extraction, layout, outcome, grobid_types)

        # Rendering runs after the pages exist so a failure here attaches to a page that is
        # already readable.  It is its own stage because it is the slowest part and because a
        # page image that cannot be produced must not cost the reader the text.
        self.queue.set_stage(session, lease, "render", "rendering page images")
        self._render_page_images(session, lease, document, revision, source, outcome)

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

    def _render_page_images(
        self,
        session: Session,
        lease: LeaseInfo,
        document: Document,
        revision: ParseRevision,
        source: Path,
        outcome: ParseOutcome,
    ) -> None:
        """Render each page to a preview and a thumbnail in the workspace.

        Until now this step did not exist and ``preview_path`` was written as ``None``, on the
        grounds that PDF.js drew the page in the browser.  The source pane now shows a
        server-produced image (ADR 0016), so that reasoning no longer holds and the reader would
        otherwise open a document whose original pane is blank.

        A page whose render fails keeps its text and gets its own warning and a failed unit; the
        parse is reported as partial rather than failed.  Losing the picture of a page must not
        cost the reader the page.
        """
        rendered = 0
        for index, preview, thumbnail in render_page_images(source, dpi=PREVIEW_DPI,
                                                          thumbnail_max_width=THUMBNAIL_MAX_WIDTH):
            page = session.get(Page, f"pg_{revision.id}_{index}")
            if page is None:
                continue
            unit_key = f"render:{index}"
            try:
                preview_relative = self.workspace.relative(
                    self.workspace.derived_dir(document.id, revision.id) / "pages" / f"{index:04d}.png"
                )
                thumbnail_relative = self.workspace.relative(
                    self.workspace.derived_dir(document.id, revision.id) / "pages" / f"{index:04d}.thumb.png"
                )
                self.workspace.write_bytes(preview_relative, preview)
                self.workspace.write_bytes(thumbnail_relative, thumbnail)
            except Exception as error:  # noqa: BLE001 - any render or write failure is per-page
                page.warnings = list(page.warnings or []) + [
                    f"page image could not be rendered: {type(error).__name__}: {error}"
                ]
                outcome.warnings.append(
                    f"page {index}: page image could not be rendered ({type(error).__name__})"
                )
                self.queue.upsert_unit(
                    session, lease, unit_key=unit_key, unit_type="page", page_index=index,
                    status="failed", error_code="RENDER_FAILED",
                    error_message=f"{type(error).__name__}: {error}",
                )
                outcome.status = ParseStatus.PARTIAL.value
                continue

            page.preview_path = preview_relative
            page.thumbnail_path = thumbnail_relative
            rendered += 1
            self.queue.upsert_unit(
                session, lease, unit_key=unit_key, unit_type="page", page_index=index,
                status="succeeded",
                usage={"preview_bytes": len(preview), "thumbnail_bytes": len(thumbnail)},
            )
        outcome.stats["pages_rendered"] = rendered
        session.flush()

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
        grobid_types: dict | None = None,
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
            if paragraph.lines and all(line.non_horizontal for line in paragraph.lines):
                decision = TypeDecision(BlockType.HEADER, Confidence(.95, ConfidenceLevel.HIGH, "text-direction"),
                    ["non-horizontal marginal text retained in the original page"])
            elif paragraph.kind == "list_item":
                decision = TypeDecision(BlockType.LIST_ITEM, Confidence(.95, ConfidenceLevel.HIGH, "native-list"),
                    ["keyword list item preserves its original row"])

            hint = (grobid_types or {}).get(order)
            if hint in {"heading","paragraph","abstract","caption"} and decision.type not in {
                BlockType.REFERENCE, BlockType.HEADER, BlockType.FOOTER, BlockType.PAGE_NUMBER,
                BlockType.FORMULA, BlockType.TABLE, BlockType.TABLE_CELL, BlockType.FIGURE,
            }:
                decision = TypeDecision(BlockType(hint), Confidence(.9, ConfidenceLevel.HIGH, "grobid+text"),
                    ["GROBID structure corroborated by source text"])
            block = self._build_block(
                revision=revision,
                document=document,
                paragraph=paragraph,
                decision=decision,
                order=order,
                page_index=page_index,
                extraction=extraction,
            )
            if hint:
                block.extra = {**block.extra,"grobid_type":hint}
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
                "non_horizontal": bool(paragraph.lines) and all(line.non_horizontal for line in paragraph.lines),
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
            **outcome.stats,
            "pages": outcome.pages,
            "blocks": outcome.blocks,
            "translatable_blocks": outcome.translatable_blocks,
            "body_blocks": outcome.body_blocks,
        }


__all__ = ["PARSER_NAME", "PARSER_VERSION", "SUPPORTED_FORMATS", "ParseOutcome", "ParseService"]
