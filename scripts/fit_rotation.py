"""Solve the raw->normalised transform from the measured marker positions.

Rather than guessing a formula and testing it, this fits ``x' = a*x + c*y + e``
and ``y' = b*x + d*y + f`` to the measured positions from
``scripts/derive_rotation3.py``.  Four non-collinear points determine an affine
map exactly, so the fit is not an approximation and the residual is a check on
the measurements rather than a compromise.

The measured values are hard-coded from that script's output on purpose: this
script's job is to convert one representation into another, and re-measuring here
would couple it to PyMuPDF in a way that makes the conversion harder to audit.
"""

from __future__ import annotations

import numpy as np

PAGE_W = 400.0
PAGE_H = 600.0

#: Marker centres in raw PDF space.
RAW = {
    "raw_bl": (20.0, 20.0),
    "raw_br": (380.0, 20.0),
    "raw_tl": (20.0, 580.0),
    "raw_tr": (380.0, 580.0),
}

#: Measured display positions, from scripts/derive_rotation3.py.
MEASURED: dict[int, dict[str, tuple[float, float]]] = {
    0: {
        "raw_bl": (0.0498, 0.9668),
        "raw_br": (0.9490, 0.9668),
        "raw_tl": (0.0498, 0.0332),
        "raw_tr": (0.9490, 0.0332),
    },
    90: {
        "raw_bl": (0.0332, 0.0498),
        "raw_br": (0.0332, 0.9490),
        "raw_tl": (0.9668, 0.0498),
        "raw_tr": (0.9668, 0.9490),
    },
    180: {
        "raw_bl": (0.9490, 0.0332),
        "raw_br": (0.0498, 0.0332),
        "raw_tl": (0.9490, 0.9668),
        "raw_tr": (0.0498, 0.9668),
    },
    270: {
        "raw_bl": (0.9668, 0.9490),
        "raw_br": (0.9668, 0.0498),
        "raw_tl": (0.0332, 0.9490),
        "raw_tr": (0.0332, 0.0498),
    },
}


def main() -> int:
    for rotation, measured in MEASURED.items():
        design = np.array(
            [[RAW[name][0], RAW[name][1], 1.0] for name in ("raw_bl", "raw_br", "raw_tl", "raw_tr")]
        )
        targets = np.array(
            [measured[name] for name in ("raw_bl", "raw_br", "raw_tl", "raw_tr")]
        )
        solution, _residuals, rank, _sv = np.linalg.lstsq(design, targets, rcond=None)
        predicted = design @ solution
        error = float(np.abs(predicted - targets).max())
        (a, b), (c, d), (e, f) = solution
        print(f"rotation {rotation}: rank={rank} max error={error:.6f}")
        print(f"    a={a:+.10f} b={b:+.10f} c={c:+.10f} d={d:+.10f} e={e:+.10f} f={f:+.10f}")

        # Express in the form the implementation uses: coefficients that already
        # include the 1/w and 1/h scaling, so they can be written literally.
        print(
            "    as normalized coefficients: "
            f"a={a:+.10f} b={b:+.10f} c={c:+.10f} "
            f"d={d:+.10f} e={e:+.10f} f={f:+.10f}"
        )

        # Also report the equivalent closed form using w, h, x0, y0 so the code
        # can keep a readable formula.  w and h are the visible page size, and the
        # measurements already imply the signs.
        print("    closed-form check at the page corners:")
        for name in ("raw_bl", "raw_br", "raw_tl", "raw_tr"):
            x, y = RAW[name]
            got = (a * x + c * y + e, b * x + d * y + f)
            want = measured[name]
            print(
                f"      {name}: {got[0]:.4f},{got[1]:.4f} vs {want[0]:.4f},{want[1]:.4f}"
            )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
