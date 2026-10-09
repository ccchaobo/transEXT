"""Verify the layout metrics measure what they claim to.

The first layout run reported region accuracy 0.02, which is implausible: the
reference regions were recorded from the same lines the extractor emits, so a
correctly matched block should overlap its reference almost exactly.  A figure that
low is more likely to be a defect in the measurement than in the pipeline, so this
prints the match, the two regions and the overlap for individual blocks and lets
the numbers be judged directly.

Run on a single-column document first: there is no column reordering to confuse
matters, so any region failure there is a measurement or a real placement bug.
"""

from __future__ import annotations

import json
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import (
    BODY_TYPES,
    adjacent_order_accuracy,
    body_coverage,
    match_blocks_to_paragraphs,
    region_accuracy,
)

CORPUS = Path("benchmarks/corpus")
NAME = "pdf_a_single_column"


def main() -> int:
    truth = json.loads((CORPUS / "truth" / f"{NAME}.json").read_text(encoding="utf-8"))
    extractor = PdfTextExtractor()
    extraction = extractor.extract(CORPUS / "files" / f"{NAME}.pdf")
    result = analyse(extraction.lines_by_page)

    # Import the loader from the measurement script so this tests the same code
    # path that produced the suspicious number.
    import sys

    sys.path.insert(0, str(Path("scripts").resolve()))
    from measure_layout import load_truth_blocks

    blocks = load_truth_blocks(truth, extraction.geometry_by_page)
    paragraphs = [
        (p.text, min(p.page_indices or [0]), tuple(p.bbox.as_list()))
        for p in result.paragraphs
    ]

    print(f"=== {NAME}: geometry sanity ===")
    print(f"  paragraphs: {len(paragraphs)}   truth blocks: {len(blocks)}")
    body_blocks = [b for b in blocks if b.block_type in BODY_TYPES]
    print(f"  body blocks: {len(body_blocks)}")

    # Are the two coordinate systems actually comparable?  Print a few blocks and a
    # few paragraphs side by side.
    print("\n  --- truth block regions (normalised display space) ---")
    for block in body_blocks[:8]:
        print(
            f"    #{block.index:3} {block.block_type:10} page={block.page_index} "
            f"bbox=({block.bbox[0]:.3f},{block.bbox[1]:.3f},"
            f"{block.bbox[2]:.3f},{block.bbox[3]:.3f}) {block.text[:42]!r}"
        )
    print("\n  --- layout paragraph regions ---")
    for index, (text, page, bbox) in enumerate(paragraphs[:8]):
        print(
            f"    p{index:3} page={page} "
            f"bbox=({bbox[0]:.3f},{bbox[1]:.3f},{bbox[2]:.3f},{bbox[3]:.3f}) "
            f"{text[:42]!r}"
        )

    report = match_blocks_to_paragraphs(body_blocks, paragraphs)
    print(f"\n  --- matches (first 12 of {len(report.matches)}) ---")
    for match in report.matches[:12]:
        overlap = 0.0
        if match.bbox is not None:
            from rr_m0.layout_metrics import _overlap_fraction

            overlap = _overlap_fraction(match.bbox, match.block.bbox)
        print(
            f"    block #{match.block.index:3} {match.block.block_type:10} "
            f"sim={match.similarity:.3f} "
            f"page {match.block.page_index}->{match.page_index} "
            f"overlap={overlap:.3f}"
        )
        print(f"        ref  bbox=({match.block.bbox[0]:.3f},{match.block.bbox[1]:.3f},"
              f"{match.block.bbox[2]:.3f},{match.block.bbox[3]:.3f}) {match.block.text[:38]!r}")
        if match.bbox:
            print(f"        para bbox=({match.bbox[0]:.3f},{match.bbox[1]:.3f},"
                  f"{match.bbox[2]:.3f},{match.bbox[3]:.3f}) {match.paragraph_text[:38]!r}")

    region = region_accuracy(report.matches)
    print(f"\n  region: {region['accuracy']} ({region['correct']}/{region['considered']})")
    print(f"    wrong_page:   {region['wrong_page'][:6]}")
    print(f"    wrong_region: {region['wrong_region'][:6]}")

    order, correct, total = adjacent_order_accuracy(
        [m.block for m in report.matches], report.matches
    )
    print(f"  order: {order} ({correct}/{total})")

    body_ref = " ".join(b.text for b in blocks if b.block_type in BODY_TYPES and b.has_text)
    hyp = " ".join(p.text for p in result.paragraphs)
    coverage, missing, length = body_coverage(body_ref, hyp)
    print(f"  coverage: {coverage:.4f} (missing {missing} of {length})")

    # Which characters are missing?  If furniture is the cause, the missing text
    # should look like running heads and page numbers.
    ref_counts = {}
    from collections import Counter

    ref = " ".join(body_ref.split())
    h = " ".join(hyp.split())
    missing_chars = Counter(ref) - Counter(h)
    print(f"\n  most common missing characters: {missing_chars.most_common(12)}")
    words = ref.split()
    missing_words = [w for w in words if w not in set(h.split())]
    print(f"  reference words not present in output: {len(missing_words)} of {len(words)}")
    print(f"    sample: {missing_words[:14]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
