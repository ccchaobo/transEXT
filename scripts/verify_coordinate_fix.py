"""Verify the candidate fix for the vertical mirror, across all rotations.

The adapter must convert PyMuPDF's text box (unrotated media frame, y down) into raw
PDF space (y up) before applying ``PageGeometry.raw_to_norm``.  This checks that the
corrected result lands on the marker's actual rendered pixels for 0/90/180/270.

The marker's raw-space position is known exactly: it is drawn at a chosen point in
the text frame, so raw = (x, H - y).  The expected normalised position is therefore
that raw point put through the same transform, and the measured position is read off
the render.  Agreement between the transform and the render is what ADR 0009
established for the transform itself; this test checks the adapter applies it to the
right frame.
"""

from __future__ import annotations

import pymupdf
from PIL import Image

from document_core.coords import PageGeometry

W, H = 400.0, 600.0
DPI = 72
MARK = 24.0
COLOURS = {
    "tl": (1.0, 0.0, 0.0),
    "tr": (0.0, 0.8, 0.0),
    "bl": (0.0, 0.0, 1.0),
    "br": (1.0, 0.63, 0.0),
}
COLOURS_255 = {k: tuple(round(c * 255) for c in v) for k, v in COLOURS.items()}
#: Marker centres in page units (top-left media frame, y down).
CORNERS = {
    "tl": (40.0, 40.0),
    "tr": (W - 40.0, 40.0),
    "bl": (40.0, H - 40.0),
    "br": (W - 40.0, H - 40.0),
}


def locate(image: Image.Image, colour: tuple[int, int, int]) -> tuple[float, float]:
    width, height = image.size
    pixels = image.load()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y][:3]
            if abs(r - colour[0]) < 45 and abs(g - colour[1]) < 45 and abs(b - colour[2]) < 45:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise AssertionError(f"colour {colour} not found")
    return (sum(xs) / len(xs) / width, sum(ys) / len(ys) / height)


def main() -> int:
    failures = 0
    for rotation in (0, 90, 180, 270):
        doc = pymupdf.open()
        page = doc.new_page(width=W, height=H)
        for name, (cx, cy) in CORNERS.items():
            page.draw_rect(
                pymupdf.Rect(cx - MARK / 2, cy - MARK / 2, cx + MARK / 2, cy + MARK / 2),
                color=COLOURS[name],
                fill=COLOURS[name],
            )
        page.set_rotation(rotation)
        page = doc.load_page(0)

        geometry = PageGeometry(W, H, rotation=rotation)
        transform = geometry.raw_to_norm()

        pixmap = page.get_pixmap(dpi=DPI, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

        print(f"\n=== rotation {rotation} ===")
        for name, (cx, cy) in CORNERS.items():
            # The candidate fix: text frame -> raw space -> normalised.
            raw_x, raw_y = cx, H - cy
            predicted = transform.apply(raw_x, raw_y)
            measured = locate(image, COLOURS_255[name])
            dx = abs(predicted[0] - measured[0])
            dy = abs(predicted[1] - measured[1])
            ok = dx < 0.01 and dy < 0.01
            if not ok:
                failures += 1
            print(
                f"  {name}: predicted ({predicted[0]:.3f},{predicted[1]:.3f})  "
                f"measured ({measured[0]:.3f},{measured[1]:.3f})  "
                f"delta ({dx:.4f},{dy:.4f})  {'ok' if ok else 'MISMATCH'}"
            )

            # What the current adapter does: transform applied straight to the
            # text-frame point.  Printed to show the size of the existing error.
            wrong = transform.apply(cx, cy)
            print(
                f"      current adapter would give "
                f"({wrong[0]:.3f},{wrong[1]:.3f})"
            )
        doc.close()

    print()
    if failures:
        print(f"FAIL: {failures} marker(s) did not match the corrected transform")
        return 1
    print("PASS: text-frame -> raw -> normalised lands on every marker for all rotations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
