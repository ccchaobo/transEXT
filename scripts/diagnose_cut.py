"""Show exactly what the XY-cut decides at the top of a page, instead of inferring it."""
from __future__ import annotations

import sys
from pathlib import Path

from document_core.layout import (
    LayoutParams,
    _horizontal_cut,
    _split_regions,
    _vertical_cut,
    _x_profile,
    _y_occupancy,
    _find_gutters,
    _widest_gap,
    _CUT_BINS,
    _CUT_ROWS,
    segment_bands,
)
from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
name = sys.argv[1] if len(sys.argv) > 1 else "pdf_b_two_column"
page_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0

extraction = PdfTextExtractor().extract(CORPUS / "files" / f"{name}.pdf")
lines = extraction.lines_by_page[page_index]
geometry = extraction.geometry_by_page[page_index]
params = LayoutParams()

print(f"{name} page {page_index}: {len(lines)} lines, "
      f"media {geometry.media_width:.0f}x{geometry.media_height:.0f}")
print()

indices = list(range(len(lines)))
y0 = min(lines[i].bbox.y0 for i in indices)
y1 = max(lines[i].bbox.y1 for i in indices)
x0 = min(lines[i].bbox.x0 for i in indices)
x1 = max(lines[i].bbox.x1 for i in indices)
heights = sorted(lines[i].height for i in indices if lines[i].height > 0)
typical = heights[len(heights) // 2] if heights else 0.0
print(f"region: x {x0:.3f}-{x1:.3f}  y {y0:.3f}-{y1:.3f}  typical line height {typical:.5f}")
print()

# --- horizontal candidates -------------------------------------------------
occupancy = _y_occupancy(lines, indices, _CUT_ROWS)
print(f"horizontal: y-occupancy over {_CUT_ROWS} rows")
low_rows = [r for r, v in enumerate(occupancy) if v <= params.cut_max_occupancy]
print(f"  rows with low occupancy (<= {params.cut_max_occupancy}): {len(low_rows)}")
runs = []
start = None
for r in range(_CUT_ROWS):
    if occupancy[r] <= params.cut_max_occupancy:
        if start is None:
            start = r
    elif start is not None:
        runs.append((start, r))
        start = None
if start is not None:
    runs.append((start, _CUT_ROWS))
print(f"  low-occupancy runs (row0,row1,height, y-as-fraction):")
y_span = y1 - y0
for a, b in runs:
    frac_a = a / _CUT_ROWS
    frac_b = b / _CUT_ROWS
    ya = y0 + frac_a * y_span
    yb = y0 + frac_b * y_span
    ok = (b - a) / _CUT_ROWS * y_span > params.band_gap_factor * typical and 0 < a and b < _CUT_ROWS
    print(f"    rows {a:3}-{b:3} h={b - a:3} y {ya:.3f}-{yb:.3f} "
          f"gap={((b - a) / _CUT_ROWS * y_span):.5f} needed>{params.band_gap_factor * typical:.5f} "
          f"{'CUTTABLE' if ok else ''}")
gap = _widest_gap(occupancy, invert=False)
print(f"  _widest_gap chose rows {gap[0]}-{gap[1]} height {gap[2]}")
print()

# --- vertical candidates ---------------------------------------------------
profile = _x_profile(lines, indices, _CUT_BINS)
min_bins = max(1, int(params.gutter_min_width * _CUT_BINS))
cands = [
    (a, b)
    for a, b in _find_gutters(profile, min_bins, max_val=params.cut_max_occupancy)
    if a > 0 and b < _CUT_BINS
]
print(f"vertical: {len(cands)} corridor candidates "
      f"(min width {min_bins} bins, max occupancy {params.cut_max_occupancy})")
for a, b in cands:
    cut_x = x0 + (a + (b - a) / 2.0) / _CUT_BINS * (x1 - x0)
    left = [i for i in indices if lines[i].bbox.x1 <= cut_x]
    right = [i for i in indices if lines[i].bbox.x0 >= cut_x]
    span = [i for i in indices if i not in set(left) and i not in set(right)]
    note = ""
    if left and right:
        lt = min(lines[i].bbox.y0 for i in left)
        lb = max(lines[i].bbox.y1 for i in left)
        rt = min(lines[i].bbox.y0 for i in right)
        rb = max(lines[i].bbox.y1 for i in right)
        overlap = min(lb, rb) - max(lt, rt)
        shorter = min(lb - lt, rb - rt)
        ratio = overlap / shorter if shorter > 0 else -1
        note = (f"overlap_ratio={ratio:.3f} (need {params.cut_min_column_overlap}) "
                f"left_y={lt:.3f}-{lb:.3f} right_y={rt:.3f}-{rb:.3f}")
    print(f"  rows {a:3}-{b:3} cut_x={cut_x:.4f} left={len(left):3} spanning={len(span):3} "
          f"right={len(right):3}  {note}")
print()

# --- what each cut actually returns ---------------------------------------
h = _horizontal_cut(lines, indices, params, 0, typical, y0, y1)
v = _vertical_cut(lines, indices, params, 0, x0, x1)
print(f"_horizontal_cut returned: {None if h is None else [len(g) for g in h]}")
print(f"_vertical_cut   returned: {None if v is None else [len(g) for g in v]}")
print()

groups = _split_regions(lines, indices, params)
print(f"_split_regions produced {len(groups)} groups, sizes: {[len(g) for g in groups]}")
print()
for order, members in enumerate(groups):
    gy0 = min(lines[i].bbox.y0 for i in members)
    gy1 = max(lines[i].bbox.y1 for i in members)
    gx0 = min(lines[i].bbox.x0 for i in members)
    gx1 = max(lines[i].bbox.x1 for i in members)
    sample = " | ".join(lines[i].text[:34] for i in members[:3])
    print(f"  group {order:2} n={len(members):3} x {gx0:.3f}-{gx1:.3f} y {gy0:.3f}-{gy1:.3f}  {sample!r}")
