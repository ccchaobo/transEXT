"""Why does a two-column page report one column?

Prints the vertical bands, the x projection profile of each band, and the gutter
runs the detector finds, so the failure can be located rather than guessed at.
"""

from __future__ import annotations

from pathlib import Path

from document_core.layout import (
    LayoutParams,
    _find_gutters,
    _projection_profile,
    segment_bands,
)
from rr_adapters.pdf_extract import PdfTextExtractor

PDF = Path("benchmarks/corpus/files/pdf_b_two_column.pdf")
N_BINS = 200


def describe_profile(profile: list[float]) -> str:
    """A compact picture of where the page is occupied along x."""
    # 40 buckets, so the shape is visible on one line.
    step = N_BINS // 40
    chars = []
    for b in range(40):
        block = profile[b * step : (b + 1) * step]
        peak = max(block) if block else 0.0
        if peak == 0.0:
            chars.append(".")
        elif peak < 0.05:
            chars.append("-")
        elif peak < 0.25:
            chars.append("+")
        else:
            chars.append("#")
    return "".join(chars)


def main() -> int:
    params = LayoutParams()
    extractor = PdfTextExtractor()
    extraction = extractor.extract(PDF)

    for page_index, lines in sorted(extraction.lines_by_page.items()):
        if page_index != 0:
            continue
        print(f"=== page {page_index}: {len(lines)} lines ===")
        ordered = sorted(lines, key=lambda ln: (ln.bbox.y0, ln.bbox.x0))
        page_height = max(ln.bbox.y1 for ln in ordered)
        xs = [ln.bbox.x0 for ln in ordered]
        xe = [ln.bbox.x1 for ln in ordered]
        print(f"  x span: {min(xs):.3f} .. {max(xe):.3f}   page height: {page_height:.3f}")

        bands = segment_bands(ordered, page_height, params)

        # Whole-page profile for reference.
        page_profile = _projection_profile(ordered, N_BINS)
        print(f"\n  whole page profile  [{describe_profile(page_profile)}]")
        print(f"    legend: '#'>=0.25  '+'>=0.05  '-'<0.05  '.'=0.00   (40 buckets)")

        print(f"\n  bands: {len(bands)}")
        for band in bands:
            band_lines = [ordered[i] for i in band.line_indices]
            profile = _projection_profile(band_lines, N_BINS)
            min_width = max(1, int(params.gutter_min_width * N_BINS))
            gutters = _find_gutters(profile, min_width)
            print(
                f"    band {band.index:2}: lines={len(band_lines):3} "
                f"y={band.y0:.3f}..{band.y1:.3f}"
            )
            print(f"      profile [{describe_profile(profile)}]")
            print(
                f"      gutter runs (bins, need >={min_width}): {gutters}"
            )
            # Where is the ink actually absent?
            empty = [i for i, v in enumerate(profile) if v <= 0.02]
            if empty:
                print(
                    f"      empty bins: {len(empty)}, "
                    f"x range {empty[0] / N_BINS:.3f}..{empty[-1] / N_BINS:.3f}"
                )
            else:
                print("      empty bins: NONE - there is no empty vertical run")
            # Peak occupancy inside the expected gutter (page centre).
            centre = [profile[i] for i in range(95, 106)]
            print(f"      occupancy near x=0.5 (bins 95-105): "
                  f"min={min(centre):.3f} max={max(centre):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
