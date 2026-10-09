"""Reference reading order versus the pipeline's, side by side, with no matcher.

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
    print(f"  page_columns detected: {result.page_columns}")
    for band in result.bands_by_page.get(PAGE, []):
        print(
            f"  band {band.index}: lines={len(band.line_indices)} "
            f"y={band.y0:.3f}..{band.y1:.3f} columns={len(band.columns)} "
            f"full_width={band.full_width}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
