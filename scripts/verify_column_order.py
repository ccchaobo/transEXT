"""Does the pipeline read a two-column page column-major, in its own output?

The acceptance metric says yes, but that metric was wrong twice, so this checks the
pipeline's own paragraph sequence directly against the page's geometry and prints it,
with no text matcher involved.  On a two-column page the paragraphs should run down the
left column and then down the right one.
"""

from __future__ import annotations

import json
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from measure_layout import load_truth_blocks

CORPUS = Path("benchmarks/corpus")
NAMES = ["pdf_b_two_column", "pdf_f_two_column_wide_table", "pdf_h_no_references"]


def main() -> int:
    extractor = PdfTextExtractor()
    for name in NAMES:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        result = analyse(extraction.lines_by_page)

        # Which page does each paragraph belong to, and in what order did it come out?
        by_page: dict[int, list] = {}
        for para in result.paragraphs:
            page = min(para.page_indices or [0])
            by_page.setdefault(page, []).append(para)

        print("=" * 78)
        print(f"{name}  truth layout={truth['layout']} columns={truth.get('columns')}")
        for page_index in sorted(by_page):
            paras = by_page[page_index]
            print(f"\n  page {page_index}: {len(paras)} paragraphs in pipeline order")
            print("    idx  x0     x1     y0     y1     text")
            for i, para in enumerate(paras[:26]):
                b = para.bbox
                print(
                    f"    {i:3}  {b.x0:.3f}  {b.x1:.3f}  {b.y0:.3f}  {b.y1:.3f}  "
                    f"{para.text[:44]!r}"
                )
            # Column-major means: x0 values should not alternate between the left and
            # right column once a column has started, except for full-width elements.
            centres = [(p.bbox.x0 + p.bbox.x1) / 2 for p in paras]
            left = [c for c in centres if c < 0.5]
            right = [c for c in centres if c >= 0.5]
            if left and right:
                order = "".join("L" if c < 0.5 else "R" for c in centres)
                print(f"    column sequence: {order}")
                # Count how often the sequence switches side.
                switches = sum(1 for a, b in zip(order, order[1:]) if a != b)
                print(f"    L/R switches: {switches} over {len(order)} paragraphs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
