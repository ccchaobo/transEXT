"""Do cross-page paragraphs get their text counted on every page they touch?

If a paragraph spans pages 0 and 1 and both pages record the whole paragraph as
"visible" lines, then the reference contains that text twice while the PDF contains
it once.  That would make a coverage metric report missing text that was never
missing, which is exactly the shape of the 0.96 result.
"""

from __future__ import annotations

import json
from pathlib import Path

from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
NAME = "pdf_a_single_column"

truth = json.loads((CORPUS / "truth" / f"{NAME}.json").read_text(encoding="utf-8"))
extraction = PdfTextExtractor().extract(CORPUS / "files" / f"{NAME}.pdf")

print(f"=== {NAME}: duplicated visible lines in the reference ===")
seen: dict[str, list[int]] = {}
for page in truth["pages"]:
    for line in page.get("visible_lines", []):
        seen.setdefault(" ".join(line.split()), []).append(page["index"])

duplicates = {line: pages for line, pages in seen.items() if len(pages) > 1}
print(f"  recorded lines appearing on more than one page: {len(duplicates)}")
for line, pages in list(duplicates.items())[:8]:
    print(f"    pages {pages}: {line[:78]!r}")

print(f"\n=== is the duplicated text physically on each page? ===")
for line, pages in list(duplicates.items())[:4]:
    for page_index in pages:
        pdf_text = " ".join(
            ln.text for ln in extraction.lines_by_page.get(page_index, [])
        )
        present = " ".join(line.split()) in " ".join(pdf_text.split())
        print(f"    page {page_index}: present={present}  {line[:56]!r}")

print(f"\n=== per-page reference size against unique content ===")
total_recorded = 0
total_unique = 0
for page in truth["pages"]:
    lines = [" ".join(l.split()) for l in page.get("visible_lines", [])]
    total_recorded += len(" ".join(lines))
    total_unique += len(" ".join(dict.fromkeys(lines)))
print(f"  reference characters counted once per page : {total_recorded}")
print(f"  reference characters unique within a page  : {total_unique}")
print(f"  duplicated by cross-page blocks            : {total_recorded - total_unique}")

pdf_chars = sum(
    len(" ".join(ln.text.split()))
    for lines in extraction.lines_by_page.values()
    for ln in lines
)
print(f"\n  total reference body characters : {total_recorded}")
print(f"  total PDF characters            : {pdf_chars}")
print(f"  difference                      : {total_recorded - pdf_chars}")
