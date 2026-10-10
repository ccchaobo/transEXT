"""Calibrate the paragraph-break threshold from the corpus, not from one page.

`assemble_paragraphs` decides whether the next line continues the current paragraph by
comparing a gap against ``paragraph_gap_factor * typical_height``.  Two things are wrong
with that: the guard on a negative gap rejects the normal slight overlap of consecutive line
boxes, and the threshold value (1.55 x box height) is larger than every real paragraph gap in
the corpus, so it can never fire.

Rather than pick a new number from one page — which is how an earlier attempt collapsed a
101-line page from 33 paragraphs to 8 — this measures the two populations the threshold has
to separate, using the corpus's own block boundaries as the answer key:

* **within** a block: the step (top-to-top distance) and the gap (foot-to-top distance)
  between consecutive lines of the same corpus block
* **between** blocks: the same quantities from the last line of one block to the first line
  of the next, in recorded reading order

If the two populations do not separate, no threshold can work and the paragraph break has to
be detected some other way.  Reporting the overlap is the point of this script; a clean
separation is not assumed.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median

CORPUS = Path("benchmarks/corpus")


def percentiles(values: list[float]) -> str:
    if not values:
        return "none"
    ordered = sorted(values)

    def at(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    return (f"n={len(ordered):5} min={ordered[0]:+.5f} p05={at(0.05):+.5f} "
            f"p25={at(0.25):+.5f} med={median(ordered):+.5f} p75={at(0.75):+.5f} "
            f"p95={at(0.95):+.5f} max={ordered[-1]:+.5f}")


def main() -> int:
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        entry["name"]
        for entry in sorted(manifest["entries"], key=lambda e: e["name"])
        if entry["kind"] == "text_pdf"
    ]

    within_step: list[float] = []
    within_gap: list[float] = []
    between_step: list[float] = []
    between_gap: list[float] = []
    heights: list[float] = []
    per_document: list[tuple[str, float, float]] = []

    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        doc_within_step: list[float] = []
        doc_between_step: list[float] = []
        for page_index, page in enumerate(truth["pages"]):
            blocks = sorted(page.get("blocks") or [], key=lambda b: b.get("reading_order", 0))
            previous_last = None
            for block in blocks:
                boxes = [
                    tuple(box)
                    for box, owner in zip(
                        block.get("line_bboxes") or [],
                        block.get("line_page_indices") or [],
                    )
                    if not owner or owner == page_index
                ]
                if not boxes:
                    continue
                for box in boxes:
                    # line_bboxes are raw PDF coordinates with y increasing upward.
                    heights.append(abs(box[3] - box[1]))
                if previous_last is not None:
                    # Only same-column pairs matter.  `_same_column_flow` also requires the
                    # two lines to overlap horizontally, so a column jump is already
                    # rejected before any threshold is consulted; counting those pairs here
                    # would make any threshold look bad for a case it never sees.
                    if previous_last[0] < boxes[0][2] and boxes[0][0] < previous_last[2]:
                        step = previous_last[3] - boxes[0][3]
                        gap = previous_last[1] - boxes[0][3]
                        between_step.append(step)
                        between_gap.append(gap)
                        doc_between_step.append(step)
                for above, below in zip(boxes, boxes[1:]):
                    step = above[3] - below[3]
                    gap = above[1] - below[3]
                    within_step.append(step)
                    within_gap.append(gap)
                    doc_within_step.append(step)
                previous_last = boxes[-1]
        doc_within = median(doc_within_step) if doc_within_step else 0.0
        doc_between = median(doc_between_step) if doc_between_step else 0.0
        per_document.append((name, doc_within, doc_between))
        print(f"  {name:34} within med={doc_within:7.2f}  between med={doc_between:7.2f}  "
              f"ratio={doc_between / doc_within if doc_within else 0:.3f}")

    print()
    print("RAW PDF POINTS (page height 842)")
    print(f"  median line box height : {median(heights):.2f}")
    print()
    print("STEP (top to top, points)")
    print(f"  within a block   : {percentiles(within_step)}")
    print(f"  between blocks   : {percentiles(between_step)}")
    print()
    print("GAP (foot to top, points; negative = boxes overlap)")
    print(f"  within a block   : {percentiles(within_gap)}")
    print(f"  between blocks   : {percentiles(between_gap)}")

    print()
    if within_step and between_step:
        max_within = max(within_step)
        min_between = min(between_step)
        overlap = sum(1 for value in between_step if value <= max_within)
        typical = median(heights)
        print(f"SEPARATION, same-column pairs only, using the GAP the code actually uses")
        print(f"  median line box height : {typical:.2f} pt")
        print(f"  largest within-block gap  : {max(within_gap):8.2f} pt")
        print(f"  smallest between-block gap: {min(between_gap):8.2f} pt")
        if min_between > max_within:
            print("  -> disjoint; any threshold in between works")
        else:
            print(f"  -> OVERLAP; {overlap} of {len(between_step)} between-block gaps "
                  f"({overlap / len(between_step):.3f}) are no larger than the largest "
                  f"within-block gap")
        best = None
        for candidate in sorted(set(within_gap + between_gap)):
            wrong = sum(1 for v in within_gap if v > candidate) + sum(
                1 for v in between_gap if v <= candidate
            )
            if best is None or wrong < best[1]:
                best = (candidate, wrong)
        if best:
            total = len(within_gap) + len(between_gap)
            print(f"  best single gap threshold: {best[0]:.2f} pt = {best[0] / typical:.3f} x "
                  f"median box height; {best[1]} of {total} pairs misjudged "
                  f"({best[1] / total:.4f})")
            print(f"  for comparison, the code's current threshold is "
                  f"1.55 x {typical:.2f} = {1.55 * typical:.2f} pt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
