"""Synthetic academic documents with machine-readable ground truth.

The M0/M1 acceptance suites need *legal, reproducible, labelled* documents
(spec 12.1: at least 24 legally usable files, with per-page/per-block manual
annotation).  Downloading real papers was not possible from this environment
and would in any case raise redistribution questions, so the corpus is
**authored here**: every paper is written from scratch and laid out by this
generator.

Two consequences worth stating plainly:

* The text is not real research.  It exercises layout, OCR and translation
  paths, but it cannot validate domain translation quality, which is why the
  translation-quality acceptance item stays PENDING until a human reviews real
  output.
* Ground truth is derived from what was *drawn*.  The generator therefore
  records coordinates at draw time, and the checks compare against
  independently computable properties (page counts, reading order, character
  coverage) rather than against the parser's own output.

Output layout under the chosen root::

    <root>/
      files/<name>.<ext>
      truth/<name>.json
      manifest.json
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas

from rr_adapters.fonts import draw as draw_text_run
from rr_adapters.fonts import has_cjk, measure, register_pdf_fonts

__all__ = [
    "PAGE_W",
    "PAGE_H",
    "LayoutKind",
    "LineSpec",
    "BlockSpec",
    "PageSpec",
    "DocumentSpec",
    "ContentItem",
    "TextPdfBuilder",
]

# A4 in PostScript points, the page size used throughout the corpus.
PAGE_W = 595.276
PAGE_H = 841.89
MARGIN_X = 56.0
MARGIN_TOP = 64.0
MARGIN_BOTTOM = 64.0
COLUMN_GUTTER = 18.0
BODY_FONT = "Times-Roman"
BODY_BOLD = "Times-Bold"
BODY_ITALIC = "Times-Italic"
BODY_SIZE = 10.0
LINE_LEADING = 12.4
HEADING_SIZE = 12.5
TITLE_SIZE = 17.0
CAPTION_SIZE = 9.0
FOOTNOTE_SIZE = 8.0


class LayoutKind(str, Enum):
    SINGLE = "single_column"
    DOUBLE = "double_column"


@dataclass
class LineSpec:
    """One drawn line of text, with the page-space box it occupies."""

    text: str
    x0: float
    y0: float  # bottom edge in PDF space (y grows upwards)
    x1: float
    y1: float
    font: str
    size: float
    block_index: int

    @property
    def bbox(self) -> list[float]:
        return [round(self.x0, 3), round(self.y0, 3), round(self.x1, 3), round(self.y1, 3)]


@dataclass
class BlockSpec:
    """A semantic block, as laid out by the generator."""

    index: int
    type: str
    text: str
    page_index: int
    lines: list[LineSpec] = field(default_factory=list)
    reading_order: int = 0
    section_path: list[str] = field(default_factory=list)
    level: int | None = None

    @property
    def bbox(self) -> list[float]:
        if not self.lines:
            return [0.0, 0.0, 0.0, 0.0]
        return [
            round(min(ln.x0 for ln in self.lines), 3),
            round(min(ln.y0 for ln in self.lines), 3),
            round(max(ln.x1 for ln in self.lines), 3),
            round(max(ln.y1 for ln in self.lines), 3),
        ]

    @property
    def page_indices(self) -> list[int]:
        seen: list[int] = []
        for line in self.lines:
            idx = getattr(line, "page_index", self.page_index)
            if idx not in seen:
                seen.append(idx)
        return seen or [self.page_index]

    @property
    def text_lines(self) -> list[str]:
        """The text of every drawn line, in draw order.

        This is the record of what actually appears on the page, which is not
        always ``text``: an abstract is drawn with an ``Abstract-`` prefix that
        the semantic text does not carry, and a reference is drawn with its
        number appended.
        """
        return [line.text for line in self.lines]

    def text_on_page(self, page_index: int) -> str:
        """The text this block contributes to one page.

        A paragraph that flows across a page or column boundary contributes only
        its visible lines to each page.  Recording the whole paragraph text on
        every page it touches would credit the document with text that is not on
        that page, which breaks any per-page coverage or OCR comparison.
        """
        parts = [
            line.text
            for line in self.lines
            if getattr(line, "page_index", self.page_index) == page_index
        ]
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "type": self.type,
            "text": self.text,
            "page_index": self.page_index,
            "page_indices": self.page_indices,
            "reading_order": self.reading_order,
            "bbox": self.bbox,
            "line_bboxes": [ln.bbox for ln in self.lines],
            "line_texts": self.text_lines,
            "line_page_indices": [
                getattr(ln, "page_index", self.page_index) for ln in self.lines
            ],
            "n_lines": len(self.lines),
            "section_path": self.section_path,
            "level": self.level,
        }


@dataclass
class PageSpec:
    index: int
    blocks: list[BlockSpec] = field(default_factory=list)
    rotation: int = 0
    source_kind: str = "text"
    figure_boxes: list[list[float]] = field(default_factory=list)
    table_boxes: list[list[float]] = field(default_factory=list)
    header_text: str | None = None
    footer_text: str | None = None
    printed_label: str | None = None
    has_formula: bool = False
    #: Text drawn on the page that belongs to no block, such as an equation
    #: number.  Recorded so per-page text comparison does not mistake it for a
    #: recognition error.
    decorations: list[str] = field(default_factory=list)

    def drawn_lines(self, page_index: int) -> list[str]:
        """Every visual line drawn on one page, in reading order.

        A paragraph is drawn as many short lines, but ``text_on_page`` returns the
        paragraph as one string.  Reporting only the flattened form made the
        reading-order metric meaningless: a two-column page has around 96 drawn
        lines, and a reference of one line per paragraph can never be aligned
        against them, so the metric could not distinguish correct order from
        scrambled order.  This exposes the drawn line structure that the metric
        needs.

        Order is the page's block reading order, then the lines within each block,
        then any decorations.  That is the order the lines were actually drawn in.
        """
        lines: list[str] = []
        for block in self.blocks:
            for line in block.lines:
                # A block's lines carry their own page index; the fallback is the
                # page being described, not the block's page.  `self` here is a
                # PageSpec, which has `index` and no `page_index`.
                if getattr(line, "page_index", self.index) != page_index:
                    continue
                text = line.text
                if text.strip():
                    lines.append(text)
        lines.extend(part for part in self.decorations if part.strip())
        return lines

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "rotation": self.rotation,
            "source_kind": self.source_kind,
            "printed_label": self.printed_label,
            "header_text": self.header_text,
            "footer_text": self.footer_text,
            "has_formula": self.has_formula,
            "figure_boxes": self.figure_boxes,
            "table_boxes": self.table_boxes,
            "blocks": [b.to_dict() for b in self.blocks],
            # What is actually drawn on this page, per block.  This differs from
            # the block's semantic text for blocks that span pages and for
            # blocks whose drawn text carries decoration (Abstract- prefixes,
            # reference numbers).  Per-page comparisons must use this.
            "visible_blocks": [
                {
                    "index": b.index,
                    "type": b.type,
                    "text": b.text_on_page(self.index),
                    "page_index": self.index,
                }
                for b in self.blocks
                if b.text_on_page(self.index).strip()
            ],
            # The drawn lines, in reading order.  This is the reference for
            # reading-order scoring; `visible_text` is the same content flattened
            # for character-level comparison, and must not be used for order.
            "visible_lines": self.drawn_lines(self.index),
            "visible_text": " ".join(
                part
                for part in (
                    [
                        b.text_on_page(self.index)
                        for b in self.blocks
                        if b.text_on_page(self.index).strip()
                    ]
                    + list(self.decorations)
                )
                if part.strip()
            ),
            "decorations": self.decorations,
        }


@dataclass
class DocumentSpec:
    name: str
    title: str
    authors: str
    layout: LayoutKind
    pages: list[PageSpec] = field(default_factory=list)
    columns: int = 1
    language: str = "en"
    notes: str = ""
    has_formula: bool = False
    has_table: bool = False
    has_figure: bool = False
    has_references: bool = True
    has_footnotes: bool = False
    cross_page_paragraph: bool = False
    mixed_language: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "authors": self.authors,
            "layout": self.layout.value,
            "columns": self.columns,
            "page_count": len(self.pages),
            "language": self.language,
            "notes": self.notes,
            "has_formula": self.has_formula,
            "has_table": self.has_table,
            "has_figure": self.has_figure,
            "has_references": self.has_references,
            "has_footnotes": self.has_footnotes,
            "cross_page_paragraph": self.cross_page_paragraph,
            "mixed_language": self.mixed_language,
            "pages": [p.to_dict() for p in self.pages],
        }


@dataclass
class ContentItem:
    """One item of document content, laid out by :class:`TextPdfBuilder`."""

    kind: str
    text: str = ""
    level: int = 1
    height: float = 130.0
    caption: str = ""
    label: str = "Figure"
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    number: str = ""


class TextPdfBuilder:
    """Draw a document and record exactly what was drawn.

    The builder keeps a single cursor (column, then page) and every draw call
    records a :class:`LineSpec` carrying the page it landed on.  Blocks know the
    full set of pages they touch, which is what makes cross-page paragraphs
    checkable.
    """

    def __init__(self, spec: DocumentSpec, output_path: Path) -> None:
        self.spec = spec
        self.output_path = Path(output_path)
        self.blocks: list[BlockSpec] = []
        self._block_counter = 0
        self._order_counter = 0
        self._canvas: rl_canvas.Canvas | None = None
        self._page_index = 0
        self._col_index = 0
        self._cursor_y = PAGE_H - MARGIN_TOP
        self._current_section: list[str] = []
        # Registered once per builder; the report is carried into the document
        # spec so a corpus generated without CJK coverage is detectable.
        self.font_report = register_pdf_fonts()
        if self.font_report.get("cjk_available") == "false":
            warnings.warn(
                "no CJK-capable font was found: Chinese text in the corpus will be "
                "drawn with missing glyphs. Install Noto Sans SC or a system CJK font.",
                RuntimeWarning,
                stacklevel=2,
            )

    # -- geometry ----------------------------------------------------------
    @property
    def _usable(self) -> float:
        return PAGE_W - 2 * MARGIN_X

    @property
    def _col_widths(self) -> list[float]:
        if self.spec.columns == 1:
            return [self._usable]
        return [(self._usable - COLUMN_GUTTER) / 2.0] * 2

    @property
    def col_w(self) -> float:
        return self._col_widths[min(self._col_index, len(self._col_widths) - 1)]

    @property
    def x(self) -> float:
        return MARGIN_X + self._col_index * (self.col_w + COLUMN_GUTTER)

    @property
    def page_spec(self) -> PageSpec:
        while len(self.spec.pages) <= self._page_index:
            self.spec.pages.append(PageSpec(index=len(self.spec.pages)))
        return self.spec.pages[self._page_index]

    # -- measurement -------------------------------------------------------
    def wrap(self, text: str, font: str, size: float, width: float) -> list[str]:
        """Wrap text to ``width``, handling CJK and Latin scripts.

        Latin text breaks at spaces.  CJK text has no spaces between words, so a
        run of CJK characters may break between any two characters; mixing the
        two rules matters for the mixed-language corpus document, where a
        Latin-only wrapper would either overflow or emit one enormous line.
        """
        if not text:
            return []
        words = text.split()
        if not words:
            return []
        lines: list[str] = []
        current = ""
        for word in words:
            if current:
                candidate = f"{current} {word}"
                if measure(candidate, font, size) <= width:
                    current = candidate
                    continue
                lines.append(current)
                current = ""
            current = self._break_token(word, font, size, width, lines)
        if current:
            lines.append(current)
        return lines

    @staticmethod
    def _break_token(
        token: str, font: str, size: float, width: float, lines: list[str]
    ) -> str:
        """Place one whitespace-free token, splitting it if it cannot fit.

        A long Latin token is emitted whole (a URL or DOI must not be split);
        a CJK token is split between characters because that is how the script
        wraps.
        """
        if measure(token, font, size) <= width:
            return token
        if not has_cjk(token):
            return token
        current = ""
        for char in token:
            candidate = current + char
            if current and measure(candidate, font, size) > width:
                lines.append(current)
                current = char
            else:
                current = candidate
        return current

    # -- cursor ------------------------------------------------------------
    def _new_page(self) -> None:
        assert self._canvas is not None
        self._canvas.showPage()
        self._page_index += 1
        self.page_spec  # materialise the PageSpec
        self._col_index = 0
        self._cursor_y = PAGE_H - MARGIN_TOP

    def _next_column(self) -> bool:
        if self._col_index + 1 < self.spec.columns:
            self._col_index += 1
            self._cursor_y = PAGE_H - MARGIN_TOP
            return True
        return False

    def ensure_space(self, needed: float) -> None:
        while self._cursor_y - needed < MARGIN_BOTTOM:
            if not self._next_column():
                self._new_page()

    @property
    def at_page_top(self) -> bool:
        return self._cursor_y >= PAGE_H - MARGIN_TOP - 0.01

    # -- drawing -----------------------------------------------------------
    def _new_block(self, block_type: str, text: str) -> BlockSpec:
        block = BlockSpec(
            index=self._block_counter,
            type=block_type,
            text=text,
            page_index=self._page_index,
            reading_order=self._order_counter,
        )
        self._block_counter += 1
        self._order_counter += 1
        block.section_path = list(self._current_section)
        self.blocks.append(block)
        self.page_spec.blocks.append(block)
        return block

    def _rehome(self, block: BlockSpec) -> None:
        """Keep the block listed on every page it reached."""
        for page_index in block.page_indices:
            page = self.spec.pages[page_index]
            if block not in page.blocks:
                page.blocks.append(block)

    def _draw_line(
        self,
        block: BlockSpec,
        text: str,
        x: float,
        font: str,
        size: float,
        *,
        width: float,
        align: str = "left",
        container_x: float | None = None,
    ) -> None:
        assert self._canvas is not None
        line_width = measure(text, font, size)
        if align == "center" and container_x is not None:
            draw_x = container_x + (width - line_width) / 2.0
        elif align == "right" and container_x is not None:
            draw_x = container_x + width - line_width
        else:
            draw_x = x
        top = self._cursor_y
        baseline = top - size * 0.82
        draw_text_run(self._canvas, text, draw_x, baseline, font, size)
        line = LineSpec(
            text=text,
            x0=draw_x,
            y0=top - size,
            x1=draw_x + line_width,
            y1=top,
            font=font,
            size=size,
            block_index=block.index,
        )
        line.page_index = self._page_index  # type: ignore[attr-defined]
        block.lines.append(line)
        self._cursor_y -= size + (size * 0.24)

    def _emit_paragraph(self, block: BlockSpec, text: str, font: str, size: float) -> None:
        """Flow a paragraph across columns and pages, recording each line.

        Line breaking uses :meth:`wrap`, which handles both Latin and CJK.  A
        long CJK paragraph has no spaces, so a word-splitting loop would emit a
        single overflowing line; the wrapper splits between characters instead.
        """
        for line in self.wrap(text, font, size, self.col_w):
            self.ensure_space(size * 1.24)
            self._draw_line(block, line, self.x, font, size, width=self.col_w)

    def _draw_figure(self, box: Sequence[float], label: str) -> None:
        c = self._canvas
        assert c is not None
        x0, y0, x1, y1 = box
        c.saveState()
        c.setStrokeColor(HexColor("#333333"))
        c.setFillColor(HexColor("#f2f4f7"))
        c.setLineWidth(0.6)
        c.rect(x0, y0, x1 - x0, y1 - y0, stroke=1, fill=1)
        c.setStrokeColor(HexColor("#1f6feb"))
        c.setLineWidth(1.2)
        points: list[tuple[float, float]] = []
        n = 40
        for i in range(n):
            px = x0 + 12 + (x1 - x0 - 24) * i / (n - 1)
            value = math.sin(i / 3.0) * 0.35 + 0.5 + (i / n) * 0.25
            py = y0 + 12 + (y1 - y0 - 24) * min(0.95, max(0.05, value))
            points.append((px, py))
        for i in range(1, len(points)):
            c.line(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
        c.setFillColor(HexColor("#d1242f"))
        for px, py in points[::8]:
            c.circle(px, py, 1.6, stroke=0, fill=1)
        c.setFillColor(HexColor("#333333"))
        c.setFont("Helvetica", 6.5)
        c.drawString(x0 + 6, y1 - 10, f"{label}: synthetic data series")
        c.drawString(x0 + 6, y0 + 4, "x axis: time (s)")
        c.restoreState()

    def _draw_furniture(self) -> None:
        """Running header and footer for the page just finished."""
        c = self._canvas
        assert c is not None
        page = self.page_spec
        c.saveState()
        c.setFillColor(HexColor("#666666"))
        if page.header_text:
            draw_text_run(c, page.header_text, MARGIN_X, PAGE_H - 40, BODY_FONT, 7.5)
        if page.footer_text:
            width = measure(page.footer_text, BODY_FONT, 8.0)
            draw_text_run(
                c, page.footer_text, (PAGE_W - width) / 2.0, 34, BODY_FONT, 8.0
            )
        c.restoreState()

    # -- main --------------------------------------------------------------
    def build(self, content: Sequence[ContentItem]) -> DocumentSpec:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        c = rl_canvas.Canvas(str(self.output_path), pagesize=(PAGE_W, PAGE_H))
        self._canvas = c
        c.setTitle(self.spec.title)
        c.setAuthor(self.spec.authors)
        c.setCreator("research-reader corpus generator (synthetic, authored)")
        c.setSubject("Synthetic academic document for parser/OCR acceptance testing")

        # Title block (always full width).
        title_block = self._new_block("title", self.spec.title)
        for line in self.wrap(self.spec.title, BODY_BOLD, TITLE_SIZE, self._usable):
            self.ensure_space(TITLE_SIZE * 1.3)
            self._draw_line(
                title_block, line, MARGIN_X, BODY_BOLD, TITLE_SIZE,
                width=self._usable, align="center", container_x=MARGIN_X,
            )
        self._cursor_y -= 6.0
        author_block = self._new_block("author", self.spec.authors)
        for line in self.wrap(self.spec.authors, BODY_FONT, BODY_SIZE + 0.5, self._usable):
            self.ensure_space((BODY_SIZE + 0.5) * 1.3)
            self._draw_line(
                author_block, line, MARGIN_X, BODY_FONT, BODY_SIZE + 0.5,
                width=self._usable, align="center", container_x=MARGIN_X,
            )
        self._cursor_y -= 10.0
        self.page_spec.header_text = self.spec.title[:44]
        self.page_spec.footer_text = "1"
        self.page_spec.printed_label = "1"

        for item in content:
            handler = getattr(self, f"_item_{item.kind}", None)
            if handler is None:
                raise ValueError(f"unsupported content kind: {item.kind}")
            handler(item)

        # Finish the final page.
        self._draw_furniture()
        c.showPage()
        c.save()
        for block in self.blocks:
            self._rehome(block)
        return self.spec

    # -- item handlers -----------------------------------------------------
    def _item_pagebreak(self, item: ContentItem) -> None:
        self._draw_furniture()
        assert self._canvas is not None
        self._canvas.showPage()
        self._page_index += 1
        self.page_spec  # materialise
        self._col_index = 0
        self._cursor_y = PAGE_H - MARGIN_TOP
        self.page_spec.footer_text = str(self._page_index + 1)
        self.page_spec.printed_label = str(self._page_index + 1)
        if not self.page_spec.header_text:
            self.page_spec.header_text = self.spec.title[:44]

    def _item_heading(self, item: ContentItem) -> None:
        size = HEADING_SIZE if item.level == 1 else BODY_SIZE + 0.8
        if item.level == 1:
            self._current_section = [item.text]
        else:
            self._current_section = self._current_section[: item.level - 1] + [item.text]
        self.ensure_space(size * 1.4 + 12)
        if not self.at_page_top:
            self._cursor_y -= 8.0
        block = self._new_block("heading", item.text)
        block.level = item.level
        for line in self.wrap(item.text, BODY_BOLD, size, self.col_w):
            self.ensure_space(size * 1.4)
            self._draw_line(block, line, self.x, BODY_BOLD, size, width=self.col_w)
        self._cursor_y -= 3.0

    def _item_paragraph(self, item: ContentItem) -> None:
        block = self._new_block("paragraph", item.text)
        self._emit_paragraph(block, item.text, BODY_FONT, BODY_SIZE)
        self._cursor_y -= 4.5

    def _item_abstract(self, item: ContentItem) -> None:
        self.ensure_space(LINE_LEADING * 4)
        block = self._new_block("abstract", item.text)
        width = max(40.0, self.col_w - 16.0)
        lines = self.wrap(item.text, BODY_ITALIC, BODY_SIZE - 0.5, width)
        if lines:
            lines = ["Abstract\u2014" + lines[0]] + lines[1:]
        for line in lines:
            self.ensure_space((BODY_SIZE - 0.5) * 1.3)
            self._draw_line(
                block, line, self.x + 8, BODY_ITALIC, BODY_SIZE - 0.5, width=width
            )
        self._cursor_y -= 7.0

    def _item_formula(self, item: ContentItem) -> None:
        self.ensure_space(26.0)
        block = self._new_block("formula", item.text)
        self.page_spec.has_formula = True
        self.spec.has_formula = True
        size = BODY_SIZE + 1
        self.ensure_space(size * 1.4)
        self._draw_line(
            block, item.text, self.x, BODY_ITALIC, size,
            width=self.col_w, align="center", container_x=self.x,
        )
        if item.number:
            assert self._canvas is not None
            width = measure(item.number, BODY_FONT, BODY_SIZE)
            draw_text_run(
                self._canvas,
                item.number,
                self.x + self.col_w - width,
                self._cursor_y + 4,
                BODY_FONT,
                BODY_SIZE,
            )
            # The equation number is drawn but belongs to no block.  It is
            # recorded as page decoration so that per-page text comparison
            # accounts for it instead of treating it as a recognition error.
            self.page_spec.decorations.append(item.number)
        self._cursor_y -= 14.0

    def _item_figure(self, item: ContentItem) -> None:
        height = item.height
        self.ensure_space(height + 34.0)
        block = self._new_block("figure", item.caption)
        self.spec.has_figure = True
        box = (self.x, self._cursor_y - height, self.x + self.col_w, self._cursor_y)
        self.page_spec.figure_boxes.append([round(v, 3) for v in box])
        self._draw_figure(box, item.label)
        self._cursor_y -= height + 5.0
        caption_block = self._new_block("caption", item.caption)
        for line in self.wrap(item.caption, BODY_FONT, CAPTION_SIZE, self.col_w):
            self.ensure_space(CAPTION_SIZE * 1.3)
            self._draw_line(caption_block, line, self.x, BODY_FONT, CAPTION_SIZE, width=self.col_w)
        self._cursor_y -= 7.0

    def _item_table(self, item: ContentItem) -> None:
        rows = [item.header] + item.rows
        col_count = max(len(r) for r in rows)
        row_h = 13.0
        height = row_h * len(rows)
        self.ensure_space(height + 40.0)
        self.spec.has_table = True
        table_block = self._new_block("table", item.caption)
        assert self._canvas is not None
        self.page_spec.table_boxes.append(
            [round(self.x, 3), round(self._cursor_y - height, 3), round(self.x + self.col_w, 3), round(self._cursor_y, 3)]
        )
        caption_block = self._new_block("caption", item.caption)
        for line in self.wrap(item.caption, BODY_BOLD, CAPTION_SIZE, self.col_w):
            self.ensure_space(CAPTION_SIZE * 1.3)
            self._draw_line(caption_block, line, self.x, BODY_BOLD, CAPTION_SIZE, width=self.col_w)
        self._cursor_y -= 3.0
        cell_w = self.col_w / col_count
        for r_index, row in enumerate(rows):
            font = BODY_BOLD if r_index == 0 else BODY_FONT
            for c_index in range(col_count):
                value = str(row[c_index]) if c_index < len(row) else ""
                x0 = self.x + c_index * cell_w
                self._canvas.setStrokeColor(HexColor("#888888"))
                self._canvas.setLineWidth(0.4)
                self._canvas.rect(
                    x0, self._cursor_y - row_h, cell_w, row_h, stroke=1, fill=0
                )
                self._canvas.setFillColor(HexColor("#000000"))
                shown = value[:24]
                draw_text_run(
                    self._canvas, shown, x0 + 3, self._cursor_y - row_h + 4, font, CAPTION_SIZE
                )
                cell_block = self._new_block("table_cell", shown)
                line = LineSpec(
                    text=shown,
                    x0=x0 + 3,
                    y0=self._cursor_y - row_h + 2,
                    x1=x0 + 3 + measure(shown, font, CAPTION_SIZE),
                    y1=self._cursor_y - 2,
                    font=font,
                    size=CAPTION_SIZE,
                    block_index=cell_block.index,
                )
                line.page_index = self._page_index  # type: ignore[attr-defined]
                cell_block.lines.append(line)
            self._cursor_y -= row_h
        self._cursor_y -= 9.0

    def _item_footnote(self, item: ContentItem) -> None:
        assert self._canvas is not None
        self.spec.has_footnotes = True
        block = self._new_block("footnote", item.text)
        y = MARGIN_BOTTOM + 6
        draw_text_run(self._canvas, item.text, MARGIN_X, y, BODY_FONT, FOOTNOTE_SIZE)
        line = LineSpec(
            text=item.text,
            x0=MARGIN_X,
            y0=y - 2,
            x1=MARGIN_X + measure(item.text, BODY_FONT, FOOTNOTE_SIZE),
            y1=y + FOOTNOTE_SIZE,
            font=BODY_FONT,
            size=FOOTNOTE_SIZE,
            block_index=block.index,
        )
        line.page_index = self._page_index  # type: ignore[attr-defined]
        block.lines.append(line)

    def _item_reference(self, item: ContentItem) -> None:
        block = self._new_block("reference", item.text)
        width = max(40.0, self.col_w - 12.0)
        lines = self.wrap(item.text, BODY_FONT, CAPTION_SIZE, width)
        for index, line in enumerate(lines):
            self.ensure_space(CAPTION_SIZE * 1.35)
            draw_x = self.x if index == 0 else self.x + 12.0
            self._draw_line(block, line, draw_x, BODY_FONT, CAPTION_SIZE, width=width)
        self._cursor_y -= 2.0
