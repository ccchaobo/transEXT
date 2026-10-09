"""Unambiguous rotation check: one black square, find where it lands.

The four-marker version fitted a transform with least squares over a rectangle of
design points, which is rank deficient, so its fitted coefficients were
meaningless and were used to justify a wrong conclusion.  This draws a single
marker in one known raw corner, renders, and reports where the dark pixels are,
which needs no fitting.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

PAGE_W = 400.0
PAGE_H = 600.0
MARK = 40.0


def render_with_marker(rotation: int, raw_x: float, raw_y: float, tmp: Path) -> Image.Image:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    cx = min(max(raw_x, MARK / 2), PAGE_W - MARK / 2)
    cy = min(max(raw_y, MARK / 2), PAGE_H - MARK / 2)
    page.draw_rect(
        pymupdf.Rect(cx - MARK / 2, cy - MARK / 2, cx + MARK / 2, cy + MARK / 2),
        color=(0, 0, 0),
        fill=(0, 0, 0),
    )
    page.set_rotation(rotation)
    path = tmp / f"m_{rotation}_{int(raw_x)}_{int(raw_y)}.pdf"
    doc.save(str(path))
    doc.close()

    doc = pymupdf.open(str(path))
    pg = doc.load_page(0)
    pixmap = pg.get_pixmap(dpi=72, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    print(
        f"    /Rotate={pg.rotation} mediabox={pg.mediabox.width:.0f}x{pg.mediabox.height:.0f} "
        f"page.rect={pg.rect.width:.0f}x{pg.rect.height:.0f} render={pixmap.width}x{pixmap.height}"
    )
    doc.close()
    return image


def dark_centroid(image: Image.Image) -> tuple[float, float]:
    array = np.asarray(image.convert("L"))
    mask = array < 100
    if not mask.any():
        return (float("nan"), float("nan"))
    rows, columns = np.nonzero(mask)
    height, width = array.shape
    return (float(columns.mean() / width), float(rows.mean() / height))


def region_name(x: float, y: float) -> str:
    vertical = "top" if y < 0.4 else ("bottom" if y > 0.6 else "middle")
    horizontal = "left" if x < 0.4 else ("right" if x > 0.6 else "centre")
    return f"{vertical}-{horizontal}"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Raw corners of the unrotated page.
        spots = {
            "raw bottom-left (0,0)": (0.0, 0.0),
            "raw bottom-right (W,0)": (PAGE_W, 0.0),
            "raw top-left (0,H)": (0.0, PAGE_H),
            "raw top-right (W,H)": (PAGE_W, PAGE_H),
        }
        for rotation in (0, 90, 180, 270):
            print(f"\n/Rotate {rotation}  (marker in each raw corner):")
            for label, (x, y) in spots.items():
                image = render_with_marker(rotation, x, y, tmp)
                cx, cy = dark_centroid(image)
                print(f"  {label:26} -> render ({cx:.2f}, {cy:.2f})  = {region_name(cx, cy)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
