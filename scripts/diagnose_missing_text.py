"""Locate the missing words in the reference, and in the PDF's own text.

The reference claims text the PDF does not contain.  This prints the reference
block that holds a missing word, its recorded drawn lines, and the PDF''s own
extraction for that region, so it is clear whether the generator failed to draw the
text or recorded more text than it drew.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
NAME = "pdf_a_single_column"
#: A word the reference has and the PDF does not.
NEEDLE = "kappa"


def main() -> int:
    truth = json.loads((CORPUS / "truth" / f"{NAME}.json").read_text(encoding="utf-8"))
    pdf = CORPUS / "files" / f"{NAME}.pdf"

    print(f"=== reference blocks containing {NEEDLE!r} ===")
    for page in truth["pages"]:
        for block in page["blocks"]:
            if NEEDLE in block["text"]:
                print(f"  page {page['index']} block #{block['index']} "
                      f"type={block['type']} n_lines={block['n_lines']}")
                print(f"    block text       : {block['text']!r}")
                print(f"    block page_indices: {block['page_indices']}")
                print(f"    line_texts ({len(block['line_texts'])}):")
                for line in block["line_texts"]:
                    print(f"      {line!r}")

    print(f"\n=== reference lines across the document containing {NEEDLE!r} ===")
    for page in truth["pages"]:
        for line in page.get("visible_lines", []):
            if NEEDLE in line:
                print(f"  page {page['index']}: {line!r}")

    doc = pymupdf.open(str(pdf))
    print(f"\n=== PDF pages whose own text contains {NEEDLE!r} ===")
    for index in range(doc.page_count):
        text = doc.load_page(index).get_text("text")
        if NEEDLE in text:
            print(f"  page {index}: FOUND")
            for line in text.splitlines():
                if NEEDLE in line:
                    print(f"      {line!r}")
        else:
            print(f"  page {index}: not present")
    doc.close()

    extraction = PdfTextExtractor().extract(pdf)
    print(f"\n=== adapter lines containing {NEEDLE!r} ===")
    found = False
    for page_index, lines in sorted(extraction.lines_by_page.items()):
        for line in lines:
            if NEEDLE in line.text:
                print(f"  page {page_index}: {line.text!r}")
                found = True
    if not found:
        print("  none - the text is not in the PDF")

    print("\n=== how many reference lines are absent from the PDF, page by page ===")
    for page in truth["pages"]:
        page_index = page["index"]
        pdf_lines = set(
            " ".join(ln.text.split())
            for ln in extraction.lines_by_page.get(page_index, [])
        )
        recorded = [" ".join(line.split()) for line in page.get("visible_lines", [])]
        absent = [line for line in recorded if line not in pdf_lines]
        print(f"  page {page_index}: {len(absent)} of {len(recorded)} recorded lines "
              f"are not in the extracted PDF text")
        for line in absent[:6]:
            print(f"      MISSING: {line!r}")
        if absent:
            for line in recorded:
                if line not in pdf_lines:
                    continue
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
