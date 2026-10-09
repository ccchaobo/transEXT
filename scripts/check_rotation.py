"""Verify the corner mapping against the measured table.

The expected values come from rendering a marker in a known raw corner and
locating it in the resulting pixels (``scripts/derive_rotation3.py``, kept as a
test in ``tests/unit/test_rotation_geometry.py``).  They are measurements, not a
restatement of the implementation.

This script is the quick check: it asserts the table against the implementation and
exercises the round-trip and range properties.  The authoritative measurement is
the pixel test, which re-derives these positions from a render every time it runs;
if the two ever disagree, the pixel test is right.

**This table was stale once and reported the rotated cases as failures while the
implementation was already correct.**  The values here had been taken from an
earlier, wrong derivation.  A hard-coded expectation that nobody re-measures is
exactly how the original rotation bug survived, so the values below are copied from
the pixel test's verified output and ``--verify`` re-measures them.

Note on labels: in raw PDF space y grows upwards, so ``(0, 0)`` is the
**bottom-left** of the page.  PyMuPDF's ``Page.draw_rect`` takes a rectangle in
its own top-left-origin coordinate system, which is why the measurement script had
to be read carefully: a marker drawn at ``y = 0`` appears at the top of the
rendered image.  The mapping below uses raw-space names consistently.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from document_core.coords import PageGeometry

W, H = 400.0, 600.0

#: Measured from rendered markers: raw-space corner -> normalised position on
#: the displayed page.
#:
#: The distinguishing property of each row is where the **raw bottom-left**
#: corner (the origin of raw PDF space) ends up.  A page rotated clockwise by
#: ``/Rotate`` carries the raw origin to a different displayed corner each time:
#:
#: ==== ====================
#: rot  raw bottom-left at
#: ==== ====================
#: 0    bottom-left
#: 90   top-left
#: 180  top-right
#: 270  bottom-right
#: ==== ====================
MEASURED = {
    0: {"bl": (0.0, 1.0), "br": (1.0, 1.0), "tl": (0.0, 0.0), "tr": (1.0, 0.0)},
    90: {"bl": (0.0, 0.0), "br": (0.0, 1.0), "tl": (1.0, 0.0), "tr": (1.0, 1.0)},
    180: {"bl": (1.0, 0.0), "br": (0.0, 0.0), "tl": (1.0, 1.0), "tr": (0.0, 1.0)},
    270: {"bl": (1.0, 1.0), "br": (1.0, 0.0), "tl": (0.0, 1.0), "tr": (0.0, 0.0)},
}

#: Corners named in raw PDF space: y grows upwards from the bottom.
RAW = {"bl": (0.0, 0.0), "br": (W, 0.0), "tl": (0.0, H), "tr": (W, H)}
TOL = 1e-9


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the corner mapping")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="re-measure the table from rendered pixels instead of trusting it",
    )
    args = parser.parse_args(argv)

    if args.verify:
        # Delegating keeps one implementation of the measurement.  Duplicating the
        # render-and-locate logic here is how two copies of a measurement drift
        # apart, which is what went wrong with this table in the first place.
        import subprocess
        import sys
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_rotation_geometry.py",
                "-q",
                "-k",
                "agrees_with_rendered_pixels",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        print(result.stdout.strip()[-2000:])
        if result.returncode != 0:
            print(result.stderr.strip()[-2000:])
            print("\nFAIL: the pixel measurement disagrees with the implementation")
            return 1
        print("\nThe pixel measurement agrees with the implementation.")

    mismatches = 0
    for rotation, expected in MEASURED.items():
        geometry = PageGeometry(W, H, rotation=rotation)
        transform = geometry.raw_to_norm()
        cells = []
        for name, raw in RAW.items():
            got = transform.apply(*raw)
            want = expected[name]
            ok = abs(got[0] - want[0]) < TOL and abs(got[1] - want[1]) < TOL
            if not ok:
                mismatches += 1
            cells.append(f"{name}=({got[0]:.2f},{got[1]:.2f}){'ok' if ok else ' BAD'}")
        print(f"rot {rotation:3}  " + "  ".join(cells))

    # The inverse must round-trip for every rotation.
    for rotation in MEASURED:
        geometry = PageGeometry(W, H, rotation=rotation)
        forward = geometry.raw_to_norm()
        backward = geometry.norm_to_raw()
        for raw in RAW.values():
            assert backward.apply(*forward.apply(*raw)) == (
                raw[0],
                raw[1],
            ), f"round trip failed for rotation {rotation}"

    # Every corner must land inside the unit square.
    for rotation in MEASURED:
        geometry = PageGeometry(W, H, rotation=rotation)
        transform = geometry.raw_to_norm()
        for name, raw in RAW.items():
            x, y = transform.apply(*raw)
            assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, (rotation, name, x, y)

    print()
    if mismatches:
        print(f"FAIL: {mismatches} corner mapping(s) disagree with the measurements")
        return 1
    print("PASS: all 16 corner mappings match the measured table; round trip and range checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
