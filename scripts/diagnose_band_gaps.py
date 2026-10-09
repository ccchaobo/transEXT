"""Why does the extent split not fire on pdf_b_two_column page 0?

Prints the lines in y order with their x extents and the vertical gap between
consecutive lines, so it is clear whether the header is separated by a vertical gap,
by a disjoint x extent, or by neither.
"""

from __future__ import annotations

from pathlib import Path

from rr_adapters.pdf_extract import PdfTextExtractor

PDF = Path("benchmarks/corpus/files/pdf_b_two_column.pdf")


def main() -> int:
    extraction = PdfTextExtractor().extract(PDF)
    lines = sorted(extraction.lines_by_page[0], key=lambda ln: ln.y_centre)
    heights = sorted(ln.height for ln in lines if ln.height > 0)
    median_height = heights[len(heights) // 2]
    print(f"  lines={len(lines)} median height={median_height:.4f} "
          f"gap threshold={1.9 * median_height:.4f}")

    print("\n  y0     y1     x0     x1     gap     overlap_frac  text")
    span_x0, span_x1 = lines[0].bbox.x0, lines[0].bbox.x1
    for i, line in enumerate(lines[:16]):
        gap = line.bbox.y0 - lines[i - 1].bbox.y1 if i else 0.0
        overlap = min(span_x1, line.bbox.x1) - max(span_x0, line.bbox.x0)
        narrower = min(span_x1 - span_x0, line.bbox.width)
        frac = overlap / narrower if narrower > 0 else 1.0
        print(
            f"  {line.bbox.y0:.4f} {line.bbox.y1:.4f} {line.bbox.x0:.4f} "
            f"{line.bbox.x1:.4f} {gap:7.4f} {frac:11.3f}  {line.text[:44]!r}"
        )
        span_x0 = min(span_x0, line.bbox.x0)
        span_x1 = max(span_x1, line.bbox.x1)

    print("\n  --- every line, index 16 onward, gaps only where they exceed 0.01 ---")
    for i in range(1, len(lines)):
        gap = lines[i].bbox.y0 - lines[i - 1].bbox.y1
        if gap > 0.004:
            print(
                f"    between y={lines[i - 1].bbox.y1:.4f} and y={lines[i].bbox.y0:.4f}: "
                f"gap={gap:.4f}  "
                f"{lines[i - 1].text[:26]!r} -> {lines[i].text[:26]!r}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
