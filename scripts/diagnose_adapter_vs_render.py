"""Where does the adapter place a known string, and where is it actually rendered?

This is the decisive measurement.  The title of a single-column page is at the top
of the page.  The script finds the adapter's box for that exact string, then renders
the page and finds those pixels, and compares the two.

If they agree, the adapter's coordinate handling is correct and the layout metric's
region comparison is what is wrong.  If they disagree by a vertical mirror, the
adapter applies the transform to boxes that are not in raw PDF space.
"""

from __future__ import annotations

import pymupdf
from PIL import Image

from rr_adapters.pdf_extract import PdfTextExtractor, page_geometry

PDF = "benchmarks/corpus/files/pdf_a_single_column.pdf"
NEEDLE = "Structured Extraction for Single-Column Articles"
DPI = 100


def find_ink_rows(image: Image.Image) -> tuple[int, int]:
    """First and last row containing dark pixels, over the whole image."""
    grey = image.convert("L")
    width, height = grey.size
    pixels = grey.load()
    rows = []
    for y in range(height):
        for x in range(0, width, 3):
            if pixels[x, y] < 128:
                rows.append(y)
                break
    if not rows:
        return -1, -1
    return rows[0], rows[-1]


def main() -> int:
    extractor = PdfTextExtractor()
    extraction = extractor.extract(PDF)

    print(f"=== adapter's box for {NEEDLE!r} ===")
    found = None
    for page_index, lines in sorted(extraction.lines_by_page.items()):
        for line in lines:
            if NEEDLE[:40] in line.text:
                found = (page_index, line)
                print(
                    f"  page {page_index}: bbox=({line.bbox.x0:.3f},{line.bbox.y0:.3f},"
                    f"{line.bbox.x1:.3f},{line.bbox.y1:.3f})"
                )
                break
    if found is None:
        print("  NOT FOUND in adapter output - the title was dropped or altered")
        return 1

    page_index, line = found

    doc = pymupdf.open(PDF)
    page = doc.load_page(page_index)
    geo = page_geometry(page)
    print(f"\n=== page {page_index} facts ===")
    print(f"  page.rect={page.rect} rotation={page.rotation}")
    print(f"  geometry.display_height={geo.display_height} rotation={geo.rotation}")

    pixmap = page.get_pixmap(dpi=DPI, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    width, height = image.size
    print(f"  render: {width}x{height} px at {DPI} dpi")

    first_ink, last_ink = find_ink_rows(image)
    print(f"  rendered ink rows: {first_ink}..{last_ink} "
          f"(normalised {first_ink / height:.3f}..{last_ink / height:.3f})")

    # The adapter's claim, as a pixel range.
    claimed_top = line.bbox.y0 * height
    claimed_bottom = line.bbox.y1 * height
    print(f"  adapter claims rows: {claimed_top:.0f}..{claimed_bottom:.0f} "
          f"(normalised {line.bbox.y0:.3f}..{line.bbox.y1:.3f})")

    # Find the title's own pixels: crop nothing, but use the adapter's x range as a
    # horizontal filter is circular, so instead locate the first text row cluster.
    grey = image.convert("L")
    pixels = grey.load()
    row_has_ink = []
    for y in range(height):
        count = sum(1 for x in range(0, width, 2) if pixels[x, y] < 128)
        row_has_ink.append(count)
    # The title is the first run of rows with ink.
    run_start = None
    runs = []
    for y, count in enumerate(row_has_ink):
        if count > 2:
            if run_start is None:
                run_start = y
        else:
            if run_start is not None and y - run_start >= 2:
                runs.append((run_start, y))
            run_start = None
    if run_start is not None:
        runs.append((run_start, height))
    print(f"\n  first 6 ink row-runs (a run is a text line): {runs[:6]}")
    if runs:
        top_run = runs[0]
        print(f"  topmost text line at rows {top_run[0]}..{top_run[1]} "
              f"(normalised {top_run[0] / height:.3f}..{top_run[1] / height:.3f})")

    print("\n=== VERDICT ===")
    if runs:
        top_norm = top_run[0] / height
        if abs(top_norm - line.bbox.y0) < 0.05:
            print("  The adapter's box for the title is at the top of the render: "
                  "coordinates AGREE.")
            print("  So the metric, not the adapter, is at fault for the region result.")
        elif abs((1.0 - top_norm) - line.bbox.y0) < 0.05:
            print("  The adapter's box for the title is at the BOTTOM of the render "
                  "while the")
            print("  text is drawn at the top: the adapter is VERTICALLY MIRRORED.")
        else:
            print(f"  Inconclusive: adapter says {line.bbox.y0:.3f}, "
                  f"render's top line is at {top_norm:.3f}.")
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
