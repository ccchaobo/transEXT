"""Which lines occupy the page centre on the two-column page?

`detect_columns` correctly refuses to split a band when the expected gutter is not
empty, so the question is whether real content crosses the centre or whether the
extracted line boxes are wrong.  Prints the lines that straddle x = 0.5.
"""

from __future__ import annotations

from pathlib import Path

from rr_adapters.pdf_extract import PdfTextExtractor

PDF = Path("benchmarks/corpus/files/pdf_b_two_column.pdf")


def main() -> int:
    extractor = PdfTextExtractor()
    extraction = extractor.extract(PDF)

    for page_index, lines in sorted(extraction.lines_by_page.items()):
        if page_index != 0:
            continue
        print(f"=== page {page_index}: {len(lines)} lines ===")

        straddling = [
            ln for ln in lines if ln.bbox.x0 < 0.48 and ln.bbox.x1 > 0.52
        ]
        print(f"\n  lines straddling the centre (x0<0.48 and x1>0.52): {len(straddling)}")
        for ln in sorted(straddling, key=lambda l: l.bbox.y0):
            print(
                f"    y={ln.bbox.y0:.3f} x=({ln.bbox.x0:.3f}..{ln.bbox.x1:.3f}) "
                f"w={ln.bbox.width:.3f} {ln.text[:70]!r}"
            )

        wide = [ln for ln in lines if ln.bbox.width > 0.5]
        print(f"\n  lines wider than half the page: {len(wide)}")
        for ln in sorted(wide, key=lambda l: l.bbox.y0):
            print(
                f"    y={ln.bbox.y0:.3f} x=({ln.bbox.x0:.3f}..{ln.bbox.x1:.3f}) "
                f"w={ln.bbox.width:.3f} {ln.text[:70]!r}"
            )

        print("\n  --- every line, ordered by y, first 40 ---")
        for ln in sorted(lines, key=lambda l: (l.bbox.y0, l.bbox.x0))[:40]:
            print(
                f"    y={ln.bbox.y0:.3f} x=({ln.bbox.x0:.3f}..{ln.bbox.x1:.3f}) "
                f"sz={ln.font_size} {ln.text[:58]!r}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
