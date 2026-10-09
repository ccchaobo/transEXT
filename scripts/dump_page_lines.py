"""Print a page's extracted lines in vertical order with their horizontal extent.

Written because reasoning about what the XY-cut sees, instead of looking, produced two
wrong hypotheses in a row.
"""
from __future__ import annotations

import sys
from pathlib import Path

from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
name = sys.argv[1] if len(sys.argv) > 1 else "pdf_b_two_column"
page_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 24

extraction = PdfTextExtractor().extract(CORPUS / "files" / f"{name}.pdf")
lines = extraction.lines_by_page[page_index]
geometry = extraction.geometry_by_page[page_index]

ordered = sorted(range(len(lines)), key=lambda i: (lines[i].bbox.y0, lines[i].bbox.x0))
print(f"{name} page {page_index}: {len(lines)} lines, media {geometry.media_width:.0f}pt wide")
print(f"{'y0':>7} {'y1':>7} {'x0':>7} {'x1':>7}  text")
for i in ordered[:limit]:
    line = lines[i]
    box = line.bbox
    print(f"{box.y0:7.4f} {box.y1:7.4f} {box.x0:7.4f} {box.x1:7.4f}  {line.text[:62]!r}")
