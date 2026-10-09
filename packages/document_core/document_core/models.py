"""Unified document model.

Every parser adapter converts its vendor-specific output into these types, so
the rest of the system never depends on a supplier's raw fields (spec 9.2).

Two invariants matter most:

1. **Raw extraction is preserved.** A block keeps ``raw_text`` exactly as
   extracted/recognised, and ``clean_text`` separately, so that a cleaning or
   normalisation bug can always be traced back to what the parser actually saw
   (spec 5.2).
2. **Geometry is expressed in the frozen coordinate contract.** ``bbox`` is
   display-normalised; ``raw_bbox`` keeps the source space box; the transform
   chain that relates them is recorded on the span.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

from .coords import AffineTransform, BBox, CoordinateSpace, PageGeometry, polygon_from_bbox

__all__ = [
    "BlockType",
    "Confidence",
    "ConfidenceLevel",
    "SourceSpan",
    "Block",
    "Asset",
    "Page",
    "Section",
    "ParseResult",
    "ParseStatus",
    "Severity",
    "QualityIssue",
    "SCHEMA_VERSION",
]

SCHEMA_VERSION = "1.0.0"


class BlockType(str, Enum):
    """Semantic block types produced by parsing."""

    TITLE = "title"
    AUTHOR = "author"
    AFFILIATION = "affiliation"
    ABSTRACT = "abstract"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    CAPTION = "caption"          # figure / table caption, translated independently
    TABLE = "table"
    TABLE_CELL = "table_cell"
    FORMULA = "formula"          # kept verbatim, never sent to translation
    FIGURE = "figure"            # image region, kept verbatim
    REFERENCE = "reference"      # bibliography entry, identifiers not translated
    FOOTNOTE = "footnote"
    HEADER = "header"            # running header: excluded from body coverage
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    UNKNOWN = "unknown"

    @property
    def is_translatable(self) -> bool:
        return self in _TRANSLATABLE

    @property
    def is_body(self) -> bool:
        """Body content counts towards the body-coverage acceptance metric."""
        return self in _BODY

    @property
    def is_furniture(self) -> bool:
        return self in _FURNITURE


_TRANSLATABLE = frozenset(
    {
        BlockType.TITLE,
        BlockType.ABSTRACT,
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.LIST_ITEM,
        BlockType.CAPTION,
        BlockType.TABLE_CELL,
        BlockType.FOOTNOTE,
    }
)

_BODY = frozenset(
    {
        BlockType.TITLE,
        BlockType.AUTHOR,
        BlockType.AFFILIATION,
        BlockType.ABSTRACT,
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.LIST_ITEM,
        BlockType.CAPTION,
        BlockType.TABLE,
        BlockType.TABLE_CELL,
        BlockType.FOOTNOTE,
        BlockType.REFERENCE,
    }
)

_FURNITURE = frozenset(
    {
        BlockType.HEADER,
        BlockType.FOOTER,
        BlockType.PAGE_NUMBER,
    }
)


class ConfidenceLevel(str, Enum):
    """Coarse, user-visible reliability, per spec 5.3.

    ``UNKNOWN`` is a first-class value: when an OCR engine does not return a
    score we must not invent one.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"

    @staticmethod
    def from_score(score: float | None) -> "ConfidenceLevel":
        if score is None:
            return ConfidenceLevel.UNKNOWN
        if score >= 0.85:
            return ConfidenceLevel.HIGH
        if score >= 0.6:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW


@dataclass
class Confidence:
    """A score plus the level derived from it."""

    score: float | None = None
    level: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    source: str | None = None  # e.g. "ocr:rapidocr", "layout:heuristic"

    @staticmethod
    def unknown(source: str | None = None) -> "Confidence":
        return Confidence(score=None, level=ConfidenceLevel.UNKNOWN, source=source)

    @staticmethod
    def of(score: float | None, source: str | None = None) -> "Confidence":
        return Confidence(score=score, level=ConfidenceLevel.from_score(score), source=source)

    @property
    def needs_review(self) -> bool:
        return self.level in (ConfidenceLevel.LOW, ConfidenceLevel.UNKNOWN)

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "level": self.level.value, "source": self.source}


@dataclass
class SourceSpan:
    """A region of a source page that a block came from.

    A block may have several spans (cross-page paragraphs, complex reading
    order).  ``bbox`` is display-normalised; ``raw_bbox`` is in
    ``raw_space``; both are related by ``raw_to_display``.
    """

    page_index: int
    bbox: BBox
    raw_bbox: BBox | None = None
    raw_space: str = CoordinateSpace.RAW_PDF
    raw_to_display: AffineTransform = field(default_factory=AffineTransform.identity)
    transforms: list[AffineTransform] = field(default_factory=list)
    text_start: int = 0
    text_end: int = 0
    polygon: Sequence[Sequence[float]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "bbox": self.bbox.as_list(),
            "raw_bbox": self.raw_bbox.as_list() if self.raw_bbox else None,
            "raw_space": self.raw_space,
            "raw_to_display": self.raw_to_display.to_dict(),
            "transforms": [t.to_dict() for t in self.transforms],
            "text_start": self.text_start,
            "text_end": self.text_end,
            "polygon": [list(p) for p in self.polygon] if self.polygon else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceSpan":
        return cls(
            page_index=int(data["page_index"]),
            bbox=BBox.from_any(data["bbox"]),
            raw_bbox=BBox.from_any(data["raw_bbox"]) if data.get("raw_bbox") else None,
            raw_space=data.get("raw_space", CoordinateSpace.RAW_PDF),
            raw_to_display=AffineTransform.from_dict(data.get("raw_to_display")),
            transforms=[AffineTransform.from_dict(t) for t in data.get("transforms", [])],
            text_start=int(data.get("text_start", 0)),
            text_end=int(data.get("text_end", 0)),
            polygon=tuple(tuple(p) for p in data["polygon"]) if data.get("polygon") else None,
        )


@dataclass
class Block:
    """A semantic unit of the document."""

    id: str
    page_index: int
    order: int                       # reading order, unique per parse revision
    type: BlockType
    raw_text: str
    clean_text: str
    spans: list[SourceSpan] = field(default_factory=list)
    confidence: Confidence = field(default_factory=Confidence.unknown)
    section_path: list[str] = field(default_factory=list)
    section_id: str | None = None
    level: int | None = None         # heading depth
    ocr_revision: int = 0            # bumped when a human corrects recognised text
    table: "TableData | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.clean_text or self.raw_text

    @property
    def is_translatable(self) -> bool:
        return self.type.is_translatable and bool(self.text.strip())

    @property
    def is_human_edited(self) -> bool:
        return self.ocr_revision > 0

    @property
    def pages(self) -> list[int]:
        seen: list[int] = []
        for span in self.spans:
            if span.page_index not in seen:
                seen.append(span.page_index)
        return seen

    @property
    def is_cross_page(self) -> bool:
        return len(self.pages) > 1

    @property
    def bbox(self) -> BBox | None:
        return self.spans[0].bbox if self.spans else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "page_index": self.page_index,
            "order": self.order,
            "type": self.type.value,
            "raw_text": self.raw_text,
            "clean_text": self.clean_text,
            "spans": [s.to_dict() for s in self.spans],
            "confidence": self.confidence.to_dict(),
            "section_path": list(self.section_path),
            "section_id": self.section_id,
            "level": self.level,
            "ocr_revision": self.ocr_revision,
            "table": self.table.to_dict() if self.table else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Block":
        return cls(
            id=data["id"],
            page_index=int(data["page_index"]),
            order=int(data["order"]),
            type=BlockType(data["type"]),
            raw_text=data.get("raw_text", ""),
            clean_text=data.get("clean_text", ""),
            spans=[SourceSpan.from_dict(s) for s in data.get("spans", [])],
            confidence=Confidence(
                score=data.get("confidence", {}).get("score"),
                level=ConfidenceLevel(data.get("confidence", {}).get("level", "unknown")),
                source=data.get("confidence", {}).get("source"),
            ),
            section_path=list(data.get("section_path", [])),
            section_id=data.get("section_id"),
            level=data.get("level"),
            ocr_revision=int(data.get("ocr_revision", 0)),
            table=TableData.from_dict(data["table"]) if data.get("table") else None,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class TableData:
    """A recognised simple table.  Complex tables are *not* reconstructed."""

    n_rows: int
    n_cols: int
    cells: list[list[str]]
    cell_blocks: dict[str, str] = field(default_factory=dict)  # "r,c" -> block id
    reconstruction: str = "none"  # "none" | "simple_grid"
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "cells": [list(r) for r in self.cells],
            "cell_blocks": dict(self.cell_blocks),
            "reconstruction": self.reconstruction,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableData":
        return cls(
            n_rows=int(data["n_rows"]),
            n_cols=int(data["n_cols"]),
            cells=[list(r) for r in data.get("cells", [])],
            cell_blocks=dict(data.get("cell_blocks", {})),
            reconstruction=data.get("reconstruction", "none"),
            notes=data.get("notes"),
        )


@dataclass
class Asset:
    """An image or other binary extracted from the document, kept verbatim."""

    id: str
    kind: str                 # "figure" | "table_image" | "page_preview" | "ocr_crop"
    page_index: int
    path: str                 # relative to the derived-resource root
    sha256: str
    bbox: BBox | None = None
    caption_block_id: str | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "page_index": self.page_index,
            "path": self.path,
            "sha256": self.sha256,
            "bbox": self.bbox.as_list() if self.bbox else None,
            "caption_block_id": self.caption_block_id,
            "width": self.width,
            "height": self.height,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Asset":
        return cls(
            id=data["id"],
            kind=data["kind"],
            page_index=int(data["page_index"]),
            path=data["path"],
            sha256=data["sha256"],
            bbox=BBox.from_any(data["bbox"]) if data.get("bbox") else None,
            caption_block_id=data.get("caption_block_id"),
            width=data.get("width"),
            height=data.get("height"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Page:
    index: int
    width_pt: float
    height_pt: float
    geometry: PageGeometry
    printed_label: str | None = None
    preview_path: str | None = None
    source_kind: str = "text"     # "text" | "scanned" | "mixed" | "image" | "docx_preview"
    text_chars: int = 0
    ocr_applied: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
            "geometry": self.geometry.to_dict(),
            "printed_label": self.printed_label,
            "preview_path": self.preview_path,
            "source_kind": self.source_kind,
            "text_chars": self.text_chars,
            "ocr_applied": self.ocr_applied,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Page":
        geo = data["geometry"]
        return cls(
            index=int(data["index"]),
            width_pt=float(data["width_pt"]),
            height_pt=float(data["height_pt"]),
            geometry=PageGeometry(
                media_width=geo["media_width"],
                media_height=geo["media_height"],
                rotation=geo.get("rotation", 0),
                crop=BBox.from_any(geo["crop"]) if geo.get("crop") else None,
                dpi=geo.get("dpi", 72.0),
            ),
            printed_label=data.get("printed_label"),
            preview_path=data.get("preview_path"),
            source_kind=data.get("source_kind", "text"),
            text_chars=int(data.get("text_chars", 0)),
            ocr_applied=bool(data.get("ocr_applied", False)),
            warnings=list(data.get("warnings", [])),
        )


@dataclass
class Section:
    id: str
    title: str
    level: int
    path: list[str]
    start_order: int
    end_order: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ParseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partially_succeeded"
    FAILED = "failed"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class QualityIssue:
    """A quality-check finding attached to a page or block."""

    code: str
    severity: Severity
    message: str
    page_index: int | None = None
    block_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "page_index": self.page_index,
            "block_id": self.block_id,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualityIssue":
        return cls(
            code=data["code"],
            severity=Severity(data.get("severity", "info")),
            message=data.get("message", ""),
            page_index=data.get("page_index"),
            block_id=data.get("block_id"),
            details=dict(data.get("details", {})),
        )


@dataclass
class ParseResult:
    """Everything a parser adapter produces for one parse revision."""

    parser: str
    parser_version: str
    pages: list[Page]
    blocks: list[Block]
    assets: list[Asset] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    issues: list[QualityIssue] = field(default_factory=list)
    status: ParseStatus = ParseStatus.SUCCEEDED
    parameters: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def body_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.type.is_body]

    def page_blocks(self, page_index: int) -> list[Block]:
        return [b for b in self.blocks if page_index in b.pages]

    def block_by_id(self, block_id: str) -> Block | None:
        for b in self.blocks:
            if b.id == block_id:
                return b
        return None

    def iter_spans(self) -> Iterable[tuple[Block, SourceSpan]]:
        for block in self.blocks:
            for span in block.spans:
                yield block, span

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "status": self.status.value,
            "parameters": dict(self.parameters),
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
            "pages": [p.to_dict() for p in self.pages],
            "blocks": [b.to_dict() for b in self.blocks],
            "assets": [a.to_dict() for a in self.assets],
            "sections": [s.to_dict() for s in self.sections],
            "issues": [i.to_dict() for i in self.issues],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParseResult":
        return cls(
            parser=data.get("parser", "unknown"),
            parser_version=data.get("parser_version", "0"),
            pages=[Page.from_dict(p) for p in data.get("pages", [])],
            blocks=[Block.from_dict(b) for b in data.get("blocks", [])],
            assets=[Asset.from_dict(a) for a in data.get("assets", [])],
            sections=[Section(**s) for s in data.get("sections", [])],
            issues=[QualityIssue.from_dict(i) for i in data.get("issues", [])],
            status=ParseStatus(data.get("status", "succeeded")),
            parameters=dict(data.get("parameters", {})),
            warnings=list(data.get("warnings", [])),
            stats=dict(data.get("stats", {})),
        )
