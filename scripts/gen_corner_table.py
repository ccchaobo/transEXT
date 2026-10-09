"""Generate the measured raw<->display corner table for the tests.

Hand-transcribing this table produced three rounds of wrong expectations, so it
is generated from the transform output instead.  The transform itself is verified
against rendered pixels by ``scripts/derive_rotation2.py`` and
``tests/unit/test_rotation_geometry.py``; this script only transcribes its output
into the shape the tests want.
"""

from __future__ import annotations

from document_core.coords import PageGeometry

W, H = 595.276, 841.89

#: Raw-space corners, named by their position in raw (bottom-left origin) space.
RAW = {
    "top_left": (0.0, H),
    "top_right": (W, H),
    "bottom_left": (0.0, 0.0),
    "bottom_right": (W, 0.0),
}

#: Normalised display positions.
DISPLAY = {
    "top_left": (0.0, 0.0),
    "top_right": (1.0, 0.0),
    "bottom_left": (0.0, 1.0),
    "bottom_right": (1.0, 1.0),
}


def main() -> int:
    print("DISPLAY_TO_RAW: dict[int, dict[str, tuple[float, float]]] = {")
    for rotation in (0, 90, 180, 270):
        geometry = PageGeometry(W, H, rotation=rotation)
        transform = geometry.raw_to_norm()
        # Which raw corner lands on each display corner.
        found: dict[str, tuple[float, float]] = {}
        for raw_name, raw_point in RAW.items():
            got = transform.apply(*raw_point)
            for display_name, want in DISPLAY.items():
                if abs(got[0] - want[0]) < 1e-9 and abs(got[1] - want[1]) < 1e-9:
                    found[display_name] = raw_point
        assert len(found) == 4, (rotation, found)
        print(f"    {rotation}: {{")
        for display_name in ("top_left", "top_right", "bottom_left", "bottom_right"):
            x, y = found[display_name]
            print(f'        "{display_name}": ({x:g}, {y:g}),')
        print("    },")
    print("}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
