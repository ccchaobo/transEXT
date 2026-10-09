"""Compare the pipeline's reading order on one page against the corpus truth order."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
name = sys.argv[1] if len(sys.argv) > 1 else "pdf_b_two_column"
page_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0

truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
pages = truth["pages"]
print("truth keys:", list(truth.keys()))
print("truth page keys:", list(pages[0].keys()))
print()

page = pages[page_index]
blocks = page.get("blocks") or page.get("items") or []
print(f"truth block keys: {list(blocks[0].keys()) if blocks else 'none'}")
print()

on_page = list(blocks)
print(f"TRUTH page {page_index} ({len(on_page)} blocks), recorded order:")
for b in on_page:
    text = " ".join((b.get("text") or "").split())
    bbox = b.get("bbox") or [0, 0, 0, 0]
    print(f"  {b.get('index'):3} {str(b.get('block_type') or b.get('type')):11} "
          f"y={bbox[1]:.3f} x={bbox[0]:.3f} {text[:58]!r}")

extraction = PdfTextExtractor().extract(CORPUS / "files" / f"{name}.pdf")
layout = analyse(extraction.lines_by_page)
print()
print(f"PIPELINE page {page_index}, reading order:")
for position, para in enumerate(layout.paragraphs):
    if (para.page_indices or [-1])[0] != page_index:
        continue
    text = " ".join(para.text.split())
    print(f"  {position:3} {para.kind:11} y={para.bbox.y0:.3f} x={para.bbox.x0:.3f} {text[:58]!r}")
