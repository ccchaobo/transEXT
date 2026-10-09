"""Exactly what frame does get_text use, and what does the reader contract need?

Establishes, by rendering rather than by documentation:

1. whether get_text's box for a known word is stated in the unrotated media frame
   or in the rotated display frame;
2. what the normalised display coordinates of that word must be, read off the
   rendered image.

Together these say what transform the adapter must apply.  An unrotated page makes
the two frames identical, so the rotated cases are the ones that decide it.
"""

from __future__ import annotations

import pymupdf
from PIL import Image, ImageDraw

W, H = 400.0, 600.0
#: Drawn in page units (top-left origin, y down) on the *unrotated* page.
X, Y = 50.0, 40.0
DPI = 72
MARK = 24.0
#: Marker colours as fractions, which is what `draw_rect` requires (0..1).
COLOURS = {
    "tl": (1.0, 0.0, 0.0),
    "tr": (0.0, 0.8, 0.0),
    "bl": (0.0, 0.0, 1.0),
    "br": (1.0, 0.63, 0.0),
}
#: The same colours in 0..255 space, for locating them in the render.
COLOURS_255 = {
    name: tuple(round(c * 255) for c in value) for name, value in COLOURS.items()
}
# Corners in page units (top-left origin, y down): (x, y)
CORNERS = {
    "tl": (5.0, 5.0),
    "tr": (W - 5.0, 5.0),
    "bl": (5.0, H - 5.0),
    "br": (W - 5.0, H - 5.0),
}


def locate(image: Image.Image, colour: tuple[int, int, int]) -> tuple[float, float]:
    width, height = image.size
    pixels = image.load()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y][:3]
            # Channel dominance, not proximity: two markers share a zero channel
            # and antialiasing blurs edges.
            if (
                abs(r - colour[0]) < 45
                and abs(g - colour[1]) < 45
                and abs(b - colour[2]) < 45
            ):
                xs.append(x)
                ys.append(y)
    if not xs:
        raise AssertionError(f"colour {colour} not found")
    return (sum(xs) / len(xs) / width, sum(ys) / len(ys) / height)


def main() -> int:
    for rotation in (0, 90, 180, 270):
        doc = pymupdf.open()
        page = doc.new_page(width=W, height=H)
        for name, (cx, cy) in CORNERS.items():
            page.draw_rect(
                pymupdf.Rect(cx - MARK / 2, cy - MARK / 2, cx + MARK / 2, cy + MARK / 2),
                color=COLOURS[name],
                fill=COLOURS[name],
            )
        page.insert_text((X, Y), "MARKER", fontsize=12)
        page.set_rotation(rotation)
        page = doc.load_page(0)

        print(f"\n=== rotation {rotation} ===")
        print(f"  page.rect = {page.rect}  (display frame, points)")

        # get_text box for MARKER.
        box = None
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if "MARKER" in span.get("text", ""):
                        box = span["bbox"]
        if box:
            rect = page.rect
            print(f"  get_text span bbox = "
                  f"({box[0]:.1f},{box[1]:.1f},{box[2]:.1f},{box[3]:.1f})")
            print(f"    normalised by page.rect = "
                  f"({box[0] / rect.width:.3f},{box[1] / rect.height:.3f},"
                  f"{box[2] / rect.width:.3f},{box[3] / rect.height:.3f})")
            print(f"    normalised by media ({W}x{H}) = "
                  f"({box[0] / W:.3f},{box[1] / H:.3f},"
                  f"{box[2] / W:.3f},{box[3] / H:.3f})")

        # Ground truth from the render.
        pixmap = page.get_pixmap(dpi=DPI, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        print(f"  render = {image.size[0]}x{image.size[1]} px at {DPI} dpi")
        for name, colour in COLOURS_255.items():
            px, py = locate(image, colour)
            print(f"    corner {name} rendered at normalised ({px:.3f},{py:.3f})")
        doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
