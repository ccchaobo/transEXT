"""Show why lines were merged into one paragraph, using the real gap distribution."""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import median

from document_core.layout import LayoutParams, _same_column_flow, _vertical_gap, segment_bands
from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
name = sys.argv[1] if len(sys.argv) > 1 else "pdf_b_two_column"
page_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
group_index = int(sys.argv[3]) if len(sys.argv) > 3 else 1

params = LayoutParams()
extraction = PdfTextExtractor().extract(CORPUS / "files" / f"{name}.pdf")
lines = extraction.lines_by_page[page_index]
geometry = extraction.geometry_by_page[page_index]
bands = segment_bands(lines, geometry.media_height, params)
members = bands[group_index].line_indices

heights = [lines[i].height for i in members if lines[i].height > 0]
typical = median(heights) if heights else 0.0
print(f"{name} page {page_index} group {group_index}: {len(members)} lines")
print(f"median line height {typical:.5f}  -> paragraph gap threshold "
      f"{params.paragraph_gap_factor} x {typical:.5f} = {params.paragraph_gap_factor * typical:.5f}")
print(f"indent threshold {params.paragraph_indent} * 3 = {params.paragraph_indent * 3:.5f}")
print()
print(f"{'#':>3} {'gap':>9} {'dx0':>9} {'y0':>7} {'x0':>7} {'size':>5}  merge? text")
font_attr = "font_size" if hasattr(lines[members[0]], "font_size") else None
for position in range(len(members)):
    i = members[position]
    line = lines[i]
    size = getattr(line, font_attr) if font_attr else None
    if position == 0:
        print(f"{position:3} {'-':>9} {'-':>9} {line.bbox.y0:7.4f} {line.bbox.x0:7.4f} "
              f"{(size if size else 0):5.1f}  {'':6} {line.text[:40]!r}")
        continue
    above = lines[members[position - 1]]
    gap = _vertical_gap(above, line)
    dx0 = line.bbox.x0 - above.bbox.x0
    keep = _same_column_flow(above, line, geometry.media_height, params, typical)
    marker = "merge" if keep else "BREAK"
    print(f"{position:3} {gap:9.5f} {dx0:9.5f} {line.bbox.y0:7.4f} {line.bbox.x0:7.4f} "
          f"{(size if size else 0):5.1f}  {marker:6} {line.text[:40]!r}")

breaks = sum(
    1
    for p in range(1, len(members))
    if not _same_column_flow(lines[members[p - 1]], lines[members[p]], geometry.media_height, params, typical)
)
print()
print(f"breaks inside this group: {breaks}  ->  this group becomes ~{breaks + 1} paragraphs")
