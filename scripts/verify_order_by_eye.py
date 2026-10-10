"""Reference reading order versus the pipeline's, side by side, with no matcher.

**This is an inspection tool, not a verification.** It returns 0 whatever it prints: there is no
threshold here and no assertion, because the whole point is that a human reads the two sequences
and judges. It is therefore deliberately absent from ``scripts/verify_all.py`` - listing a script
that cannot fail would pad a verification report with a line that means nothing.

Every alignment metric can be wrong; this one is not a metric.  It prints the reference
blocks for one page in the order the corpus generator drew them, and the pipeline's
paragraphs for the same page in the order it emitted them, so the two sequences can be
compared by eye and any divergence is unambiguous.
"""

from __future__ import annotations

import json
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from measure_layout import load_truth_blocks

CORPUS = Path("benchmarks/corpus")
NAME = "pdf_b_two_column"
PAGE = 0


def main() -> int:
    truth = json.loads((CORPUS / "truth" / f"{NAME}.json").read_text(encoding="utf-8"))
    extraction = PdfTextExtractor().extract(CORPUS / "files" / f"{NAME}.pdf")
    result = analyse(extraction.lines_by_page)
    blocks = load_truth_blocks(truth, extraction.geometry_by_page)

    page_blocks = sorted(
        (b for b in blocks if b.page_index == PAGE and b.has_text),
        key=lambda b: b.reading_order,
    )
    paras = [p for p in result.paragraphs if PAGE in (p.page_indices or [0])]

    print(f"=== {NAME} page {PAGE}: reference order (how the corpus drew it) ===")
    for b in page_blocks:
        print(
            f"  y={b.bbox[1]:.3f} x={b.bbox[0]:.3f} {b.block_type:10} "
            f"{b.text[:52]!r}"
        )

    print(f"\n=== pipeline order ({len(paras)} paragraphs) ===")
    for i, p in enumerate(paras):
        print(
            f"  {i:3} y={p.bbox.y0:.3f} x={p.bbox.x0:.3f} {p.kind:10} "
            f"{p.text[:52]!r}"
        )

    print("\n=== column extents actually used on this page ===")
    xs = sorted({round(p.bbox.x0, 3) for p in paras})
    print(f"  distinct x0 values: {xs}")

    # The bands themselves are local to the per-page loop in document_core.layout and are not kept
    # on LayoutResult; what it does keep is their summary, keyed by page index.  This script used
    # to read ``result.bands_by_page``, which has never existed on the current LayoutResult - it
    # raised AttributeError for anyone who ran it.  A stale attribute is worse than a missing
    # feature: it reads as a broken product.
    print("\n=== layout bands for this page (from the pipeline's own evidence) ===")
    summary = result.evidence.get(str(PAGE), {})
    if not summary:
        print(f"  no band summary recorded for page {PAGE}")
    else:
        columns = summary.get("columns_per_band") or []
        full_width = set(summary.get("full_width_bands") or [])
        print(f"  bands: {summary.get('n_bands')}")
        for index, count in enumerate(columns):
            span = "full width" if index in full_width else f"{count} column(s)"
            print(f"    band {index}: {span}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
