"""Is the reading-order failure real, or an artefact of the matcher?

The order metric compares the pipeline's paragraph sequence against the reference
sequence only for blocks the *text matcher* managed to pair.  Two very different things
can produce the same low score:

* the pipeline really does read across columns, or
* the matcher mis-pairs some blocks, so the pairs it keeps are not the ones the ordering
  question is about.

The distinction matters before rewriting anything: if the matcher is at fault, a layout
rewrite would be aimed at a problem that does not exist.

This attributes every failed adjacent pair.  A pair is a genuine column interleave when
the two reference blocks are in *different* columns on the same page 鈥?the pipeline
visited the right column before finishing the left.  It is a same-column error when both
blocks are in one column, which means the fault is in vertical ordering, not columns.

It also measures order over *all* reference blocks, using each block's own recorded
column and position rather than the matcher, so the two readings can be compared.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import (
    BODY_TYPES,
    adjacent_order_accuracy,
    match_blocks_to_paragraphs,
)
from measure_layout import _para_page, load_truth_blocks

CORPUS = Path("benchmarks/corpus")


def _column_of(block, gutters: dict[int, float | None]) -> int:
    """Which column a reference block sits in, from the page's body gutter."""
    gutter = gutters.get(block.page_index)
    if gutter is None:
        return 0
    return 0 if block.bbox[0] < gutter else 1


def _infer_body_gutter(blocks: list) -> dict[int, float | None]:
    """Find the widest central x corridor that almost no reference block crosses.

    Taken from the reference geometry, independently of the pipeline, so it answers
    'which column is this block in' without asking the code under test.
    """
    gutters: dict[int, float | None] = {}
    by_page: dict[int, list] = {}
    for block in blocks:
        by_page.setdefault(block.page_index, []).append(block)
    for page_index, page_blocks in by_page.items():
        left = min(b.bbox[0] for b in page_blocks)
        right = max(b.bbox[2] for b in page_blocks)
        best: float | None = None
        best_width = 0.0
        # Scan candidate corridor centres; a real gutter is crossed by no line.
        steps = 200
        for i in range(30, steps - 30):
            x = left + (right - left) * i / steps
            spanning = sum(
                1 for b in page_blocks if b.bbox[0] < x < b.bbox[2]
            )
            if spanning:
                continue
            # Widen this corridor as far as it stays uncrossed.
            x0 = x
            while x0 > left:
                probe = x0 - (right - left) / steps
                if any(b.bbox[0] < probe < b.bbox[2] for b in page_blocks):
                    break
                x0 = probe
            x1 = x
            while x1 < right:
                probe = x1 + (right - left) / steps
                if any(b.bbox[0] < probe < b.bbox[2] for b in page_blocks):
                    break
                x1 = probe
            if x1 - x0 > best_width:
                best_width = x1 - x0
                best = (x0 + x1) / 2.0
        # Only call it a gutter if there are blocks on both sides.
        if best is not None:
            lhs = sum(1 for b in page_blocks if b.bbox[2] <= best)
            rhs = sum(1 for b in page_blocks if b.bbox[0] >= best)
            if lhs < 3 or rhs < 3:
                best = None
        gutters[page_index] = best
    return gutters


def main() -> int:
    extractor = PdfTextExtractor()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        e["name"]
        for e in sorted(manifest["entries"], key=lambda e: e["name"])
        if e["kind"] == "text_pdf"
    ]

    totals = Counter()
    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        result = analyse(extraction.lines_by_page)
        blocks = load_truth_blocks(truth, extraction.geometry_by_page)
        body_blocks = [b for b in blocks if b.block_type in BODY_TYPES and b.has_text]

        paragraphs = [
            (p.text, tuple(sorted(p.page_indices or [0])), tuple(p.bbox.as_list())) for p in result.paragraphs
        ]
        report = match_blocks_to_paragraphs(body_blocks, paragraphs)
        accuracy, correct, total = adjacent_order_accuracy(
            [m.block for m in report.matches], report.matches
        )

        gutters = _infer_body_gutter(body_blocks)
        by_key = {m.block.key: m for m in report.matches if m.paragraph_index is not None}
        ordered = sorted(
            (b for b in body_blocks if b.key in by_key),
            key=lambda b: b.reading_order,
        )

        cross_column = 0
        same_column = 0
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            if by_key[cur.key].paragraph_index > by_key[prev.key].paragraph_index:
                continue
            if prev.page_index != cur.page_index:
                cross_column += 1  # page-order inversion
                continue
            if _column_of(prev, gutters) != _column_of(cur, gutters):
                cross_column += 1
            else:
                same_column += 1
        totals["correct"] += correct or 0
        totals["total"] += total or 0
        totals["cross_column"] += cross_column
        totals["same_column"] += same_column
        two_col_pages = sum(1 for v in gutters.values() if v is not None)
        print(
            f"{name:30} order={accuracy if accuracy is None else round(accuracy, 4)} "
            f"({correct}/{total})  failed: cross-column={cross_column} "
            f"same-column={same_column}  pages_with_gutter={two_col_pages}"
        )

    print("\n=== totals over matched pairs ===")
    print(f"  correct      : {totals['correct']}/{totals['total']} "
          f"= {totals['correct'] / totals['total']:.4f}")
    print(f"  failures that are cross-column : {totals['cross_column']}")
    print(f"  failures that are same-column  : {totals['same_column']}")

    print("\n=== is the unmatched set a matcher problem? ===")
    print(f"  blocks matched   : {len([m for m in report.matches if m.paragraph_index is not None])}")
    print(f"  blocks unmatched : {report.unmatched_count if hasattr(report, 'unmatched_count') else 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
