"""Calibrate a font-size rule for separating a heading from the paragraph under it.

The residual segmentation defect is that a section heading is merged into the paragraph that
follows it.  The heading's box is taller than a body line, so the gap between them comes out
negative and the overlap tolerance (scaled by the *median* line height) accepts it.

A direct signal is available instead: the type size changes.  This measures whether that
signal is actually there and how wide the valley is, in the SAME-COLUMN pairs only, using the
corpus's recorded block boundaries as the answer key.

For every consecutive pair of lines it reports the ratio ``below.font_size / above.font_size``:

* **within a block** the ratio should be 1.0 if the rule is safe, since splitting inside a
  paragraph would be a new defect;
* **between blocks** the ratio is what the rule has to detect.

Pairs where the two lines are in different columns are excluded, for the same reason as in
``calibrate_paragraph_step.py``: the horizontal-overlap test already separates those, so
counting them would credit the rule with a case it never sees.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median

from document_core.layout import LayoutParams, segment_bands
from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
IOU_THRESHOLD = 0.5

#: Block types the corpus uses for a section heading.
HEADING_TYPES = {"heading", "title"}


def iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def to_raw(geometry, box) -> tuple[float, float, float, float]:
    """Pipeline line boxes are display-normalised; the truth is raw PDF.  Convert."""
    return (
        box.x0,
        geometry.media_height - box.y1,
        box.x1,
        geometry.media_height - box.y0,
    )


def load_lines(extraction, page_index: int):
    """Pipeline lines in the layout's order, with their font size."""
    lines = extraction.lines_by_page.get(page_index) or []
    geometry = extraction.geometry_by_page.get(page_index)
    if not lines or geometry is None:
        return []
    params = LayoutParams()
    out = []
    for band in segment_bands(lines, geometry.media_height, params):
        for member in band.line_indices:
            line = lines[member]
            # The geometry object owns this conversion; hand-rolling the y flip gets crop and
            # rotation wrong, and did: it matched nothing on any page.
            out.append((line, geometry.norm_to_raw_box(line.bbox).as_tuple(), band.index))
    return out


def main() -> int:
    extractor = PdfTextExtractor()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        entry["name"]
        for entry in sorted(manifest["entries"], key=lambda e: e["name"])
        if entry["kind"] == "text_pdf"
    ]

    within: list[float] = []
    between: list[float] = []
    heading_to_next: list[float] = []
    no_font = 0
    rows: list[tuple[str, str, str, float, float, str, str]] = []

    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        for page_index, page in enumerate(truth["pages"]):
            pipeline = load_lines(extraction, page_index)
            if not pipeline:
                continue
            # Attach each pipeline line to the truth line it covers, and so to its block.
            owner = [None] * len(pipeline)
            blocks = sorted(page.get("blocks") or [], key=lambda b: b.get("reading_order", 0))
            for block_index, block in enumerate(blocks):
                for box, page_of in zip(
                    block.get("line_bboxes") or [],
                    block.get("line_page_indices") or [],
                ):
                    if page_of and page_of != page_index:
                        continue
                    best, best_iou = None, IOU_THRESHOLD
                    for position, (_, raw, _) in enumerate(pipeline):
                        score = iou(tuple(box), raw)
                        if score > best_iou:
                            best, best_iou = position, score
                    if best is not None:
                        owner[best] = (block_index, block.get("type", "paragraph"))

            for position in range(1, len(pipeline)):
                above, above_raw, band_a = pipeline[position - 1]
                below, below_raw, band_b = pipeline[position]
                own_a, own_b = owner[position - 1], owner[position]
                if above.font_size is None or below.font_size is None or not below.font_size:
                    no_font += 1
                    continue
                # Same column only: the horizontal-overlap test already handles column jumps.
                if not (above_raw[0] < below_raw[2] and below_raw[0] < above_raw[2]):
                    continue
                ratio = below.font_size / above.font_size
                if own_a is None or own_b is None:
                    continue
                if own_a[0] == own_b[0]:
                    within.append(ratio)
                else:
                    between.append(ratio)
                    if own_a[1] in HEADING_TYPES:
                        heading_to_next.append(ratio)
                        if len(rows) < 14:
                            rows.append(
                                (name, own_a[1], own_b[1], ratio, above.font_size, above.text[:26], below.text[:26])
                            )

    def describe(label: str, values: list[float]) -> None:
        if not values:
            print(f"  {label:26} none")
            return
        ordered = sorted(values)

        def at(q: float) -> float:
            return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

        print(f"  {label:26} n={len(ordered):5} min={ordered[0]:.3f} p05={at(0.05):.3f} "
              f"p25={at(0.25):.3f} med={median(ordered):.3f} p75={at(0.75):.3f} max={ordered[-1]:.3f}")

    print(f"{len(names)} text PDFs; pairs with no font size: {no_font}")
    print()
    print("RATIO below.font_size / above.font_size, same-column pairs")
    describe("within a block", within)
    describe("between blocks", between)
    describe("heading -> next block", heading_to_next)

    if within and heading_to_next:
        worst_within = min(within)
        print()
        print("SEPARATION")
        print(f"  smallest within-block ratio : {worst_within:.3f}")
        print(f"  largest heading->next ratio : {max(heading_to_next):.3f}")
        missed = sum(1 for r in heading_to_next if r >= worst_within)
        print(f"  heading->next pairs a 1.0-ratio-safe rule would miss: "
              f"{missed} of {len(heading_to_next)}")

    print()
    print("HEADING -> NEXT BLOCK SAMPLES (block types, ratio, sizes, texts)")
    for name, ta, tb, ratio, size, above_text, below_text in rows:
        print(f"  {name[:22]:22} {ta:9}->{tb:9} ratio={ratio:5.3f} above={size:5.1f} "
              f"{above_text!r} | {below_text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
