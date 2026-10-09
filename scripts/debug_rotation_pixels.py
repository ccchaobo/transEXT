"""Print predicted vs measured marker positions for each rotation.

Repeated rounds of adjusting tolerances did not converge, which means the
disagreement is structural rather than noise.  Printing both columns for every
marker shows where the mapping is wrong instead of hiding it behind a tolerance.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from unit.test_rotation_geometry import (  # noqa: E402
    COLOURS,
    PAGE_H,
    PAGE_W,
    POSITIONS,
    _build_marked_pdf,
    _geometry_for,
    _locate,
    _render,
)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        directory = Path(tmpdir)
        for rotation in (0, 90, 180, 270):
            path = _build_marked_pdf(rotation, directory)
            geometry = _geometry_for(path)
            image = _render(path)
            transform = geometry.raw_to_norm()
            print(f"\n=== rotation {rotation}  render {image.size} ===")
            print(f"  {'marker':11} {'raw':>18} {'predicted':>18} {'measured':>18} {'delta':>16}")
            for name, raw in POSITIONS.items():
                predicted = transform.apply(*raw)
                measured = _locate(image, COLOURS[name])
                delta = (predicted[0] - measured[0], predicted[1] - measured[1])
                print(
                    f"  {name:11} {str(raw):>18} "
                    f"({predicted[0]:.4f},{predicted[1]:.4f}) "
                    f"({measured[0]:.4f},{measured[1]:.4f}) "
                    f"({delta[0]:+.4f},{delta[1]:+.4f})"
                )
            print(f"  aspect: geometry={geometry.aspect_ratio:.4f} "
                  f"render={image.size[0] / image.size[1]:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
