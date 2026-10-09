"""How much of the match set is cross-page pairings?

A block and the paragraph it is matched to must be on the same page: a block's text is
drawn on one page, and a paragraph's text comes from one page.  If the matcher is free
to pair across pages it will happily attach a page-0 sentence to an acknowledgement on
page 2 whenever they happen to share common characters, and every such pair invents an
ordering inversion that does not correspond to any real reading-order mistake.

This counts those pairings so the size of the problem is known before the matcher is
constrained.
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
        body = [b for b in blocks if b.block_type in BODY_TYPES and b.has_text]
        paragraphs = [
            (p.text, tuple(sorted(p.page_indices or [0])), tuple(p.bbox.as_list())) for p in result.paragraphs
        ]
        report = match_blocks_to_paragraphs(body, paragraphs)

        matched = [m for m in report.matches if m.paragraph_index is not None]
        cross_page = [
            m
            for m in matched
            if m.block.page_index not in paragraphs[m.paragraph_index][1]
        ]
        totals["matched"] += len(matched)
        totals["cross_page"] += len(cross_page)
        accuracy, correct, total = adjacent_order_accuracy(
            [m.block for m in report.matches], report.matches
        )
        print(
            f"{name:30} matched={len(matched):4} cross-page={len(cross_page):4} "
            f"({len(cross_page) / max(1, len(matched)):.0%})  order="
            f"{accuracy if accuracy is None else round(accuracy, 4)}"
        )
        for m in cross_page[:3]:
            print(
                f"      block#{m.block.index} page={m.block.page_index} "
                f"{m.block.text[:44]!r}"
            )
            print(
                f"        -> paragraph page={paragraphs[m.paragraph_index][1]} "
                f"{paragraphs[m.paragraph_index][0][:44]!r}"
            )

    print("\n=== totals ===")
    print(f"  matched pairs       : {totals['matched']}")
    print(f"  cross-page pairings : {totals['cross_page']} "
          f"({totals['cross_page'] / max(1, totals['matched']):.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
