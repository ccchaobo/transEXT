"""Derive the raw->display transform empirically, corner by corner.

The previous attempt asserted a transform and failed, including one coordinate
outside [0, 1].  Rather than adjust the expectation, this measures where each raw
corner actually lands in a PyMuPDF render, then fits the transform from those
measurements.  The fitted transform is printed so it can be compared with
``PageGeometry.raw_to_display``.

Four labelled markers are drawn, one per raw corner, so the corner order in the
rendered image is read off directly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

PAGE_W = 400.0
PAGE_H = 600.0
MARK = 24.0
DPI = 100

#: Distinct greys so each corner can be identified by its pixel value.
CORNER_COLOURS = {
    "raw_tl": (0.15, 0.15, 0.15),   # raw (0, H)
    "raw_tr": (0.35, 0.35, 0.35),   # raw (W, H)
    "raw_br": (0.55, 0.55, 0.55),   # raw (W, 0)
    "raw_bl": (0.75, 0.75, 0.75),   # raw (0, 0)
}


def build(rotation: int, tmp: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    spots = {
        "raw_tl": (0.0, PAGE_H),
        "raw_tr": (PAGE_W, PAGE_H),
        "raw_br": (PAGE_W, 0.0),
        "raw_bl": (0.0, 0.0),
    }
    for name, (x, y) in spots.items():
        # Centre the square on the corner, inset so it is fully on the page.
        cx = min(max(x, MARK / 2), PAGE_W - MARK / 2)
        cy = min(max(y, MARK / 2), PAGE_H - MARK / 2)
        page.draw_rect(
            pymupdf.Rect(cx - MARK / 2, cy - MARK / 2, cx + MARK / 2, cy + MARK / 2),
            color=CORNER_COLOURS[name],
            fill=CORNER_COLOURS[name],
        )
    page.set_rotation(rotation)
    path = tmp / f"corners_{rotation}.pdf"
    doc.save(str(path))
    doc.close()
    return path


def locate(image: Image.Image, target: tuple[float, float]) -> tuple[float, float]:
    """Find the centroid of the region matching ``target`` greyscale value."""
    array = np.asarray(image.convert("L")).astype("float32")
    value = target[0] * 255.0
    mask = np.abs(array - value) < 12.0
    if not mask.any():
        return (float("nan"), float("nan"))
    rows, columns = np.nonzero(mask)
    height, width = array.shape
    return (float(columns.mean() / width), float(rows.mean() / height))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for rotation in (0, 90, 180, 270):
            path = build(rotation, tmp)
            doc = pymupdf.open(str(path))
            page = doc.load_page(0)
            pixmap = page.get_pixmap(dpi=DPI, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            print(
                f"\n=== /Rotate {rotation} ==="
                f"  mediabox={page.mediabox.width:.0f}x{page.mediabox.height:.0f}"
                f"  page.rect={page.rect.width:.0f}x{page.rect.height:.0f}"
                f"  render={pixmap.width}x{pixmap.height}"
            )
            print(f"  {'raw corner':10} {'raw (x,y)':>18} {'normalised in render':>22}")
            positions: dict[str, tuple[float, float]] = {}
            for name in ("raw_tl", "raw_tr", "raw_br", "raw_bl"):
                position = locate(image, CORNER_COLOURS[name])
                positions[name] = position
                raw = {
                    "raw_tl": (0.0, PAGE_H),
                    "raw_tr": (PAGE_W, PAGE_H),
                    "raw_br": (PAGE_W, 0.0),
                    "raw_bl": (0.0, 0.0),
                }[name]
                print(f"  {name:10} {str(raw):>18} ({position[0]:.4f},{position[1]:.4f})".rjust(52))

            # Fit: normalised = A * raw + b, using the four correspondences.
            raw_points = np.array(
                [
                    [0.0, PAGE_H, 1.0],
                    [PAGE_W, PAGE_H, 1.0],
                    [PAGE_W, 0.0, 1.0],
                    [0.0, 0.0, 1.0],
                ]
            )
            norm_points = np.array(
                [
                    positions["raw_tl"],
                    positions["raw_tr"],
                    positions["raw_br"],
                    positions["raw_bl"],
                ]
            )
            solution, residuals, _rank, _sv = np.linalg.lstsq(raw_points, norm_points, rcond=None)
            print("  fitted normalised = A*raw + b:")
            print(f"    x' = {solution[0][0]:+.6f}*x {solution[1][0]:+.6f}*y {solution[2][0]:+.6f}")
            print(f"    y' = {solution[0][1]:+.6f}*x {solution[1][1]:+.6f}*y {solution[2][1]:+.6f}")
            predicted = raw_points @ solution
            error = np.abs(predicted - norm_points).max()
            print(f"  max fit error: {error:.5f}")

            # What the implementation currently produces, for comparison.
            from document_core.coords import PageGeometry

            geometry = PageGeometry(
                media_width=page.mediabox.width,
                media_height=page.mediabox.height,
                rotation=int(page.rotation),
            )
            transform = geometry.raw_to_norm()
            print(
                f"  implementation: a={transform.a:+.6f} b={transform.b:+.6f} "
                f"c={transform.c:+.6f} d={transform.d:+.6f} "
                f"e={transform.e:+.6f} f={transform.f:+.6f}"
            )
            doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
