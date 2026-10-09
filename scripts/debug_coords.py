"""Print the raw_to_norm result for each rotation, to locate the disagreement."""

from __future__ import annotations

from document_core.coords import PageGeometry

W, H = 595.276, 841.89

for rotation in (0, 90, 180, 270):
    geometry = PageGeometry(W, H, rotation=rotation)
    transform = geometry.raw_to_norm()
    print(f"rotation {rotation}: rotation attr={geometry.rotation}")
    print(
        f"  transform a={transform.a:+.8f} b={transform.b:+.8f} c={transform.c:+.8f} "
        f"d={transform.d:+.8f} e={transform.e:+.8f} f={transform.f:+.8f}"
    )
    for label, point in (
        ("raw (0,0)", (0.0, 0.0)),
        ("raw (W,0)", (W, 0.0)),
        ("raw (0,H)", (0.0, H)),
        ("raw (W,H)", (W, H)),
    ):
        print(f"  {label:12} -> {transform.apply(*point)}")
    print(f"  display {geometry.display_width:.2f}x{geometry.display_height:.2f}")
    print()
