"""Coordinate contract for the research reader.

Frozen rules (spec section 8):

* Internal page index is 0-based; the UI shows ``page_index + 1``; a printed
  page label may be stored separately on ``Page``.
* Normalised coordinates always mean: origin at the **top-left of the page as
  displayed**, x to the right, y downwards, both axes in ``[0, 1]``.
* ``display_bbox`` / ``display_polygon`` are normalised to the *displayed*
  orientation, i.e. **after** the page ``/Rotate`` has been applied.
* ``raw_bbox`` is kept in the original page coordinate space of the source
  document (PDF user space: points, origin bottom-left, y upwards).
* Cropping, rotation, deskew and device pixel ratio all happen in the display
  layer, and every one of them must be expressible as an
  :class:`AffineTransform` recorded on the span.

Everything in this module is pure and unit-testable: no I/O, no optional
dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "BBox",
    "Polygon",
    "AffineTransform",
    "CoordinateSpace",
    "PageGeometry",
    "normalise_bbox",
    "clamp",
    "iou",
    "bbox_area",
    "union_bbox",
    "expand_bbox",
    "polygon_from_bbox",
    "bbox_from_polygon",
]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp ``value`` into ``[low, high]``."""
    if value < low:
        return low
    if value > high:
        return high
    return value


@dataclass(frozen=True, order=True)
class BBox:
    """Axis-aligned rectangle in a well-defined coordinate space.

    ``x0 <= x1`` and ``y0 <= y1`` are guaranteed for ``display`` space
    (top-left origin).  For ``raw`` PDF space the constructor sorts the pairs so
    the invariant still holds; callers that need the original corner order must
    keep their own polygon.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        # Read both values before writing either.  Swapping in place would
        # overwrite ``x0`` with ``x1`` and then copy that same value back into
        # ``x1``, collapsing the box to zero width instead of reversing it.
        if self.x0 > self.x1:
            x0, x1 = self.x1, self.x0
            object.__setattr__(self, "x0", x0)
            object.__setattr__(self, "x1", x1)
        if self.y0 > self.y1:
            y0, y1 = self.y1, self.y0
            object.__setattr__(self, "y0", y0)
            object.__setattr__(self, "y1", y1)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]

    @classmethod
    def from_any(cls, value: Sequence[float] | "BBox") -> "BBox":
        if isinstance(value, BBox):
            return value
        if len(value) != 4:
            raise ValueError(f"bbox needs 4 numbers, got {len(value)}")
        return cls(float(value[0]), float(value[1]), float(value[2]), float(value[3]))

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


Polygon = tuple[tuple[float, float], ...]


def polygon_from_bbox(box: BBox) -> Polygon:
    return ((box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1))


def bbox_from_polygon(points: Iterable[Sequence[float]]) -> BBox:
    pts = [(float(p[0]), float(p[1])) for p in points]
    if not pts:
        raise ValueError("empty polygon")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return BBox(min(xs), min(ys), max(xs), max(ys))


def bbox_area(box: BBox) -> float:
    return max(0.0, box.width) * max(0.0, box.height)


def union_bbox(boxes: Iterable[BBox]) -> BBox:
    items = list(boxes)
    if not items:
        raise ValueError("union of zero boxes is undefined")
    return BBox(
        min(b.x0 for b in items),
        min(b.y0 for b in items),
        max(b.x1 for b in items),
        max(b.y1 for b in items),
    )


def expand_bbox(box: BBox, pad: float) -> BBox:
    return BBox(box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad)


def iou(a: BBox, b: BBox) -> float:
    """Intersection over union; 0.0 when the boxes do not overlap."""
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0.0:
        return 0.0
    union = bbox_area(a) + bbox_area(b) - inter
    return inter / union if union > 0 else 0.0


def normalise_bbox(box: BBox, page: "PageGeometry") -> BBox:
    """Convert a raw PDF-space bbox into display-normalised coordinates."""
    return page.raw_to_display_box(box)


class CoordinateSpace(str):
    """Well-known coordinate space names."""

    RAW_PDF = "raw_pdf"          # points, origin bottom-left, y up
    RAW_IMAGE = "raw_image"      # pixels, origin top-left, y down
    DISPLAY_NORM = "display_norm"  # 0..1, origin top-left of displayed page


@dataclass(frozen=True)
class AffineTransform:
    """Row-vector affine transform ``[x y 1] * M``.

    ``x' = a*x + c*y + e`` and ``y' = b*x + d*y + f``.
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    @staticmethod
    def identity() -> "AffineTransform":
        return AffineTransform()

    @staticmethod
    def translation(tx: float, ty: float) -> "AffineTransform":
        return AffineTransform(1.0, 0.0, 0.0, 1.0, tx, ty)

    @staticmethod
    def scale(sx: float, sy: float) -> "AffineTransform":
        return AffineTransform(sx, 0.0, 0.0, sy, 0.0, 0.0)

    @staticmethod
    def rotation_deg(degrees: float, cx: float = 0.0, cy: float = 0.0) -> "AffineTransform":
        rad = math.radians(degrees)
        cos, sin = math.cos(rad), math.sin(rad)
        # translate to origin, rotate, translate back
        t1 = AffineTransform.translation(-cx, -cy)
        rot = AffineTransform(cos, sin, -sin, cos, 0.0, 0.0)
        t2 = AffineTransform.translation(cx, cy)
        return t1.then(rot).then(t2)

    def then(self, other: "AffineTransform") -> "AffineTransform":
        """Compose: apply ``self`` first, then ``other``."""
        return AffineTransform(
            a=self.a * other.a + self.b * other.c,
            b=self.a * other.b + self.b * other.d,
            c=self.c * other.a + self.d * other.c,
            d=self.c * other.b + self.d * other.d,
            e=self.e * other.a + self.f * other.c + other.e,
            f=self.e * other.b + self.f * other.d + other.f,
        )

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def apply_polygon(self, points: Iterable[Sequence[float]]) -> Polygon:
        return tuple(self.apply(float(p[0]), float(p[1])) for p in points)

    @property
    def determinant(self) -> float:
        return self.a * self.d - self.b * self.c

    def invert(self) -> "AffineTransform":
        det = self.determinant
        if abs(det) < 1e-12:
            raise ValueError("transform is not invertible")
        ia = self.d / det
        ib = -self.b / det
        ic = -self.c / det
        id_ = self.a / det
        ie = -(self.e * ia + self.f * ic)
        if_ = -(self.e * ib + self.f * id_)
        return AffineTransform(ia, ib, ic, id_, ie, if_)

    def to_dict(self) -> dict[str, float]:
        return {"a": self.a, "b": self.b, "c": self.c, "d": self.d, "e": self.e, "f": self.f}

    @classmethod
    def from_dict(cls, data: dict[str, float] | None) -> "AffineTransform":
        if not data:
            return cls.identity()
        return cls(
            float(data.get("a", 1.0)),
            float(data.get("b", 0.0)),
            float(data.get("c", 0.0)),
            float(data.get("d", 1.0)),
            float(data.get("e", 0.0)),
            float(data.get("f", 0.0)),
        )


@dataclass(frozen=True)
class PageGeometry:
    """Everything needed to map between raw source space and display space.

    ``rotation`` follows PDF semantics: the page is rotated *clockwise* by this
    many degrees when displayed.  ``crop`` is the visible rectangle in raw
    coordinates (``None`` means the full media box).
    """

    media_width: float
    media_height: float
    rotation: int = 0
    crop: BBox | None = None
    dpi: float = 72.0

    def __post_init__(self) -> None:
        rot = int(self.rotation) % 360
        if rot not in (0, 90, 180, 270):
            raise ValueError(f"rotation must be a multiple of 90, got {self.rotation}")
        object.__setattr__(self, "rotation", rot)
        if self.media_width <= 0 or self.media_height <= 0:
            raise ValueError("media box must have positive size")

    # -- derived -----------------------------------------------------------
    @property
    def visible(self) -> BBox:
        return self.crop or BBox(0.0, 0.0, self.media_width, self.media_height)

    @property
    def display_width(self) -> float:
        """Width of the page as displayed, in raw units (points)."""
        return self.visible.height if self.rotation in (90, 270) else self.visible.width

    @property
    def display_height(self) -> float:
        return self.visible.width if self.rotation in (90, 270) else self.visible.height

    @property
    def aspect_ratio(self) -> float:
        return self.display_width / self.display_height

    # -- transforms --------------------------------------------------------
    def raw_to_norm(self) -> AffineTransform:
        """Raw PDF space -> normalised display space in one step.

        Normalised display space has its origin at the top-left of the displayed
        (cropped and rotated) page, y grows downwards, and both axes run 0..1.

        The four cases, with ``w``/``h`` the visible size in raw space and
        ``x0``/``y0`` the crop origin:

        ==== ================================================
        rot  mapping
        ==== ================================================
        0    ``((x - x0)/w, 1 - (y - y0)/h)``
        90   ``((y - y0)/h, (x - x0)/w)``
        180  ``(1 - (x - x0)/w, (y - y0)/h)``
        270  ``(1 - (y - y0)/h, 1 - (x - x0)/w)``
        ==== ================================================

        Read as "rotate the page a quarter turn clockwise about its centre, then
        put the origin at the top-left".  The raw bottom-left corner therefore
        travels bottom-left, then bottom-left again for a quarter turn only in
        the sense that the sheet turns and the corner moves to where the *next*
        side of the sheet now is -- which is why the mapping is best checked
        against rendered pixels rather than reasoned about in prose.

        An earlier version of this method had the wrong origin for every rotation
        and a left-right reflection for two of them, so anchors on a rotated page
        pointed at the wrong place while an unrotated page looked perfect.

        These coefficients were **measured**, and the closed form above was
        confirmed to reproduce the measurements exactly: ``scripts/derive_rotation3.py``
        renders one dark marker per page in a known raw corner and locates it, and
        ``scripts/fit_rotation.py`` solves the affine map from those positions,
        reporting a maximum error of 0.000000.  ``tests/unit/test_rotation_geometry.py``
        repeats the measurement as a test.
        """
        vis = self.visible
        x0, y0 = vis.x0, vis.y0
        w, h = vis.width, vis.height
        if w <= 0 or h <= 0:
            return AffineTransform.identity()
        # Coefficients in normalised units: x' = a*x + c*y + e, y' = b*x + d*y + f.
        rot = self.rotation
        if rot == 0:
            a, b, c, d = 1.0 / w, 0.0, 0.0, -1.0 / h
            e, f = -x0 / w, 1.0 + y0 / h
        elif rot == 90:
            a, b, c, d = 0.0, 1.0 / w, 1.0 / h, 0.0
            e, f = -y0 / h, -x0 / w
        elif rot == 180:
            a, b, c, d = -1.0 / w, 0.0, 0.0, 1.0 / h
            e, f = 1.0 + x0 / w, -y0 / h
        else:  # rot == 270
            a, b, c, d = 0.0, -1.0 / w, -1.0 / h, 0.0
            e, f = 1.0 + y0 / h, 1.0 + x0 / w
        return AffineTransform(a, b, c, d, e, f)

    def norm_to_raw(self) -> AffineTransform:
        return self.raw_to_norm().invert()

    def raw_to_display(self) -> AffineTransform:
        """Raw PDF space -> display space in points, origin top-left, y down."""
        return self.raw_to_norm().then(self.norm_to_display())

    def display_to_raw(self) -> AffineTransform:
        return self.raw_to_display().invert()

    def display_to_norm(self) -> AffineTransform:
        return AffineTransform.scale(1.0 / self.display_width, 1.0 / self.display_height)

    def norm_to_display(self) -> AffineTransform:
        return AffineTransform.scale(self.display_width, self.display_height)

    def display_corners(self) -> dict[str, tuple[float, float]]:
        """Where each raw corner of the visible area lands in display space.

        This is the property the reader depends on and the one that is easy to
        get wrong: which raw corner becomes the displayed top-left depends on the
        rotation.  Exposing it makes the expectation checkable at runtime and in
        tests, rather than being implicit in a matrix.
        """
        vis = self.visible
        raw_corners = {
            "raw_tl": (vis.x0, vis.y1),
            "raw_tr": (vis.x1, vis.y1),
            "raw_br": (vis.x1, vis.y0),
            "raw_bl": (vis.x0, vis.y0),
        }
        transform = self.raw_to_display()
        return {name: transform.apply(*point) for name, point in raw_corners.items()}

    # -- convenience -------------------------------------------------------
    def raw_to_display_box(self, box: BBox) -> BBox:
        pts = self.raw_to_display().apply_polygon(polygon_from_bbox(box))
        return bbox_from_polygon(pts)

    def raw_to_norm_box(self, box: BBox) -> BBox:
        return self.raw_to_norm_box_via_polygon(box)

    def raw_to_norm_box_via_polygon(self, box: BBox) -> BBox:
        """Normalise a raw bbox.

        We transform the polygon corners and then take the axis-aligned hull,
        which is correct for the 90-degree rotations we support.
        """
        pts = self.raw_to_norm().apply_polygon(polygon_from_bbox(box))
        norm = bbox_from_polygon(pts)
        return BBox(clamp(norm.x0), clamp(norm.y0), clamp(norm.x1), clamp(norm.y1))

    def norm_to_raw_box(self, box: BBox) -> BBox:
        pts = self.norm_to_raw().apply_polygon(polygon_from_bbox(box))
        return bbox_from_polygon(pts)

    def to_dict(self) -> dict:
        return {
            "media_width": self.media_width,
            "media_height": self.media_height,
            "rotation": self.rotation,
            "crop": self.crop.as_list() if self.crop else None,
            "dpi": self.dpi,
            "display_width": self.display_width,
            "display_height": self.display_height,
        }
