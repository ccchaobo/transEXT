"""Per-line reconciliation: was every recorded drawn line really drawn?

The reference claims lines the PDF appears not to contain, and the totals are close
enough that an arithmetic mistake in the comparison is a live possibility.  This
reconciles line by line: for each line the generator recorded as drawn, find the
extracted line with the same text, and report any that have no counterpart.  It also
prints the exact per-page character totals so the earlier discrepancy can be checked
rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import BODY_TYPES, body_coverage

CORPUS = Path("benchmarks/corpus")
NAMES = ["pdf_a_single_column", "pdf_b_two_column", "pdf_c_formula_footnote"]


def main() -> int:
    extractor = PdfTextExtractor()
    for name in NAMES:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        print(f"\n{'=' * 76}\n=== {name} ===")

        total_recorded = 0
        total_missing = 0
        for page in truth["pages"]:
            page_index = page["index"]
            recorded = [" ".join(l.split()) for l in page.get("visible_lines", [])]
            extracted = [
                " ".join(ln.text.split())
                for ln in extraction.lines_by_page.get(page_index, [])
            ]
            extracted_set = set(extracted)
            absent = [line for line in recorded if line not in extracted_set]

            ref_text = " ".join(page.get("visible_text", "").split())
            ext_text = " ".join(extracted)
            cov, missing, length = body_coverage(ref_text, ext_text)

            print(
                f"  page {page_index}: recorded_lines={len(recorded):3} "
                f"extracted_lines={len(extracted):3} absent={len(absent):3}  "
                f"ref_chars={len(ref_text):5} ext_chars={len(ext_text):5} "
                f"missing={missing:4} coverage={cov:.4f}"
            )
            for line in absent[:5]:
                print(f"      ABSENT: {line[:88]!r}")
            total_recorded += len(recorded)
            total_missing += len(absent)

        print(f"  TOTAL recorded lines={total_recorded} absent from extraction={total_missing}")

        # Which block types account for whatever is still missing?
        print("  --- coverage by block type (whole document) ---")
        by_type: dict[str, list[str]] = {}
        for page in truth["pages"]:
            for block in page["blocks"]:
                if not block["text"].strip():
                    continue
                by_type.setdefault(block.get("type") or "paragraph", []).append(block["text"])
        all_extracted = " ".join(
            ln.text for lines in extraction.lines_by_page.values() for ln in lines
        )
        for block_type, texts in sorted(by_type.items()):
            ref = " ".join(texts)
            cov, missing, length = body_coverage(ref, all_extracted)
            flag = "" if block_type in BODY_TYPES else "   (not a body type)"
            print(
                f"    {block_type:12} blocks={len(texts):3} chars={length:5} "
                f"coverage={cov:.4f} missing={missing:4}{flag}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
