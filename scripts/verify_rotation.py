"""Is the coordinate transform a rotation, or a mirror?

This project has shipped a vertical mirror twice - once in the adapter's line boxes and once in
the rotation handling.  ``verify_coordinate_fix.py`` checks where a marker ends up, and a mirror
puts markers in *plausible* places: the four corners of the page still land on the four corners of
the image, still distinct, still inside the unit square.  Position agreement at 16 corners is
therefore not, by itself, a test for the defect that actually happened.

The invariant a mirror breaks is **handedness**.  Going around the page top-left, top-right,
bottom-right, bottom-left is one rotational direction on paper; no rotation can reverse it, and a
reflection always does.  So this script measures the rendered position of four coloured corner
markers, predicts them with the pipeline's own transform, and asserts two things:

1. each prediction lands on the marker PyMuPDF actually rendered (position), and
2. the cyclic order of the predictions has the same handedness as the cyclic order of the
   measurements (orientation).

Then it runs the same handedness test on a deliberately mirrored copy of the predictions and
**requires it to fail**.  A test for a mirror that cannot detect a mirror is not evidence, which
is the mistake two earlier overlay metrics in this repository made.

Rewritten after ``scripts/verify_all.py`` reported this file failing: it drew its marker with
``page.draw_rect``, which takes the media frame (top-left origin, y down), and then fed that point
to ``PageGeometry.raw_to_norm`` as though it were raw PDF space (y up).  The two frames differ by
exactly the flip under test, so all six cases failed with mirrored numbers - and its printed
column labels did not match its own format string, so the numbers read as the opposite of what
they were.  The product was correct; this script was not.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image

from document_core.coords import PageGeometry

W, H = 400.0, 600.0
DPI = 100
MARK = 26.0

#: Distinct colours, so the four corners can be told apart in one render.
COLOURS = {
    "tl": (1.0, 0.0, 0.0),
    "tr": (0.0, 0.8, 0.0),
    "br": (1.0, 0.63, 0.0),
    "bl": (0.0, 0.0, 1.0),
}
COLOURS_255 = {name: tuple(round(c * 255) for c in value) for name, value in COLOURS.items()}

#: Marker centres in the frame ``draw_rect`` uses: the unrotated media frame, origin top-left,
#: y increasing downwards.  This is the same frame ``page.get_text("dict")`` reports boxes in.
CORNERS = {
    "tl": (40.0, 40.0),
    "tr": (W - 40.0, 40.0),
    "br": (W - 40.0, H - 40.0),
    "bl": (40.0, H - 40.0),
}

#: Cyclic order around the page.  Reversing it is what a reflection does.
CYCLE = ("tl", "tr", "br", "bl")

ROTATIONS = (0, 90, 180, 270)


def locate(image: Image.Image, colour: tuple[int, int, int]) -> tuple[float, float]:
    """The centroid of a marker's colour, in normalised top-left coordinates."""
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
        raise AssertionError(f"marker colour {colour} was not rendered at all")
    return (sum(xs) / len(xs) / width, sum(ys) / len(ys) / height)


def handedness(points: list[tuple[float, float]]) -> float:
    """Twice the signed area of a closed polygon.  Its sign is the winding direction.

    Only the sign is used, and only ever to compare two polygons in the same coordinate frame, so
    the y-down convention cancels out and needs no correction.
    """
    total = 0.0
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return total


def render_marked_page(rotation: int, tmp: Path) -> Image.Image:
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
    pixmap = page.get_pixmap(dpi=DPI, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    doc.close()
    return image


def main() -> int:
    import tempfile

    failures: list[str] = []
    # Tolerance in normalised units: a marker is MARK points across, converted through the render.
    tolerance = MARK / min(int(W * DPI / 72), int(H * DPI / 72)) * 1.6

    print(f"{'rot':>4}  {'corner':>6}  {'predicted':>18}  {'measured':>18}  {'delta':>13}  ok")
    print("-" * 78)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for rotation in ROTATIONS:
            image = render_marked_page(rotation, tmp)
            geometry = PageGeometry(W, H, rotation=rotation)
            transform = geometry.raw_to_norm()

            measured: list[tuple[float, float]] = []
            predicted: list[tuple[float, float]] = []
            for name in CYCLE:
                # draw_rect used the media frame; raw PDF space is that frame with y measured from
                # the bottom, so this is the one conversion that has to happen before the
                # transform.
                cx, cy = CORNERS[name]
                raw = (cx, H - cy)
                guess = transform.apply(*raw)
                actual = locate(image, COLOURS_255[name])
                dx = abs(guess[0] - actual[0])
                dy = abs(guess[1] - actual[1])
                ok = dx < tolerance and dy < tolerance
                if not ok:
                    failures.append(
                        f"rotation={rotation} corner={name} predicted=({guess[0]:.3f},"
                        f"{guess[1]:.3f}) measured=({actual[0]:.3f},{actual[1]:.3f})"
                    )
                predicted.append(guess)
                measured.append(actual)
                print(
                    f"{rotation:>4}  {name:>6}  ({guess[0]:.3f},{guess[1]:.3f})".ljust(36)
                    + f"({actual[0]:.3f},{actual[1]:.3f})".rjust(20)
                    + f"  ({dx:.4f},{dy:.4f})".rjust(16)
                    + f"  {'PASS' if ok else 'FAIL'}"
                )

            source_sign = handedness(predicted)
            rendered_sign = handedness(measured)
            same = (source_sign > 0) == (rendered_sign > 0)
            if not same:
                failures.append(
                    f"rotation={rotation}: the transform reverses handedness "
                    f"({source_sign:.1f} vs {rendered_sign:.1f}) - it is a reflection, not a "
                    f"rotation"
                )
            print(
                f"      handedness: predicted {source_sign:>12.1f}  rendered {rendered_sign:>12.1f}"
                f"  {'rotation (preserved)' if same else 'MIRROR (reversed)'}"
            )

    # -- the negative control ---------------------------------------------------------------
    # The whole value of the handedness check is that it fails on a mirror.  So it is run againstone,
    # and required to fail.  Without this the check would be a claim about itself.
    mirrored = [(x, 1.0 - y) for x, y in predicted]
    detection = (handedness(predicted) > 0) != (handedness(mirrored) > 0)
    print()
    print(
        f"negative control: a deliberately y-mirrored prediction is detected as reversed: "
        f"{'yes' if detection else 'NO'}"
    )
    if not detection:
        failures.append(
            "the handedness test did not detect a deliberate mirror, so it cannot detect the "
            "defect it exists for"
        )

    print()
    if failures:
        print(f"FAIL: {len(failures)} disagreement(s):")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(
        "PASS: the transform places all 16 markers where PyMuPDF rendered them and preserves "
        "handedness at every rotation, and the handedness test detects a deliberate mirror"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
