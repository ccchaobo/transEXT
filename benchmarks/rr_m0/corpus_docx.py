"""DOCX source generation (spec 5.4).

The corpus needs Word documents that exercise headings, nested lists, tables,
images, equations and footnotes.  ``python-docx`` can produce the first four
directly.  For equations and footnotes it cannot, so this module writes the
required OOXML parts itself -- that is the only reliable way to get an
``m:oMath`` element and a real ``footnotes.xml`` into the package, and the point
of the corpus is to test conversion, not to test ``python-docx``.

Every document also records a plain-text ground truth of its paragraphs, in
document order, so the converter can be checked for dropped content without
depending on the converter's own output.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .corpus_layout import ContentItem

__all__ = ["DocxBuilder", "DocxResult"]


@dataclass
class DocxResult:
    path: Path
    paragraph_count: int
    table_count: int
    image_count: int
    formula_count: int
    footnote_count: int
    text_blocks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "paragraph_count": self.paragraph_count,
            "table_count": self.table_count,
            "image_count": self.image_count,
            "formula_count": self.formula_count,
            "footnote_count": self.footnote_count,
            "text_blocks": self.text_blocks,
        }


_FOOTNOTES_XML_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
    '<w:footnote w:type="continuationSeparator" w:id="0">'
    '<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
)
_FOOTNOTES_XML_TAIL = "</w:footnotes>"


def _math_paragraph_xml(text: str) -> str:
    """An OMML equation paragraph.

    The maths is expressed as a single run of literal text inside ``m:oMath``.
    That is enough to prove the converter carries an equation element through;
    a fully structured OMML tree would add no coverage that the corpus needs.
    """
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>"
        "<m:oMathPara xmlns:m=\"http://schemas.openxmlformats.org/officeDocument/2006/math\">"
        "<m:oMath><m:r><m:t>" + escaped + "</m:t></m:r></m:oMath></m:oMathPara></w:p>"
    )


class DocxBuilder:
    """Build a DOCX from the same :class:`ContentItem` sequence as the PDFs."""

    def __init__(self, title: str, authors: str, output_path: Path) -> None:
        self.title = title
        self.authors = authors
        self.output_path = Path(output_path)
        self._math_paragraphs: list[str] = []
        self._footnotes: list[str] = []
        self._blocks: list[dict] = []

    def build(self, content: Sequence[ContentItem]) -> DocxResult:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        doc = DocxDocument()

        normal = doc.styles["Normal"]
        normal.font.name = "Times New Roman"
        normal.font.size = Pt(10.5)

        heading = doc.add_heading(self.title, level=0)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        author_para = doc.add_paragraph(self.authors)
        author_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        author_para.runs[0].italic = True
        self._blocks.append({"type": "title", "text": self.title, "order": 0})
        self._blocks.append({"type": "author", "text": self.authors, "order": 1})

        order = 2
        tables = 0
        images = 0
        for item in content:
            if item.kind == "heading":
                doc.add_heading(item.text, level=min(max(item.level, 1), 4))
                self._blocks.append({"type": "heading", "text": item.text, "order": order, "level": item.level})
            elif item.kind == "paragraph":
                doc.add_paragraph(item.text)
                self._blocks.append({"type": "paragraph", "text": item.text, "order": order})
            elif item.kind == "abstract":
                para = doc.add_paragraph()
                run = para.add_run("Abstract\u2014")
                run.bold = True
                para.add_run(item.text)
                self._blocks.append({"type": "abstract", "text": item.text, "order": order})
            elif item.kind == "formula":
                self._math_paragraphs.append(item.text)
                self._blocks.append({"type": "formula", "text": item.text, "order": order})
            elif item.kind == "figure":
                doc.add_picture(io.BytesIO(_figure_png(item.label)), width=Inches(4.6))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                images += 1
                cap = doc.add_paragraph(item.caption)
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in cap.runs:
                    run.font.size = Pt(9)
                    run.italic = True
                self._blocks.append({"type": "figure", "text": item.caption, "order": order})
            elif item.kind == "table":
                rows = [item.header] + item.rows
                table = doc.add_table(rows=len(rows), cols=len(item.header))
                table.style = "Table Grid"
                for r_index, row in enumerate(rows):
                    for c_index, value in enumerate(row):
                        cell = table.cell(r_index, c_index)
                        cell.text = str(value)
                        for para in cell.paragraphs:
                            for run in para.runs:
                                run.font.size = Pt(9)
                                if r_index == 0:
                                    run.bold = True
                tables += 1
                cap = doc.add_paragraph(item.caption)
                for run in cap.runs:
                    run.font.size = Pt(9)
                self._blocks.append({"type": "table", "text": item.caption, "order": order})
            elif item.kind == "footnote":
                self._footnotes.append(item.text)
                self._blocks.append({"type": "footnote", "text": item.text, "order": order})
            elif item.kind == "reference":
                para = doc.add_paragraph(item.text)
                para.paragraph_format.left_indent = Inches(0.3)
                for run in para.runs:
                    run.font.size = Pt(9)
                self._blocks.append({"type": "reference", "text": item.text, "order": order})
            elif item.kind == "pagebreak":
                doc.add_page_break()
            else:
                raise ValueError(f"unsupported content kind for DOCX: {item.kind}")
            order += 1

        doc.save(str(self.output_path))
        if self._math_paragraphs or self._footnotes:
            self._inject_ooxml()
        return DocxResult(
            path=self.output_path,
            paragraph_count=len(doc.paragraphs),
            table_count=tables,
            image_count=images,
            formula_count=len(self._math_paragraphs),
            footnote_count=len(self._footnotes),
            text_blocks=self._blocks,
        )

    # -- OOXML injection ---------------------------------------------------
    def _inject_ooxml(self) -> None:
        """Rewrite the package to add equations and footnotes.

        ``python-docx`` has no equation or footnote API, so the parts are written
        directly.  The document body is re-serialised with the maths paragraphs
        appended before ``sectPr``.
        """
        path = self.output_path
        with zipfile.ZipFile(path, "r") as zin:
            parts = {name: zin.read(name) for name in zin.namelist()}

        document_xml = parts["word/document.xml"].decode("utf-8")
        if self._math_paragraphs:
            injected = "".join(_math_paragraph_xml(text) for text in self._math_paragraphs)
            marker = "<w:sectPr"
            position = document_xml.rfind(marker)
            if position == -1:
                position = document_xml.rfind("</w:body>")
            document_xml = document_xml[:position] + injected + document_xml[position:]
        if self._footnotes:
            references = "".join(
                f'<w:footnoteReference w:id="{index + 1}"/>'
                for index in range(len(self._footnotes))
            )
            footnote_para = (
                "<w:p><w:r><w:rPr><w:rStyle w:val=\"FootnoteReference\"/></w:rPr>"
                + references
                + "</w:r></w:p>"
            )
            position = document_xml.rfind("<w:sectPr")
            if position == -1:
                position = document_xml.rfind("</w:body>")
            document_xml = document_xml[:position] + footnote_para + document_xml[position:]
        parts["word/document.xml"] = document_xml.encode("utf-8")

        if self._footnotes:
            parts["word/footnotes.xml"] = (
                _FOOTNOTES_XML_HEAD
                + "".join(
                    f'<w:footnote w:id="{index + 1}"><w:p><w:r><w:t xml:space="preserve">'
                    + text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    + "</w:t></w:r></w:p></w:footnote>"
                    for index, text in enumerate(self._footnotes)
                )
                + _FOOTNOTES_XML_TAIL
            ).encode("utf-8")
            parts["word/_rels/document.xml.rels"] = _add_relationship(
                parts.get("word/_rels/document.xml.rels", b""),
                relationship_id="rIdFootnotes1",
                rel_type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
                target="footnotes.xml",
            )
            parts["[Content_Types].xml"] = _add_content_type(
                parts["[Content_Types].xml"],
                part_name="/word/footnotes.xml",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
            )

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
            for name, data in parts.items():
                zout.writestr(name, data)


def _add_relationship(existing: bytes, *, relationship_id: str, rel_type: str, target: str) -> bytes:
    xml = existing.decode("utf-8") if existing else (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )
    if relationship_id in xml:
        return xml.encode("utf-8")
    addition = f'<Relationship Id="{relationship_id}" Type="{rel_type}" Target="{target}"/>'
    return xml.replace("</Relationships>", addition + "</Relationships>").encode("utf-8")


def _add_content_type(existing: bytes, *, part_name: str, content_type: str) -> bytes:
    xml = existing.decode("utf-8")
    if part_name in xml:
        return existing
    addition = f'<Override PartName="{part_name}" ContentType="{content_type}"/>'
    return xml.replace("</Types>", addition + "</Types>").encode("utf-8")


def _figure_png(label: str) -> bytes:
    """A small deterministic chart image for the DOCX figure."""
    from PIL import Image, ImageDraw

    width, height = 640, 220
    image = Image.new("RGB", (width, height), (245, 247, 250))
    draw = ImageDraw.Draw(image)
    draw.rectangle([2, 2, width - 3, height - 3], outline=(60, 60, 60))
    points = []
    for i in range(60):
        x = 20 + (width - 40) * i / 59
        import math

        value = math.sin(i / 4.0) * 0.3 + 0.5 + (i / 60) * 0.3
        y = height - 24 - (height - 60) * min(0.95, max(0.05, value))
        points.append((x, y))
    draw.line(points, fill=(31, 111, 235), width=2)
    for point in points[::10]:
        draw.ellipse([point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3], fill=(209, 36, 47))
    draw.text((14, 10), f"{label}: synthetic series", fill=(40, 40, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
