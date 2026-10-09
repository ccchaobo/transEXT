"""Show the adjacent pairs that come out in the wrong order, with both texts.

The attribution pass showed the failures are mostly *within* a column, so the column
structure is not the main cause.  This prints the failing pairs themselves so the cause
is visible rather than inferred: whether the pipeline swapped two paragraphs, moved a
heading, or whether the matcher paired the wrong paragraph to a block and created an
apparent inversion out of nothing.
"""

from __future__ import annotations

import json
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
NAMES = [
    "pdf_a_single_column",
    "pdf_b_two_column",
    "pdf_c_formula_footnote",
    "pdf_f_two_column_wide_table",
    "pdf_h_no_references",
]


def main() -> int:
    extractor = PdfTextExtractor()
    for name in NAMES:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        result = analyse(extraction.lines_by_page)
        blocks = load_truth_blocks(truth, extraction.geometry_by_page)
        body = [b for b in blocks if b.block_type in BODY_TYPES and b.has_text]
        paragraphs = [
            (p.text, tuple(sorted(p.page_indices or [0])), tuple(p.bbox.as_list())) for p in result.paragraphs
        ]
        report = match_blocks_to_paragraphs(body, paragraphs)
        accuracy, correct, total = adjacent_order_accuracy(
            [m.block for m in report.matches], report.matches
        )
        print("=" * 78)
        print(f"{name}   order={accuracy if accuracy is None else round(accuracy, 4)} "
              f"({correct}/{total})  paragraphs={len(paragraphs)}")

        by_key = {m.block.key: m for m in report.matches if m.paragraph_index is not None}
        ordered = sorted((b for b in body if b.key in by_key), key=lambda b: b.reading_order)
        shown = 0
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            pi_prev = by_key[prev.key].paragraph_index
            pi_cur = by_key[cur.key].paragraph_index
            if pi_cur > pi_prev:
                continue
            shown += 1
            if shown > 6:
                break
            print(f"\n  INVERSION  ref#{prev.index}(p{prev.page_index},y={prev.bbox[1]:.3f}) "
                  f"-> ref#{cur.index}(p{cur.page_index},y={cur.bbox[1]:.3f})")
            print(f"    ref prev : {prev.block_type:9} {prev.text[:64]!r}")
            print(f"    ref next : {cur.block_type:9} {cur.text[:64]!r}")
            print(f"    got prev -> paragraph #{pi_prev}")
            print(f"       {paragraphs[pi_prev][0][:70]!r}")
            print(f"       paragraph page={paragraphs[pi_prev][1]} "
                  f"bbox={tuple(round(v, 3) for v in paragraphs[pi_prev][2])}")
            print(f"    got next -> paragraph #{pi_cur}")
            print(f"       {paragraphs[pi_cur][0][:70]!r}")
            print(f"       paragraph page={paragraphs[pi_cur][1]} "
                  f"bbox={tuple(round(v, 3) for v in paragraphs[pi_cur][2])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
