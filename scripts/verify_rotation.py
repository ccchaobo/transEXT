"""Verify the rotation convention against PyMuPDF's own behaviour.

``PageGeometry.raw_to_display`` composes a y-flip with a clockwise quarter turn.
That composition is arithmetic, and the question that matters is whether it
agrees with the engine that actually produces the coordinates:
``adapters/raster``/``pdf_extract`` feed it spans reported by PyMuPDF, and the
reader compares them against pixels rendered by PyMuPDF.

So this builds a page, marks a corner, reads the span back, renders the page, and
checks that the normalised coordinate really does point at the mark in the
rendered image.  That is an end-to-end check of the convention rather than a
restatement of it.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image

from document_core.coords import BBox, PageGeometry

PAGE_W = 400.0
PAGE_H = 600.0


def build_page(rotation: int, marker: tuple[float, float], tmp: Path) -> Path:
    """Create a page with a small black square at ``marker`` (raw PDF space)."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    x, y = marker
    # A filled rect drawn in raw PDF space; y is measured from the bottom.
    page.draw_rect(pymupdf.Rect(x - 12, y - 12, x + 12, y + 12), color=(0, 0, 0), fill=(0, 0, 0))
    page.set_rotation(rotation)
    path = tmp / f"marker_rot{rotation}_{int(x)}_{int(y)}.pdf"
    doc.save(str(path))
    doc.close()
    return path


def darkest_pixel_normalised(image: Image.Image) -> tuple[float, float]:
    """Where the darkest pixel is, in normalised top-left coordinates."""
    import numpy as np

    array = np.asarray(image.convert("L"))
    index = int(array.argmin())
    row, column = divmod(index, array.shape[1])
    return (column / array.shape[1], row / array.shape[0])


def main() -> int:
    import tempfile

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # For each rotation, choose a marker corner and state where that corner
        # should appear on the displayed page.
        cases = [
            # rotation, raw marker corner,      expected normalised position
            (0, (30.0, PAGE_H - 30.0), "top-left"),
            (90, (30.0, 30.0), "top-left"),
            (180, (PAGE_W - 30.0, 30.0), "top-left"),
            (270, (PAGE_W - 30.0, PAGE_H - 30.0), "top-left"),
            (0, (PAGE_W - 30.0, 30.0), "bottom-right"),
            (90, (PAGE_W - 30.0, PAGE_H - 30.0), "bottom-right"),
        ]
        print(f"{'rot':>4} {'raw marker':>18} {'expected':>12} {'pixel':>18} {'geometry':>18} {'ok':>4}")
        print("-" * 84)
        for rotation, marker, expected in cases:
            path = build_page(rotation, marker, tmp)
            doc = pymupdf.open(str(path))
            page = doc.load_page(0)
            geometry = PageGeometry(
                media_width=page.mediabox.width,
                media_height=page.mediabox.height,
                rotation=int(page.rotation),
            )
            normalised = geometry.raw_to_norm().apply(*marker)
            pixmap = page.get_pixmap(dpi=100, alpha=False)
            rendered = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            doc.close()
            pixel = darkest_pixel_normalised(rendered)

            # The two independent answers must agree with each other and with the
            # stated expectation, all within the size of the drawn square.
            tolerance = 40.0 / min(rendered.size)
            agree = abs(normalised[0] - pixel[0]) < tolerance and abs(
                normalised[1] - pixel[1]
            ) < tolerance
            target = {
                "top-left": (tolerance, tolerance),
                "bottom-right": (1 - tolerance, 1 - tolerance),
            }[expected]
            near_expected = abs(pixel[0] - target[0]) < tolerance and abs(
                pixel[1] - target[1]
            ) < tolerance
            ok = agree and near_expected
            if not ok:
                failures.append(f"rotation={rotation} marker={marker} expected={expected}")
            print(
                f"{rotation:>4} {str(marker):>18} {expected:>12} "
                f"({pixel[0]:.3f},{pixel[1]:.3f})".ljust(56)
                + f"({normalised[0]:.3f},{normalised[1]:.3f})".rjust(18)
                + f" {'PASS' if ok else 'FAIL':>5}"
            )

    print()
    if failures:
        print(f"{len(failures)} case(s) disagree with PyMuPDF's own rendering:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("geometry and rendered pixels agree for every rotation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
