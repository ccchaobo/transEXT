"""DOCX conversion (spec 5.4).

A DOCX has no fixed pagination, so it cannot supply the coordinates the reading
contract needs.  The approach here matches the spec's requirement to keep *two*
representations:

* ``source_items`` -- the OOXML body structure read by ``python-docx``: order,
  style, headings, tables, images, equations and footnote text.
* a **derived PDF rendering** built by this module, which supplies page boxes and
  coordinates.  The reader anchors against the rendering; the structure supplies
  headings, table data and the conversion-fidelity report.

Why not LibreOffice: it is not installed here, and the spec requires the
converter to be verified on the target system before being relied on.  This
built-in renderer is the *documented degraded path*: it is deterministic, has no
external dependency, and states exactly which Word features it does not
reproduce.  The adapter interface accepts an external converter, so a verified
LibreOffice install can replace it without touching the rest of the system.

Known fidelity limits are reported per document as structured issues rather than
being glossed over: font substitution, floating objects, text boxes, revision
marks and true pagination are all listed.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from docx import Document as DocxDocument
from docx.document import Document as DocxDocumentType
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas

from rr_adapters.fonts import draw as draw_text_run
from rr_adapters.fonts import measure, register_pdf_fonts

__all__ = [
    "DocxItem",
    "DocxConversion",
    "DocxConverter",
    "ConversionCapability",
]

#: Page geometry for the derived rendering.  A4 with 1 inch margins.
RENDER_PAGE_W = 595.276
RENDER_PAGE_H = 841.89
RENDER_MARGIN_X = 72.0
RENDER_MARGIN_TOP = 72.0
RENDER_MARGIN_BOTTOM = 72.0


class ConversionCapability:
    """Names for the fidelity report, so the UI can group warnings."""

    HEADINGS = "headings"
    PARAGRAPHS = "paragraphs"
    LISTS = "lists"
    TABLES = "tables"
    IMAGES = "images"
    EQUATIONS = "equations"
    FOOTNOTES = "footnotes"
    TEXT_BOXES = "text_boxes"
    FLOATING_OBJECTS = "floating_objects"
    REVISION_MARKS = "revision_marks"
    PAGINATION = "pagination"
    FONTS = "fonts"


@dataclass
class DocxItem:
    """One body element, in document order."""

    order: int
    kind: str                  # paragraph | heading | list_item | table | image | equation | footnote
    text: str
    style: str | None = None
    level: int | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)
    table: list[list[str]] | None = None
    image_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "kind": self.kind,
            "text": self.text,
            "style": self.style,
            "level": self.level,
            "runs": self.runs,
            "table": self.table,
            "image_index": self.image_index,
            "metadata": self.metadata,
        }


@dataclass
class DocxConversion:
    """Everything the pipeline needs from a DOCX."""

    source_items: list[DocxItem]
    derived_pdf: Path
    page_count: int
    images: dict[int, bytes] = field(default_factory=dict)
    capabilities: dict[str, str] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    converter: str = "builtin"
    converter_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "converter": self.converter,
            "converter_version": self.converter_version,
            "derived_pdf": str(self.derived_pdf),
            "page_count": self.page_count,
            "capabilities": self.capabilities,
            "issues": self.issues,
            "stats": self.stats,
            "source_items": [item.to_dict() for item in self.source_items],
        }


class DocxConverter:
    """Read a DOCX and produce both representations."""

    def __init__(self, *, max_archive_ratio: float = 200.0, max_uncompressed_bytes: int = 400 * 1024 * 1024) -> None:
        self.max_archive_ratio = max_archive_ratio
        self.max_uncompressed_bytes = max_uncompressed_bytes

    # -- safety ------------------------------------------------------------
    def inspect_archive(self, path: Path) -> dict[str, Any]:
        """Check the archive *before* extracting anything (spec 11).

        A small DOCX that expands enormously is a decompression bomb: the ratio
        and the absolute size are both checked, because a large legitimate
        document also has a large uncompressed size.
        """
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            compressed = 0
            uncompressed = 0
            for info in archive.infolist():
                uncompressed += info.file_size
                compressed += info.compress_size
        ratio = uncompressed / max(1, compressed)
        return {
            "parts": len(names),
            "compressed_bytes": compressed,
            "uncompressed_bytes": uncompressed,
            "ratio": ratio,
            "bomb": ratio > self.max_archive_ratio or uncompressed > self.max_uncompressed_bytes,
            "names": names,
        }

    # -- main --------------------------------------------------------------
    def convert(self, path: str | Path, output_pdf: Path) -> DocxConversion:
        path = Path(path)
        report = self.inspect_archive(path)
        if report["bomb"]:
            raise DecompressionBomb(
                f"archive expands {report['ratio']:.0f}x to "
                f"{report['uncompressed_bytes'] / 1024 / 1024:.0f} MiB"
            )

        document = DocxDocument(str(path))
        items, images, issues = self._read_body(document, path)
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        page_count, render_stats = self._render(items, images, output_pdf)

        capabilities = {
            ConversionCapability.HEADINGS: "reproduced",
            ConversionCapability.PARAGRAPHS: "reproduced",
            ConversionCapability.LISTS: "approximated",
            ConversionCapability.TABLES: "reproduced",
            ConversionCapability.IMAGES: "reproduced",
            ConversionCapability.EQUATIONS: "approximated",
            ConversionCapability.FOOTNOTES: "approximated",
            ConversionCapability.TEXT_BOXES: "not_reproduced",
            ConversionCapability.FLOATING_OBJECTS: "not_reproduced",
            ConversionCapability.REVISION_MARKS: "not_reproduced",
            ConversionCapability.PAGINATION: "derived",
            ConversionCapability.FONTS: "substituted",
        }
        issues.extend(_capability_issues(capabilities))
        if _has_element(document, "w:ins") or _has_element(document, "w:del"):
            issues.append(
                {
                    "code": "docx_revision_marks_ignored",
                    "severity": "warning",
                    "message": (
                        "The document contains tracked changes. The rendering shows the "
                        "current text only; insertions, deletions and comments are not "
                        "displayed."
                    ),
                    "details": {},
                }
            )
        stats = {
            "source_items": len(items),
            "images": len(images),
            "paragraphs": sum(1 for i in items if i.kind in ("paragraph", "heading", "list_item")),
            "tables": sum(1 for i in items if i.kind == "table"),
            "equations": sum(1 for i in items if i.kind == "equation"),
            "footnotes": sum(1 for i in items if i.kind == "footnote"),
            "archive": {k: v for k, v in report.items() if k != "names"},
            **render_stats,
        }
        return DocxConversion(
            source_items=items,
            derived_pdf=output_pdf,
            page_count=page_count,
            images=images,
            capabilities=capabilities,
            issues=issues,
            stats=stats,
        )

    # -- body reading ------------------------------------------------------
    def _read_body(
        self, document: DocxDocumentType, path: Path
    ) -> tuple[list[DocxItem], dict[int, bytes], list[dict[str, Any]]]:
        items: list[DocxItem] = []
        images: dict[int, bytes] = {}
        issues: list[dict[str, Any]] = []
        order = 0

        for element in _iter_block_elements(document):
            if isinstance(element, DocxParagraph):
                item = self._paragraph_item(element, order)
                if item is None:
                    continue
                items.append(item)
                order += 1
            elif isinstance(element, DocxTable):
                items.append(self._table_item(element, order))
                order += 1

        # Images: read every relationship target that is an image part.
        image_index = 0
        for rel in document.part.rels.values():
            if "image" not in rel.reltype:
                continue
            try:
                images[image_index] = rel.target_part.blob
                image_index += 1
            except Exception as exc:  # pragma: no cover - defensive
                issues.append(
                    {
                        "code": "docx_image_unreadable",
                        "severity": "warning",
                        "message": f"could not read an embedded image: {type(exc).__name__}",
                        "details": {},
                    }
                )

        # Attach an image reference to the drawing positions, in order.
        drawing_positions = [i for i, item in enumerate(items) if item.kind == "image"]
        for position, item_index in enumerate(drawing_positions):
            if position < len(images):
                items[item_index].image_index = position

        # Footnotes live in a separate part.
        footnotes = self._read_footnotes(path)
        for text in footnotes:
            items.append(
                DocxItem(order=order, kind="footnote", text=text, style="FootnoteText")
            )
            order += 1
        if footnotes:
            issues.append(
                {
                    "code": "docx_footnotes_approximated",
                    "severity": "info",
                    "message": (
                        f"{len(footnotes)} footnote(s) were read from the footnotes part and "
                        "appended at the end of the rendered text; their original position on "
                        "a page is not reproduced."
                    ),
                    "details": {"count": len(footnotes)},
                }
            )
        return items, images, issues

    def _paragraph_item(self, paragraph: DocxParagraph, order: int) -> DocxItem | None:
        text = paragraph.text or ""
        xml = paragraph._p  # noqa: SLF001 - python-docx has no public accessor
        has_math = xml.findall(".//" + qn("m:oMath")) or xml.findall(".//" + qn("m:oMathPara"))
        has_drawing = bool(xml.findall(".//" + qn("w:drawing"))) or bool(
            xml.findall(".//" + qn("w:pict"))
        )
        style_name = paragraph.style.name if paragraph.style is not None else None

        if has_math and not text.strip():
            math_text = "".join(
                node.text or "" for node in xml.findall(".//" + qn("m:t"))
            )
            return DocxItem(
                order=order,
                kind="equation",
                text=math_text.strip(),
                style=style_name,
                metadata={"engine": "oMath"},
            )
        if has_drawing and not text.strip():
            return DocxItem(
                order=order,
                kind="image",
                text="",
                style=style_name,
                metadata={},
            )
        if not text.strip() and not has_drawing:
            return None

        level = _heading_level(style_name)
        kind = "heading" if level else "paragraph"
        if not level and _is_list_style(style_name):
            kind = "list_item"
        runs = [
            {
                "text": run.text,
                "bold": bool(run.bold),
                "italic": bool(run.italic),
                "size": run.font.size.pt if run.font.size else None,
                "name": run.font.name,
            }
            for run in paragraph.runs
            if run.text
        ]
        return DocxItem(
            order=order,
            kind=kind,
            text=text,
            style=style_name,
            level=level,
            runs=runs,
            metadata={"list": _list_marker(paragraph) if kind == "list_item" else None},
        )

    def _table_item(self, table: DocxTable, order: int) -> DocxItem:
        rows: list[list[str]] = []
        for row in table.rows:
            rows.append([cell.text.strip() for cell in row.cells])
        return DocxItem(
            order=order,
            kind="table",
            text=" | ".join(" | ".join(r) for r in rows[:1]),
            style=table.style.name if table.style is not None else None,
            table=rows,
            metadata={"n_rows": len(rows), "n_cols": max((len(r) for r in rows), default=0)},
        )

    def _read_footnotes(self, path: Path) -> list[str]:
        try:
            with zipfile.ZipFile(path) as archive:
                if "word/footnotes.xml" not in archive.namelist():
                    return []
                xml = archive.read("word/footnotes.xml").decode("utf-8", "replace")
        except Exception:
            return []
        out: list[str] = []
        for match in re.finditer(r"<w:footnote\b[^>]*>(.*?)</w:footnote>", xml, re.S):
            body = match.group(1)
            if 'w:type="separator"' in match.group(0) or 'w:type="continuationSeparator"' in match.group(0):
                continue
            text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", body, re.S))
            text = _unescape_xml(text).strip()
            if text:
                out.append(text)
        return out

    # -- rendering ---------------------------------------------------------
    def _render(
        self,
        items: Sequence[DocxItem],
        images: dict[int, bytes],
        output_pdf: Path,
    ) -> tuple[int, dict[str, Any]]:
        canvas = rl_canvas.Canvas(
            str(output_pdf), pagesize=(RENDER_PAGE_W, RENDER_PAGE_H)
        )
        canvas.setTitle("Derived rendering (research-reader DOCX converter)")
        canvas.setCreator("research-reader builtin DOCX renderer 1.0.0")
        font_report = register_pdf_fonts()
        usable = RENDER_PAGE_W - 2 * RENDER_MARGIN_X
        cursor = RENDER_PAGE_H - RENDER_MARGIN_TOP
        page_index = 0
        pages = 1
        rendered_images = 0
        rendered_tables = 0
        rendered_equations = 0

        def new_page() -> None:
            nonlocal cursor, page_index, pages
            canvas.showPage()
            page_index += 1
            pages += 1
            cursor = RENDER_PAGE_H - RENDER_MARGIN_TOP

        def ensure(needed: float) -> None:
            nonlocal cursor
            while cursor - needed < RENDER_MARGIN_BOTTOM:
                new_page()

        def draw_paragraph(text: str, font: str, size: float, leading: float, indent: float = 0.0) -> None:
            nonlocal cursor
            for line in _wrap(text, font, size, usable - indent):
                ensure(leading)
                draw_text_run(canvas, line, RENDER_MARGIN_X + indent, cursor - size * 0.82, font, size)
                cursor -= leading

        for item in items:
            if item.kind == "heading":
                size = max(11.0, 14.0 - (item.level or 1) * 0.6)
                ensure(size * 2.4)
                cursor -= 6.0
                draw_paragraph(item.text, "Helvetica-Bold", size, size * 1.3)
                cursor -= 4.0
            elif item.kind == "paragraph":
                draw_paragraph(item.text, "Helvetica", 10.5, 13.2)
                cursor -= 5.0
            elif item.kind == "list_item":
                marker = item.metadata.get("list") or "\u2022"
                draw_paragraph(f"{marker} {item.text}", "Helvetica", 10.5, 13.2, indent=12.0)
                cursor -= 2.0
            elif item.kind == "equation":
                ensure(24.0)
                width = measure(item.text, "Times-Italic", 11.5)
                draw_text_run(
                    canvas,
                    item.text,
                    RENDER_MARGIN_X + max(0.0, (usable - width) / 2.0),
                    cursor - 12,
                    "Times-Italic",
                    11.5,
                )
                cursor -= 26.0
                rendered_equations += 1
            elif item.kind == "image":
                blob = images.get(item.image_index or 0)
                if not blob:
                    continue
                try:
                    reader = ImageReader(io.BytesIO(blob))
                    width_px, height_px = reader.getSize()
                except Exception:
                    continue
                target_w = min(usable, 400.0)
                target_h = target_w * height_px / max(1, width_px)
                ensure(target_h + 8.0)
                canvas.drawImage(
                    reader,
                    RENDER_MARGIN_X + (usable - target_w) / 2.0,
                    cursor - target_h,
                    width=target_w,
                    height=target_h,
                    preserveAspectRatio=True,
                    mask="auto",
                )
                cursor -= target_h + 10.0
                rendered_images += 1
            elif item.kind == "table":
                rows = item.table or []
                if not rows:
                    continue
                n_cols = max(len(r) for r in rows)
                col_w = usable / n_cols
                row_h = 15.0
                ensure(row_h * len(rows) + 6.0)
                for row in rows:
                    for col in range(n_cols):
                        value = row[col] if col < len(row) else ""
                        canvas.setStrokeColor(HexColor("#999999"))
                        canvas.setLineWidth(0.4)
                        canvas.rect(
                            RENDER_MARGIN_X + col * col_w, cursor - row_h, col_w, row_h,
                            stroke=1, fill=0,
                        )
                        canvas.setFillColor(HexColor("#000000"))
                        draw_text_run(
                            canvas,
                            _truncate(value, col_w - 6, "Helvetica", 8.5),
                            RENDER_MARGIN_X + col * col_w + 3,
                            cursor - row_h + 5,
                            "Helvetica",
                            8.5,
                        )
                    cursor -= row_h
                cursor -= 8.0
                rendered_tables += 1
            elif item.kind == "footnote":
                draw_paragraph(item.text, "Helvetica", 8.5, 10.5)
                cursor -= 2.0

        canvas.showPage()
        canvas.save()
        return pages, {
            "rendered_images": rendered_images,
            "rendered_tables": rendered_tables,
            "rendered_equations": rendered_equations,
            "render_pages": pages,
        }


class DecompressionBomb(RuntimeError):
    """Raised when a DOCX expands beyond the configured guard."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _iter_block_elements(document: DocxDocumentType):
    """Yield paragraphs and tables in document order."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield DocxParagraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield DocxTable(child, document)


def _heading_level(style_name: str | None) -> int | None:
    if not style_name:
        return None
    match = re.match(r"heading\s*(\d+)", style_name, re.I)
    if match:
        return int(match.group(1))
    if style_name.lower() in ("title",):
        return 1
    return None


def _is_list_style(style_name: str | None) -> bool:
    if not style_name:
        return False
    return bool(re.search(r"list", style_name, re.I))


def _list_marker(paragraph: DocxParagraph) -> str | None:
    """Return a bullet or number when the paragraph is a list item."""
    xml = paragraph._p  # noqa: SLF001
    if xml.findall(".//" + qn("w:numPr")):
        return "\u2022"
    return None


def _has_element(document: DocxDocumentType, tag: str) -> bool:
    return bool(document.element.body.findall(".//" + qn(tag)))


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Wrap to ``width``, breaking between CJK characters as well as at spaces.

    A Chinese paragraph has no spaces, so a space-only wrapper would emit a
    single line several times wider than the page.
    """
    if not text:
        return [""]
    words = text.split()
    if not words:
        return [""]
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
        if measure(word, font, size) <= width:
            current = word
            continue
        # A whitespace-free run that cannot fit: split it between characters.
        chunk = ""
        for char in word:
            candidate = chunk + char
            if chunk and measure(candidate, font, size) > width:
                lines.append(chunk)
                chunk = char
            else:
                chunk = candidate
        current = chunk
    if current:
        lines.append(current)
    return lines


def _truncate(text: str, width: float, font: str, size: float) -> str:
    if measure(text, font, size) <= width:
        return text
    out = text
    while out and measure(out + "\u2026", font, size) > width:
        out = out[:-1]
    return out + "\u2026"


def _unescape_xml(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def _capability_issues(capabilities: dict[str, str]) -> list[dict[str, Any]]:
    """Turn the fidelity gaps into explicit, user-visible issues."""
    issues: list[dict[str, Any]] = []
    messages = {
        ConversionCapability.LISTS: (
            "List bullets and numbering are approximated: the level and the numbering "
            "scheme defined in the document's numbering part are not reproduced."
        ),
        ConversionCapability.EQUATIONS: (
            "Equations are rendered from their OMML text content. Multi-line, matrix and "
            "fraction structures are flattened, so an equation is preserved verbatim but "
            "not necessarily laid out as in Word."
        ),
        ConversionCapability.FOOTNOTES: (
            "Footnotes are read and rendered at the end of the derived text rather than at "
            "the bottom of their original page."
        ),
        ConversionCapability.TEXT_BOXES: (
            "Text inside text boxes, shapes and WordArt is not extracted and will be "
            "missing from the derived rendering."
        ),
        ConversionCapability.FLOATING_OBJECTS: (
            "Floating images and wrapped objects are placed inline in document order, so "
            "their exact position on the page differs from Word."
        ),
        ConversionCapability.REVISION_MARKS: (
            "Tracked changes, comments and revision marks are not displayed."
        ),
        ConversionCapability.PAGINATION: (
            "Pagination is produced by this renderer. It is the reading basis for "
            "anchoring, and it does not match Word's pagination."
        ),
        ConversionCapability.FONTS: (
            "Fonts are substituted with built-in Type1 fonts, so line breaks and text "
            "metrics differ from the original document."
        ),
    }
    for capability, status in capabilities.items():
        if status in ("not_reproduced", "approximated", "substituted", "derived"):
            severity = "error" if status == "not_reproduced" else "warning"
            issues.append(
                {
                    "code": f"docx_{capability}_{status}",
                    "severity": severity,
                    "message": messages.get(capability, f"{capability}: {status}"),
                    "details": {"capability": capability, "status": status},
                }
            )
    return issues
