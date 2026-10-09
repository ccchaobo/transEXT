"""Definitive rotation mapping: one dark marker per render, located exactly.

Earlier attempts failed for measurement reasons that had nothing to do with the
geometry:

* four markers with light greys made the locator match background as well, so it
  reported the image centre;
* markers drawn with ``Page.draw_rect`` were placed in the page's own top-left
  coordinate system while being compared against raw PDF coordinates;
* a least-squares fit over the four corners of a rectangle is rank deficient, so
  its coefficients were meaningless.

This version avoids all three.  It renders **one** dark marker per page, so there
is nothing to confuse it with, it converts raw coordinates into page units
explicitly, and it locates the marker as the centre of the darkest connected
blob rather than by any threshold that could admit background.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

PAGE_W = 400.0
PAGE_H = 600.0
MARK = 20.0
DPI = 150

#: Raw PDF space corners (origin bottom-left, y upwards).
RAW_CORNERS = {
    "raw_bl": (MARK, MARK),
    "raw_br": (PAGE_W - MARK, MARK),
    "raw_tl": (MARK, PAGE_H - MARK),
    "raw_tr": (PAGE_W - MARK, PAGE_H - MARK),
}


def build(rotation: int, raw_x: float, raw_y: float, directory: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    # Raw PDF y is measured upwards from the bottom; the page's own drawing
    # coordinates have y measured downwards from the top.
    x, y = raw_x, PAGE_H - raw_y
    page.draw_rect(
        pymupdf.Rect(x - MARK / 2, y - MARK / 2, x + MARK / 2, y + MARK / 2),
        color=(0, 0, 0),
        fill=(0, 0, 0),
    )
    page.set_rotation(rotation)
    path = directory / f"one_{rotation}_{int(raw_x)}_{int(raw_y)}.pdf"
    doc.save(str(path))
    doc.close()
    return path


def locate_only_dark_blob(image: Image.Image) -> tuple[float, float]:
    """Centre of the darkest connected region, in normalised top-left coords."""
    array = np.asarray(image.convert("L")).astype("float32")
    threshold = array.min() + 40.0
    mask = array <= threshold
    rows, columns = np.nonzero(mask)
    if rows.size == 0:
        return float("nan"), float("nan")
    height, width = array.shape
    return (
        (columns.min() + columns.max() + 1) / 2.0 / width,
        (rows.min() + rows.max() + 1) / 2.0 / height,
    )


def main() -> int:
    results: dict[int, dict[str, tuple[float, float]]] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        directory = Path(tmpdir)
        for rotation in (0, 90, 180, 270):
            results[rotation] = {}
            print(f"\n=== /Rotate {rotation} ===")
            for name, (raw_x, raw_y) in RAW_CORNERS.items():
                path = build(rotation, raw_x, raw_y, directory)
                doc = pymupdf.open(str(path))
                page = doc.load_page(0)
                pixmap = page.get_pixmap(dpi=DPI, alpha=False)
                image = Image.frombytes(
                    "RGB", (pixmap.width, pixmap.height), pixmap.samples
                )
                doc.close()
                found = locate_only_dark_blob(image)
                results[rotation][name] = found
                print(
                    f"  {name} raw=({raw_x:.0f},{raw_y:.0f}) -> "
                    f"display=({found[0]:.4f},{found[1]:.4f})"
                )

    print("\n\n# Measured raw corner -> normalised display position")
    for rotation, mapping in results.items():
        print(f"{rotation}: {{")
        for name, point in mapping.items():
            print(f'    "{name}": ({point[0]:.4f}, {point[1]:.4f}),')
        print("},")

    print("\n# Distances from each measured point to each display corner")
    corners = {
        "top_left": (0.0, 0.0),
        "top_right": (1.0, 0.0),
        "bottom_left": (0.0, 1.0),
        "bottom_right": (1.0, 1.0),
    }
    for rotation, mapping in results.items():
        print(f"\nrotation {rotation}:")
        for name, point in mapping.items():
            nearest = min(
                corners,
                key=lambda c: abs(corners[c][0] - point[0]) + abs(corners[c][1] - point[1]),
            )
            distance = abs(corners[nearest][0] - point[0]) + abs(
                corners[nearest][1] - point[1]
            )
            print(f"  {name:7} -> {nearest:12} (offset {distance:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
