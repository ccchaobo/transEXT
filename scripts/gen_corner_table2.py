"""Print raw corner -> display position for each rotation, as the transform sees it."""

from __future__ import annotations

from document_core.coords import PageGeometry

W, H = 595.276, 841.89
RAW = {"raw_tl": (0.0, H), "raw_tr": (W, H), "raw_bl": (0.0, 0.0), "raw_br": (W, 0.0)}

for rotation in (0, 90, 180, 270):
    geometry = PageGeometry(W, H, rotation=rotation)
    transform = geometry.raw_to_norm()
    print(f"{rotation}: {{")
    for name, point in RAW.items():
        x, y = transform.apply(*point)
        print(f'    "{name}": ({x:g}, {y:g}),')
    print("},")
