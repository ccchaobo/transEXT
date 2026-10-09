"""Does `likely_scanned` actually fire on the scanned corpus files?

The coverage metric will exclude pages the extractor flags as scanned, so that flag
has to be right.  If it never fires, the exclusion would silently do nothing; if it
fires on text pages, it would hide real failures.  This checks it against the corpus,
whose scan status is known from how it was generated.
"""

from __future__ import annotations

import json
from pathlib import Path

from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))

extractor = PdfTextExtractor()
for entry in sorted(manifest["entries"], key=lambda e: (e["kind"], e["name"])):
    if entry["kind"] not in ("text_pdf", "scanned_pdf", "mixed_pdf"):
        continue
    pdf = CORPUS / "files" / f"{entry['name']}.pdf"
    if not pdf.exists():
        continue
    extraction = extractor.extract(pdf)
    flags = [
        (i.index, i.text_chars, i.likely_scanned) for i in extraction.page_infos
    ]
    print(f"{entry['name']:32} kind={entry['kind']:12}")
    for index, chars, scanned in flags:
        print(f"    page {index}: text_chars={chars:5} likely_scanned={scanned}")
