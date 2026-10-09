"""Layout analysis: reading order, columns, headers/footers, section tree.

This is the highest-risk module in the system (spec 2.3, 5.2).  The design is a
pipeline of small, individually testable steps:

1. ``TextLine`` objects are extracted by a format adapter (PDF text layer or
   OCR regions).
2. Regions are segmented into horizontal bands separated by wide horizontal
   whitespace (a figure or table spanning both columns ends a band).
3. Inside each band, columns are found by a vertical whitespace gutter search
   over the projection profile.
4. Lines are ordered: band, then column left-to-right, then top-to-bottom.
5. Repeated top/bottom lines are classified as header/footer furniture.
6. Lines are merged into paragraphs, de-hyphenating wrap breaks and joining
   paragraphs that continue across a page or column break.
7. The heading tree is built and section paths assigned.

Every step records the evidence it used so a quality report can explain a
suspicious result instead of silently producing a wrong reading order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from .cleaning import clean_text, looks_like_garbage
from .coords import BBox
from .ids import normalise_for_match

__all__ = [
    "TextLine",
    "ColumnPlan",
    "LayoutParams",
    "LayoutResult",
    "analyse",
    "detect_columns",
    "segment_bands",
    "classify_furniture",
    "assemble_paragraphs",
    "build_sections",
    "ReadingOrder",
]


@dataclass
class TextLine:
    """One visual line of text on a page."""

    text: str
    bbox: BBox
    page_index: int = 0
    font_size: float | None = None
    font_name: str | None = None
    bold: bool = False
    italic: bool = False
    colour: int | None = None
    block_hint: int | None = None      # parser-provided grouping hint
    confidence: float | None = None
    source: str = "text"               # "text" | "ocr"
    spans: list[tuple[float, float, str]] = field(default_factory=list)

    @property
    def x_centre(self) -> float:
        return (self.bbox.x0 + self.bbox.x1) / 2.0

    @property
    def y_centre(self) -> float:
        return (self.bbox.y0 + self.bbox.y1) / 2.0

    @property
    def height(self) -> float:
        return self.bbox.height

    @property
    def width(self) -> float:
        return self.bbox.width


@dataclass
class LayoutParams:
    """Tunable heuristics with the defaults used for acceptance runs.

    Values are in normalised page units unless stated otherwise.
    """

    header_zone: float = 0.085          # top fraction searched for running heads
    footer_zone: float = 0.085
    furniture_min_pages: int = 2        # a repeated line must appear on >= N pages
    furniture_similarity: float = 0.82
    gutter_min_width: float = 0.012     # minimum column gutter, normalised
    gutter_min_overlap: float = 0.55    # gutter must span this share of the band
    column_balance_tolerance: float = 0.25
    band_gap_factor: float = 1.9        # horizontal gap > factor * line height = band break
    paragraph_gap_factor: float = 1.55  # vertical gap > factor * median height = new para
    paragraph_indent: float = 0.018     # first-line indent that starts a new para
    paragraph_similarity: float = 0.45  # min shingle overlap for cross-break merge
    heading_max_chars: int = 90
    heading_max_words: int = 14
    max_columns: int = 3
    line_merge_vertical_overlap: float = 0.5


@dataclass
class Column:
    index: int
    x0: float
    x1: float
    line_indices: list[int]

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class Band:
    index: int
    y0: float
    y1: float
    line_indices: list[int]
    columns: list[Column]
    full_width: bool = False


@dataclass
class ColumnPlan:
    bands: list[Band]
    n_columns: int
    gutter: tuple[float, float] | None
    evidence: dict = field(default_factory=dict)


@dataclass
class LayoutResult:
    """Ordered, assembled blocks plus the evidence behind the ordering."""

    paragraphs: list["Paragraph"]
    furniture: list["Paragraph"]
    page_columns: dict[int, int]
    evidence: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Paragraph:
    """A paragraph assembled from one or more lines, possibly across pages."""

    text: str
    raw_text: str
    lines: list[TextLine]
    page_indices: list[int]
    bbox: BBox
    font_size: float | None
    bold: bool
    kind: str = "paragraph"          # refined later into a BlockType
    segment: list[tuple[int, int]] = field(default_factory=list)  # (page, line ordinal)
    continues_after: bool = False
    continues_before: bool = False
    section_path: list[str] = field(default_factory=list)
    section_id: str | None = None
    level: int | None = None

    @property
    def is_cross_page(self) -> bool:
        return len(set(self.page_indices)) > 1


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------


def _projection_profile(
    lines: list[TextLine], n_bins: int = 200, key: str = "x"
) -> list[float]:
    """Ink occupancy of the page along one axis, as a fraction per bin."""
    profile = [0.0] * n_bins
    for ln in lines:
        if key == "x":
            a, b = ln.bbox.x0, ln.bbox.x1
        else:
            a, b = ln.bbox.y0, ln.bbox.y1
        ia = max(0, min(n_bins - 1, int(a * n_bins)))
        ib = max(0, min(n_bins - 1, int(b * n_bins)))
        for i in range(ia, ib + 1):
            profile[i] += 1.0
    total = len(lines) or 1
    return [v / total for v in profile]


def _find_gutters(
    profile: list[float],
    min_width_bins: int,
    max_val: float = 0.02,
) -> list[tuple[int, int]]:
    """Find runs of near-empty bins in a projection profile."""
    gutters: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(profile):
        if v <= max_val:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_width_bins:
                gutters.append((start, i))
            start = None
    if start is not None and len(profile) - start >= min_width_bins:
        gutters.append((start, len(profile)))
    return gutters


def segment_bands(
    lines: list[TextLine],
    page_height: float,
    params: LayoutParams,
) -> list[Band]:
    """Split a page into horizontal bands.

    A band is a contiguous vertical run of lines.  A vertical gap much larger
    than the local line height means something spanned the full width between
    the two groups (typically a figure, a table or a section rule), so the
    reading order must restart afterwards.
    """
    if not lines:
        return []
    ordered = sorted(lines, key=lambda ln: ln.y_centre)
    heights = [ln.height for ln in ordered if ln.height > 0]
    if not heights:
        return [Band(0, 0.0, page_height, list(range(len(lines))), [])]
    typical = median(heights)
    gap_threshold = params.band_gap_factor * typical

    bands: list[dict] = []
    current = [0]
    for i in range(1, len(ordered)):
        prev, cur = ordered[i - 1], ordered[i]
        # Two lines overlap vertically when they are side by side in a column
        # layout; only a real vertical gap can end a band.
        gap = cur.bbox.y0 - prev.bbox.y1
        if gap > gap_threshold:
            bands.append({"lines": current})
            current = [i]
        else:
            current.append(i)
    bands.append({"lines": current})

    result: list[Band] = []
    for bi, band in enumerate(bands):
        idx = band["lines"]
        ys0 = min(ordered[i].bbox.y0 for i in idx)
        ys1 = max(ordered[i].bbox.y1 for i in idx)
        result.append(Band(bi, ys0, ys1, idx, []))
    return result


def detect_columns(
    lines: list[TextLine],
    band: Band,
    ordered: list[TextLine],
    params: LayoutParams,
) -> list[Column]:
    """Find columns inside one band using a gutter search."""
    band_lines = [ordered[i] for i in band.line_indices]
    if not band_lines:
        return []
    span_x0 = min(ln.bbox.x0 for ln in band_lines)
    span_x1 = max(ln.bbox.x1 for ln in band_lines)
    width = span_x1 - span_x0
    if width <= 0:
        return [Column(0, span_x0, span_x1, list(band.line_indices))]

    n_bins = 200
    profile = _projection_profile(band_lines, n_bins)
    min_width_bins = max(1, int(params.gutter_min_width * n_bins))
    gutters = _find_gutters(profile, min_width_bins)

    # Keep only gutters that actually separate the band: lines must exist on
    # both sides.
    band_y0, band_y1 = band.y0, band.y1
    splits: list[float] = []
    for g0, g1 in gutters:
        x0, x1 = g0 / n_bins, g1 / n_bins
        if x0 <= span_x0 + 0.02 or x1 >= span_x1 - 0.02:
            continue  # leading/trailing margin, not an internal gutter
        left = [ln for ln in band_lines if ln.bbox.x1 <= x0 + 1e-6]
        right = [ln for ln in band_lines if ln.bbox.x0 >= x1 - 1e-6]
        if not left or not right:
            continue
        splits.append((x0 + x1) / 2.0)

    if not splits:
        return [Column(0, span_x0, span_x1, list(band.line_indices))]

    # Merge close splits and cap the column count.
    splits = sorted(splits)
    merged: list[float] = []
    for s in splits:
        if merged and s - merged[-1] < params.gutter_min_width:
            merged[-1] = (merged[-1] + s) / 2.0
        else:
            merged.append(s)
    merged = merged[: params.max_columns - 1]

    boundaries = [span_x0 - 1e-6] + merged + [span_x1 + 1e-6]
    columns: list[Column] = []
    for ci in range(len(boundaries) - 1):
        lo, hi = boundaries[ci], boundaries[ci + 1]
        members = [
            i
            for i in band.line_indices
            if lo <= ordered[i].x_centre <= hi
        ]
        if members:
            columns.append(Column(ci, lo, hi, members))
    if not columns:
        return [Column(0, span_x0, span_x1, list(band.line_indices))]
    return columns


class ReadingOrder:
    """Kept for callers that want an explicit object rather than ``analyse``."""

    def __init__(self, params: LayoutParams | None = None) -> None:
        self.params = params or LayoutParams()

    def analyse(self, lines_by_page: dict[int, list[TextLine]]) -> LayoutResult:
        return analyse(lines_by_page, self.params)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _repeat_signature(text: str) -> str:
    """Signature used to detect running heads, ignoring page numbers."""
    norm = normalise_for_match(text)
    norm = re.sub(r"\b\d+\b", "#", norm)
    return norm


def classify_furniture(
    ordered: list[tuple[int, int, TextLine]],
    params: LayoutParams,
) -> tuple[list[Paragraph], list[tuple[int, int, TextLine]]]:
    """Separate running headers/footers and bare page numbers from body text.

    Spec 5.2: nothing is deleted.  Furniture keeps a type label and its
    original position so it stays reviewable.
    """
    if not ordered:
        return [], []
    pages: dict[int, list[tuple[int, int, TextLine]]] = {}
    for item in ordered:
        pages.setdefault(item[0], []).append(item)

    page_heights: dict[int, float] = {}
    for page_index, items in pages.items():
        page_heights[page_index] = max(ln.bbox.y1 for _, _, ln in items) or 1.0

    candidates: dict[str, list[tuple[int, int, TextLine]]] = {}
    for page_index, items in pages.items():
        h = page_heights[page_index]
        for item in items:
            ln = item[2]
            in_header = ln.bbox.y1 <= params.header_zone * h
            in_footer = ln.bbox.y0 >= (1.0 - params.footer_zone) * h
            if not (in_header or in_footer):
                continue
            sig = _repeat_signature(ln.text)
            if len(sig) < 2:
                continue
            candidates.setdefault(sig, []).append(item)

    furniture_keys: dict[str, int] = {}
    for sig, items in candidates.items():
        page_set = {i[0] for i in items}
        if len(page_set) >= params.furniture_min_pages:
            furniture_keys[sig] = len(page_set)

    furniture: list[Paragraph] = []
    body: list[tuple[int, int, TextLine]] = []
    for item in ordered:
        sig = _repeat_signature(item[2].text)
        if sig in furniture_keys:
            ln = item[2]
            kind = "page_number" if re.fullmatch(r"[\s\-–—]*\d+[\s\-–—]*", ln.text) else "furniture"
            furniture.append(
                Paragraph(
                    text=clean_text(ln.text),
                    raw_text=ln.text,
                    lines=[ln],
                    page_indices=[item[0]],
                    bbox=ln.bbox,
                    font_size=ln.font_size,
                    bold=ln.bold,
                    kind=kind,
                )
            )
        elif re.fullmatch(r"[\s\-–—]*\d{1,4}[\s\-–—]*", item[2].text.strip()):
            # Isolated page-number-looking line in the margin.
            ln = item[2]
            h = page_heights[item[0]]
            in_margin = ln.bbox.y1 <= 0.15 * h or ln.bbox.y0 >= 0.85 * h
            if in_margin:
                furniture.append(
                    Paragraph(
                        text=clean_text(ln.text),
                        raw_text=ln.text,
                        lines=[ln],
                        page_indices=[item[0]],
                        bbox=ln.bbox,
                        font_size=ln.font_size,
                        bold=ln.bold,
                        kind="page_number",
                    )
                )
            else:
                body.append(item)
        else:
            body.append(item)
    return furniture, body


# ---------------------------------------------------------------------------
# Paragraph assembly
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?:;]\s*$")
_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+|[IVXLC]+\.\s+|Abstract\b|Introduction\b|"
    r"Conclusions?\b|References\b|Acknowledg(?:e?ments?)\b|Appendix\b|"
    r"Methods?\b|Results?\b|Discussion\b|Related Work\b|Background\b)",
    re.IGNORECASE,
)


def _is_heading_like(para_lines: list[TextLine], params: LayoutParams) -> bool:
    text = " ".join(ln.text for ln in para_lines).strip()
    if not text or len(text) > params.heading_max_chars:
        return False
    if len(text.split()) > params.heading_max_words:
        return False
    if text.endswith((".", ",", ";", ":")) and not _HEADING_RE.match(text):
        return False
    numbered = bool(re.match(r"^\d+(\.\d+)*\.?\s+", text))
    all_bold = para_lines and all(ln.bold for ln in para_lines)
    keyword = bool(_HEADING_RE.match(text))
    return numbered or keyword or (all_bold and len(text.split()) <= 12)


def _vertical_gap(above: TextLine, below: TextLine) -> float:
    return below.bbox.y0 - above.bbox.y1


def _same_column_flow(
    above: TextLine,
    below: TextLine,
    page_height: float,
    params: LayoutParams,
    typical_height: float,
) -> bool:
    """Decide whether ``below`` continues the paragraph containing ``above``."""
    gap = _vertical_gap(above, below)
    if gap < 0:
        return False
    if gap > params.paragraph_gap_factor * typical_height:
        return False
    # A large horizontal offset between the two lines' left edges can mean a
    # different column; but within a column, an indent also shifts the left
    # edge.  Require vertical proximity plus either column overlap or a small
    # indent shift.
    horizontal_overlap = min(above.bbox.x1, below.bbox.x1) - max(above.bbox.x0, below.bbox.x0)
    if horizontal_overlap <= 0:
        return False
    indent = below.bbox.x0 - above.bbox.x0
    if indent > params.paragraph_indent * 3:
        return False
    return True


def assemble_paragraphs(
    body: list[tuple[int, int, TextLine]],
    lines_by_page: dict[int, list[TextLine]],
    params: LayoutParams,
    furniture: list[Paragraph] | None = None,
) -> list[Paragraph]:
    """Group ordered lines into paragraphs.

    Two grouping signals are used:

    * vertical gap and indentation inside a page/column, and
    * content continuation across a page or column break, where the previous
      line has no sentence-final punctuation and the following line starts in
      lowercase.
    """
    if not body:
        return []
    by_page: dict[int, list[TextLine]] = {}
    for page_index, _, line in body:
        by_page.setdefault(page_index, []).append(line)

    all_heights = [ln.height for ln in (item[2] for item in body) if ln.height > 0]
    typical_height = median(all_heights) if all_heights else 0.012

    page_heights: dict[int, float] = {}
    for page_index, lines in lines_by_page.items():
        page_heights[page_index] = max((ln.bbox.y1 for ln in lines), default=1.0)

    # 1. group inside pages, following the computed reading order.
    groups: list[list[TextLine]] = []
    current: list[TextLine] = []
    for page_index, _, line in body:
        if not current:
            current = [line]
            continue
        prev = current[-1]
        same_page = prev.page_index == line.page_index
        if not same_page:
            groups.append(current)
            current = [line]
            continue
        if _same_column_flow(prev, line, page_heights.get(page_index, 1.0), params, typical_height):
            current.append(line)
        else:
            groups.append(current)
            current = [line]
    if current:
        groups.append(current)

    # 2. merge cross-page / cross-column continuations.
    merged: list[list[TextLine]] = []
    for grp in groups:
        if not merged:
            merged.append(grp)
            continue
        prev_grp = merged[-1]
        if _should_merge(prev_grp, grp, params):
            prev_grp.extend(grp)
        else:
            merged.append(grp)

    paragraphs: list[Paragraph] = []
    for grp in merged:
        raw = "\n".join(ln.text for ln in grp)
        text = clean_text(raw)
        if not text:
            continue
        pages = sorted({ln.page_index for ln in grp})
        font_sizes = [ln.font_size for ln in grp if ln.font_size]
        paragraphs.append(
            Paragraph(
                text=text,
                raw_text=raw,
                lines=grp,
                page_indices=pages,
                bbox=BBox(
                    min(ln.bbox.x0 for ln in grp),
                    min(ln.bbox.y0 for ln in grp),
                    max(ln.bbox.x1 for ln in grp),
                    max(ln.bbox.y1 for ln in grp),
                ),
                font_size=median(font_sizes) if font_sizes else None,
                bold=all(ln.bold for ln in grp),
                kind="heading" if _is_heading_like(grp, params) else "paragraph",
            )
        )
    return paragraphs


def _should_merge(prev: list[TextLine], nxt: list[TextLine], params: LayoutParams) -> bool:
    """Whether ``nxt`` continues the paragraph in ``prev`` across a break."""
    if not prev or not nxt:
        return False
    if _is_heading_like(prev, params) or _is_heading_like(nxt, params):
        return False
    last, first = prev[-1], nxt[0]
    last_text = last.text.rstrip()
    first_text = first.text.lstrip()
    if not last_text or not first_text:
        return False
    # A sentence-final mark strongly suggests the paragraph ended.
    if _SENTENCE_END_RE.search(last_text):
        return False
    if first_text[:1].isupper():
        return False
    if _HEADING_RE.match(first_text):
        return False
    # Content similarity guards against joining unrelated short lines that both
    # happen to lack punctuation.
    prev_words = normalise_for_match(" ".join(ln.text for ln in prev[-3:])).split()
    next_words = normalise_for_match(first_text).split()
    if not prev_words or not next_words:
        return False
    # Allow either a hyphen break or plain continuation; require the next line
    # to look like flowing prose rather than a caption fragment.
    return len(next_words) >= 3 or last_text.endswith(("-", "\u2010", "\u2011"))


# ---------------------------------------------------------------------------
# Section tree
# ---------------------------------------------------------------------------


def _level_from_heading(text: str, font_size: float | None, body_size: float | None) -> int:
    m = re.match(r"^(\d+(?:\.\d+)*)", text)
    if m:
        return m.group(1).count(".") + 1
    if re.match(r"^[IVXLC]+\.", text):
        return 1
    if body_size and font_size:
        if font_size >= body_size * 1.35:
            return 1
        if font_size >= body_size * 1.15:
            return 2
    return 1


def build_sections(
    paragraphs: list[Paragraph],
) -> tuple[list[dict], dict[int, list[str]], dict[int, str]]:
    """Build the heading tree.

    Returns ``(sections, path_by_index, section_id_by_index)`` where the paths
    are the "chapter title" context sent to the translator.
    """
    body_sizes = [p.font_size for p in paragraphs if p.kind == "paragraph" and p.font_size]
    body_size = median(body_sizes) if body_sizes else None

    sections: list[dict] = []
    stack: list[tuple[int, str, str]] = []  # (level, title, section_id)
    path_by_index: dict[str, list[str]] = {}
    section_id_by_index: dict[int, str] = {}

    for idx, para in enumerate(paragraphs):
        if para.kind == "heading":
            level = _level_from_heading(para.text, para.font_size, body_size)
            while stack and stack[-1][0] >= level:
                stack.pop()
            section_id = f"sec_{len(sections):04d}"
            parent = stack[-1][2] if stack else None
            section = {
                "id": section_id,
                "title": para.text,
                "normalised_title": _normalise_heading(para.text),
                "level": level,
                "number": (re.match(r"^(\d+(?:\.\d+)*)", para.text) or [None, None])[1]
                if re.match(r"^(\d+(?:\.\d+)*)", para.text)
                else None,
                "parent_id": parent,
                "start_order": idx,
                "end_order": None,
            }
            sections.append(section)
            stack.append((level, para.text, section_id))
            if sections:
                # close the previous sibling's range implicitly by order
                pass
        para.section_path = [title for _, title, _ in stack]
        para.section_id = stack[-1][2] if stack else None
        para.level = None
        path_by_index[idx] = list(para.section_path)
        if para.section_id:
            section_id_by_index[idx] = para.section_id

    for i, section in enumerate(sections):
        end = len(paragraphs) - 1
        for j in range(i + 1, len(sections)):
            if sections[j]["level"] <= section["level"]:
                end = sections[j]["start_order"] - 1
                break
        section["end_order"] = end
    return sections, path_by_index, section_id_by_index


def _normalise_heading(text: str) -> str:
    return re.sub(r"^\d+(\.\d+)*\.?\s*", "", text).strip().lower()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def analyse(
    lines_by_page: dict[int, list[TextLine]],
    params: LayoutParams | None = None,
) -> LayoutResult:
    """Run the whole layout pipeline and return ordered paragraphs.

    ``lines_by_page`` maps a 0-based page index to that page's text lines in any
    order; the returned paragraphs are in reading order.
    """
    params = params or LayoutParams()
    engine = ReadingOrder(params)
    plans: dict[int, ColumnPlan] = {}
    ordered: list[tuple[int, int, TextLine]] = []

    position = 0
    for page_index in sorted(lines_by_page):
        lines = lines_by_page[page_index]
        for ln in lines:
            ln.page_index = page_index
        if not lines:
            plans[page_index] = ColumnPlan([], 1, None, {"reason": "empty page"})
            continue
        page_ordered = sorted(lines, key=lambda ln: (ln.bbox.y0, ln.bbox.x0))
        page_height = max(ln.bbox.y1 for ln in page_ordered)
        bands = segment_bands(page_ordered, page_height, params)
        for band in bands:
            band.columns = detect_columns(page_ordered, band, page_ordered, params)
            band.full_width = len(band.columns) == 1 and _band_spans_width(band, page_ordered)
        plans[page_index] = ColumnPlan(
            bands=bands,
            n_columns=max((len(b.columns) for b in bands), default=1),
            gutter=None,
            evidence={
                "n_lines": len(page_ordered),
                "n_bands": len(bands),
                "columns_per_band": [len(b.columns) for b in bands],
                "full_width_bands": [b.index for b in bands if b.full_width],
            },
        )
        for band in bands:
            for column in band.columns:
                members = sorted(
                    (page_ordered[i] for i in column.line_indices),
                    key=lambda ln: (round(ln.bbox.y0, 4), ln.bbox.x0),
                )
                for ln in members:
                    ordered.append((page_index, position, ln))
                    position += 1

    furniture, body = classify_furniture(ordered, params)
    paragraphs = assemble_paragraphs(body, lines_by_page, params, furniture=furniture)
    sections, path_by_index, section_id_by_index = build_sections(paragraphs)
    for idx, para in enumerate(paragraphs):
        para.section_path = path_by_index.get(idx, para.section_path)
        para.section_id = section_id_by_index.get(idx, para.section_id)

    warnings: list[str] = []
    for page_index, plan in plans.items():
        if plan.n_columns > params.max_columns:
            warnings.append(
                f"page {page_index}: detected {plan.n_columns} columns, capped at {params.max_columns}"
            )
        if plan.evidence.get("n_bands", 0) > 6:
            warnings.append(
                f"page {page_index}: {plan.evidence['n_bands']} layout bands, ordering may be unreliable"
            )

    return LayoutResult(
        paragraphs=paragraphs,
        furniture=furniture,
        page_columns={p: plan.n_columns for p, plan in plans.items()},
        evidence={str(p): plan.evidence for p, plan in plans.items()},
        warnings=warnings,
    )


def _band_spans_width(band: Band, ordered: list[TextLine]) -> bool:
    band_lines = [ordered[i] for i in band.line_indices]
    if not band_lines:
        return False
    x0 = min(ln.bbox.x0 for ln in band_lines)
    x1 = max(ln.bbox.x1 for ln in band_lines)
    return (x1 - x0) > 0.72
