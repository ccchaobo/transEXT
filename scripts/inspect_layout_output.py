"""Run the layout pipeline on the corpus and print what it produces.

The matching rules for the metrics cannot be chosen from the design; they have to
fit what the pipeline actually emits.  This prints the paragraph list for one
two-column document next to the truth, so the rules are grounded in observation.
"""

from __future__ import annotations

import json
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
NAMES = ["pdf_b_two_column", "pdf_a_single_column", "pdf_f_two_column_wide_table"]


def main() -> int:
    extractor = PdfTextExtractor()
    for name in NAMES:
        pdf = CORPUS / "files" / f"{name}.pdf"
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(pdf)
        result = analyse(extraction.lines_by_page)

        print(f"\n{'=' * 78}\n=== {name} ===")
        print(f"  truth: {truth['layout']} columns={truth['columns']} "
              f"pages={len(truth['pages'])}")
        print(f"  extracted lines per page: "
              f"{ {p: len(l) for p, l in sorted(extraction.lines_by_page.items())} }")
        # page_columns was printed here.  It read 1 on every page of every document, so it said
        # nothing about the page while looking like it did; the field is gone.  The per-page band
        # summary is the informative replacement.
        print(f"  bands per page: "
              f"{ {p: v.get('n_bands') for p, v in sorted(result.evidence.items())} }")
        print(f"  columns per band: "
              f"{ {p: v.get('columns_per_band') for p, v in sorted(result.evidence.items())} }")
        print(f"  paragraphs={len(result.paragraphs)} furniture={len(result.furniture)} "
              f"warnings={len(result.warnings)}")
        for warning in result.warnings[:4]:
            print(f"    WARN {warning}")

        print(f"  --- first 14 layout paragraphs ---")
        for index, para in enumerate(result.paragraphs[:14]):
            pages = sorted(set(para.page_indices))
            print(
                f"    p{index:3} kind={para.kind:9} pages={pages} n_lines={len(para.lines):2} "
                f"bbox=({para.bbox.x0:.3f},{para.bbox.y0:.3f},{para.bbox.x1:.3f},{para.bbox.y1:.3f}) "
                f"cross={para.is_cross_page}"
            )
            print(f"        {para.text[:80]!r}")

        truth_blocks = [
            b for page in truth["pages"] for b in page["blocks"]
        ]
        print(f"  --- truth blocks: {len(truth_blocks)} "
              f"(with text: {sum(1 for b in truth_blocks if b['text'].strip())}) ---")
        non_table = [b for b in truth_blocks if b["type"] != "table_cell"]
        print(f"      non-table_cell blocks: {len(non_table)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
