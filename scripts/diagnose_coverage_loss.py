"""Which reference text does the layout pipeline lose, and where?

Coverage is 0.9072, so about 9% of reference characters are unaccounted for.  The
missing characters are ordinary letters rather than rare symbols, which means whole
words are disappearing rather than being mis-normalised.  This locates them: which
page, which block type, and whether the text reaches the extracted lines at all.

That last distinction matters.  If a line is present in the extractor's output but
absent from the pipeline's paragraphs, layout dropped it.  If it never reached the
extractor, the loss is in extraction and the layout module is innocent.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import BODY_TYPES, body_coverage

CORPUS = Path("benchmarks/corpus")
NAMES = ["pdf_a_single_column", "pdf_b_two_column", "pdf_c_formula_footnote"]


def main() -> int:
    extractor = PdfTextExtractor()
    for name in NAMES:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        result = analyse(extraction.lines_by_page)

        print(f"\n{'=' * 76}\n=== {name} ===")

        # Reference body text, and what the *extractor* produced, per page.
        for page in truth["pages"]:
            page_index = page["index"]
            ref = " ".join(
                b["text"] for b in page["blocks"]
                if (b.get("type") or "paragraph") in BODY_TYPES and b["text"].strip()
            )
            extracted = " ".join(
                ln.text for ln in extraction.lines_by_page.get(page_index, [])
            )
            cov_extract, miss_e, len_e = body_coverage(ref, extracted)
            print(
                f"  page {page_index}: reference={len_e:5} chars  "
                f"extractor coverage={cov_extract:.4f} (missing {miss_e})"
            )

        # And what the pipeline's paragraphs produced, per page.
        print("  --- pipeline paragraphs per page ---")
        para_by_page: dict[int, list[str]] = {}
        for para in result.paragraphs:
            page_index = min(para.page_indices or [0])
            para_by_page.setdefault(page_index, []).append(para.text)
        for page in truth["pages"]:
            page_index = page["index"]
            ref = " ".join(
                b["text"] for b in page["blocks"]
                if (b.get("type") or "paragraph") in BODY_TYPES and b["text"].strip()
            )
            produced = " ".join(para_by_page.get(page_index, []))
            cov, miss, length = body_coverage(ref, produced)
            print(
                f"  page {page_index}: reference={length:5} chars  "
                f"pipeline coverage={cov:.4f} (missing {miss})  "
                f"paragraphs={len(para_by_page.get(page_index, []))}"
            )

        # Which words are missing from the extractor, and which from the pipeline?
        print("  --- missing whole words ---")
        for page in truth["pages"][:2]:
            page_index = page["index"]
            ref = " ".join(
                b["text"] for b in page["blocks"]
                if (b.get("type") or "paragraph") in BODY_TYPES and b["text"].strip()
            )
            ref_words = ref.split()
            extracted_words = set(
                w for ln in extraction.lines_by_page.get(page_index, []) for w in ln.text.split()
            )
            produced_words = set(
                w for text in para_by_page.get(page_index, []) for w in text.split()
            )
            missing_from_extract = [w for w in ref_words if w not in extracted_words]
            missing_from_pipeline = [w for w in ref_words if w not in produced_words]
            print(
                f"  page {page_index}: {len(missing_from_extract)} words missing from "
                f"extractor, {len(missing_from_pipeline)} from pipeline "
                f"(of {len(ref_words)})"
            )
            print(f"      not extracted : {missing_from_extract[:16]}")
            print(f"      not in output : {missing_from_pipeline[:16]}")

        # Is anything in the extractor but nowhere in the output?
        extracted_all = Counter(
            w for lines in extraction.lines_by_page.values() for ln in lines for w in ln.text.split()
        )
        produced_all = Counter(w for para in result.paragraphs for w in para.text.split())
        lost = extracted_all - produced_all
        print(f"  words present in extraction but absent from output: {sum(lost.values())}")
        print(f"      sample: {list(lost)[:20]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
