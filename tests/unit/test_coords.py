"""Coordinate contract tests (spec §8, ADR 0008).

These are the tests that matter most: every anchor in the product depends on this
module being right, and the properties are checkable independently of how a PDF
was produced, because a page's geometry is declared by the file itself.

Two contract details are pinned here deliberately, because both are easy to get
wrong in a way that stays invisible until a user clicks a sentence and lands on
the wrong paragraph:

* the origin is the top-left of the *displayed* page with y growing downwards;
* which raw corner becomes that origin depends on the rotation.

A third detail is pinned because it is a real constraint rather than a property:
``PageGeometry.rotation`` accepts only multiples of 90 and raises otherwise.  An
arbitrary skew angle therefore cannot be smuggled into ``rotation``; it is
represented by an ``AffineTransform`` (ADR 0008).
"""

from __future__ import annotations

import math

import pytest

from document_core.coords import (
    AffineTransform,
    BBox,
    CoordinateSpace,
    PageGeometry,
    bbox_area,
    bbox_from_polygon,
    clamp,
    expand_bbox,
    iou,
    normalise_bbox,
    polygon_from_bbox,
    union_bbox,
)

A4_W = 595.276
A4_H = 841.89


def _corners() -> dict[str, tuple[float, float]]:
    """The four raw-space corners of the page, named by where they start."""
    return {
        "top_left": (0.0, A4_H),
        "top_right": (A4_W, A4_H),
        "bottom_left": (0.0, 0.0),
        "bottom_right": (A4_W, 0.0),
    }


#: For each page rotation, the raw-space corner that ends up at each **displayed**
#: corner.  Keys are displayed positions; values are raw-space corners.
#:
#: Measured by rendering a marker in a known raw corner and locating the dark
#: pixels (``scripts/derive_rotation2.py``), and repeated independently in
#: ``tests/unit/test_rotation_geometry.py``.  These are measurements of how a
#: viewer really displays a rotated page, not a restatement of the transform: an
#: earlier version of ``raw_to_display`` had the wrong origin for every rotation,
#: and a table derived from the code would have agreed with that bug.
#:
#: Note that a quarter turn maps the page onto itself corner for corner, so the
#: *set* of corners is the same at every rotation while the *assignment* between
#: raw and displayed positions differs.  Only the raw origin's journey is easy to
#: remember: bottom-left, then top-right, then top-left, then bottom-right.
#:
#: ``test_the_mapping_table_agrees_with_the_transform`` checks this table against
#: the implementation so the two cannot drift apart silently.
DISPLAY_TO_RAW: dict[int, dict[str, tuple[float, float]]] = {
    0: {
        "top_left": (0.0, A4_H),
        "top_right": (A4_W, A4_H),
        "bottom_left": (0.0, 0.0),
        "bottom_right": (A4_W, 0.0),
    },
    90: {
        "top_left": (0.0, 0.0),
        "top_right": (0.0, A4_H),
        "bottom_left": (A4_W, 0.0),
        "bottom_right": (A4_W, A4_H),
    },
    180: {
        "top_left": (A4_W, 0.0),
        "top_right": (0.0, 0.0),
        "bottom_left": (A4_W, A4_H),
        "bottom_right": (0.0, A4_H),
    },
    270: {
        "top_left": (A4_W, A4_H),
        "top_right": (A4_W, 0.0),
        "bottom_left": (0.0, A4_H),
        "bottom_right": (0.0, 0.0),
    },
}


class TestBBox:
    def test_dimensions_and_centre(self) -> None:
        box = BBox(10, 20, 30, 60)
        assert box.width == pytest.approx(20)
        assert box.height == pytest.approx(40)
        assert box.centre == pytest.approx((20.0, 40.0))

    def test_area_helper_matches_dimensions(self) -> None:
        box = BBox(10, 20, 30, 60)
        assert bbox_area(box) == pytest.approx(box.width * box.height)

    def test_empty_box_has_zero_area(self) -> None:
        assert bbox_area(BBox(10, 10, 10, 10)) == 0

    def test_constructor_sorts_corners(self) -> None:
        # The class guarantees an ordered box, so callers cannot accidentally
        # produce a negative-width region by passing corners in reverse.
        box = BBox(30, 60, 10, 20)
        assert box.as_tuple() == pytest.approx((10.0, 20.0, 30.0, 60.0))

    def test_as_tuple_and_as_list(self) -> None:
        box = BBox(0.1, 0.2, 0.3, 0.4)
        assert box.as_tuple() == pytest.approx((0.1, 0.2, 0.3, 0.4))
        assert box.as_list() == pytest.approx([0.1, 0.2, 0.3, 0.4])

    def test_to_dict_round_trips_through_from_any(self) -> None:
        box = BBox(1, 2, 3, 4)
        assert BBox.from_any(box) is box
        assert BBox.from_any(box.as_list()) == box

    def test_from_any_rejects_wrong_arity(self) -> None:
        with pytest.raises(ValueError):
            BBox.from_any([1, 2, 3])

    def test_equality_and_ordering(self) -> None:
        assert BBox(0, 0, 1, 1) == BBox(0, 0, 1, 1)
        assert BBox(0, 0, 1, 1) < BBox(0, 0, 1, 2)


class TestBBoxHelpers:
    def test_iou_of_identical_boxes_is_one(self) -> None:
        box = BBox(1, 2, 3, 4)
        assert iou(box, box) == pytest.approx(1.0)

    def test_iou_of_disjoint_boxes_is_zero(self) -> None:
        assert iou(BBox(0, 0, 1, 1), BBox(5, 5, 6, 6)) == pytest.approx(0.0)

    def test_iou_of_touching_boxes_is_zero(self) -> None:
        # Zero-area overlap must not count as overlap: adjacent lines in a
        # paragraph touch but are not the same line.
        assert iou(BBox(0, 0, 10, 10), BBox(10, 0, 20, 10)) == pytest.approx(0.0)

    def test_iou_is_symmetric(self) -> None:
        a = BBox(0, 0, 10, 10)
        b = BBox(5, 5, 15, 15)
        assert iou(a, b) == pytest.approx(iou(b, a))

    def test_iou_of_half_overlap(self) -> None:
        # Two 10x10 boxes overlapping in a 5x5 square:
        # intersection 25, union 100 + 100 - 25 = 175.
        assert iou(BBox(0, 0, 10, 10), BBox(5, 5, 15, 15)) == pytest.approx(25 / 175)

    def test_union_bbox(self) -> None:
        assert union_bbox([BBox(0, 0, 1, 1), BBox(2, 2, 3, 3)]) == BBox(0, 0, 3, 3)

    def test_union_of_nothing_is_undefined(self) -> None:
        with pytest.raises(ValueError):
            union_bbox([])

    def test_expand_bbox(self) -> None:
        assert expand_bbox(BBox(1, 1, 2, 2), 1) == BBox(0, 0, 3, 3)

    def test_polygon_round_trip(self) -> None:
        box = BBox(0, 0, 10, 20)
        polygon = polygon_from_bbox(box)
        assert len(polygon) == 4
        assert bbox_from_polygon(polygon) == box

    def test_bbox_from_polygon_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            bbox_from_polygon([])

    def test_bbox_from_polygon_takes_the_hull(self) -> None:
        assert bbox_from_polygon([(0, 5), (10, 0), (3, 9)]) == BBox(0, 0, 10, 9)


class TestClamp:
    @pytest.mark.parametrize(
        "value,expected",
        [(-1.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (2.0, 1.0)],
    )
    def test_clamp(self, value: float, expected: float) -> None:
        assert clamp(value) == pytest.approx(expected)

    def test_clamp_with_custom_bounds(self) -> None:
        assert clamp(5.0, 1.0, 3.0) == pytest.approx(3.0)


class TestAffineTransform:
    def test_identity_leaves_points_alone(self) -> None:
        assert AffineTransform.identity().apply(3.0, 4.0) == pytest.approx((3.0, 4.0))

    def test_translation(self) -> None:
        assert AffineTransform.translation(2, 3).apply(1, 1) == pytest.approx((3.0, 4.0))

    def test_scale(self) -> None:
        assert AffineTransform.scale(2, 3).apply(1, 1) == pytest.approx((2.0, 3.0))

    def test_then_composes_applying_self_first(self) -> None:
        # Scale by 2, then translate by 10: 1 * 2 + 10 = 12.
        combined = AffineTransform.scale(2, 2).then(AffineTransform.translation(10, 0))
        assert combined.apply(1, 0) == pytest.approx((12.0, 0.0))

    def test_rotation_deg_maps_axes(self) -> None:
        # A +90 degree rotation in this row-vector convention takes +x to +y.
        assert AffineTransform.rotation_deg(90).apply(1.0, 0.0) == pytest.approx(
            (0.0, 1.0), abs=1e-9
        )

    def test_rotation_deg_about_a_centre_leaves_the_centre_fixed(self) -> None:
        centre = (100.0, 50.0)
        rotated = AffineTransform.rotation_deg(37.5, *centre)
        assert rotated.apply(*centre) == pytest.approx(centre, abs=1e-9)

    def test_rotation_360_is_identity(self) -> None:
        assert AffineTransform.rotation_deg(360).apply(5.0, 7.0) == pytest.approx((5.0, 7.0))

    def test_rotation_preserves_distance(self) -> None:
        # A rotation is an isometry: this catches a scaling error that a
        # single-point check would miss.
        transformed = AffineTransform.rotation_deg(23.0)
        p1 = transformed.apply(0.0, 0.0)
        p2 = transformed.apply(3.0, 4.0)
        assert math.dist(p1, p2) == pytest.approx(5.0)

    def test_invert_round_trips_points(self) -> None:
        transform = (
            AffineTransform.translation(-30, -40)
            .then(AffineTransform.rotation_deg(90))
            .then(AffineTransform.scale(2.5, 2.5))
        )
        inverse = transform.invert()
        for point in ((0.0, 0.0), (11.5, -3.25), (A4_W, A4_H)):
            assert inverse.apply(*transform.apply(*point)) == pytest.approx(point, abs=1e-9)

    def test_invert_raises_for_a_degenerate_scale(self) -> None:
        with pytest.raises(ValueError):
            AffineTransform.scale(0, 1).invert()

    def test_determinant_of_a_rotation_is_one(self) -> None:
        assert AffineTransform.rotation_deg(45).determinant == pytest.approx(1.0)

    def test_apply_polygon_preserves_point_order(self) -> None:
        polygon = ((0, 0), (10, 0), (10, 10), (0, 10))
        transformed = AffineTransform.scale(2, 2).apply_polygon(polygon)
        # Compare point by point: approx() cannot be applied to a nested
        # sequence, and the point order is exactly what this test is about.
        expected = ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0))
        assert len(transformed) == len(expected)
        for got, want in zip(transformed, expected, strict=True):
            assert got == pytest.approx(want)

    def test_dict_round_trip(self) -> None:
        transform = AffineTransform.scale(2, 3).then(AffineTransform.translation(1, 2))
        assert AffineTransform.from_dict(transform.to_dict()) == transform

    def test_from_dict_of_none_is_identity(self) -> None:
        assert AffineTransform.from_dict(None) == AffineTransform.identity()


class TestPageGeometryTopLeftOrigin:
    """Origin is the top-left of the displayed page, with y growing downwards."""

    def test_raw_top_left_maps_to_the_origin_for_no_rotation(self) -> None:
        geo = PageGeometry(A4_W, A4_H)
        # In raw PDF space, y grows upwards, so the top edge is y = height.
        assert geo.raw_to_norm().apply(0.0, A4_H) == pytest.approx((0.0, 0.0), abs=1e-9)

    def test_raw_bottom_right_maps_to_the_far_corner(self) -> None:
        geo = PageGeometry(A4_W, A4_H)
        assert geo.raw_to_norm().apply(A4_W, 0.0) == pytest.approx((1.0, 1.0), abs=1e-9)

    def test_left_edge_is_x_zero_and_right_edge_is_x_one(self) -> None:
        geo = PageGeometry(A4_W, A4_H)
        assert geo.raw_to_norm().apply(0.0, 0.0)[0] == pytest.approx(0.0, abs=1e-9)
        assert geo.raw_to_norm().apply(A4_W, 0.0)[0] == pytest.approx(1.0, abs=1e-9)

    def test_display_size_matches_media_for_an_unrotated_page(self) -> None:
        geo = PageGeometry(A4_W, A4_H)
        assert geo.display_width == pytest.approx(A4_W)
        assert geo.display_height == pytest.approx(A4_H)

    @pytest.mark.parametrize("rotation", [90, 270])
    def test_quarter_turns_swap_the_display_dimensions(self, rotation: int) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        assert geo.display_width == pytest.approx(A4_H)
        assert geo.display_height == pytest.approx(A4_W)

    @pytest.mark.parametrize("rotation", [0, 180])
    def test_half_turns_keep_the_display_dimensions(self, rotation: int) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        assert geo.display_width == pytest.approx(A4_W)
        assert geo.display_height == pytest.approx(A4_H)

    @pytest.mark.parametrize(
        "rotation,corner",
        [
            (0, (0.0, 1.0)),      # raw origin -> displayed bottom-left
            (90, (0.0, 0.0)),     # a quarter turn -> displayed top-left
            (180, (1.0, 0.0)),    # a half turn -> displayed top-right
            (270, (1.0, 1.0)),    # three quarters -> displayed bottom-right
        ],
    )
    def test_the_raw_bottom_left_corner_moves_like_a_clockwise_turn(
        self, rotation: int, corner: tuple[float, float]
    ) -> None:
        # The raw origin is the clearest landmark, and each quarter turn moves it
        # to a different displayed corner.  The table is measured from rendered
        # markers by scripts/derive_rotation3.py; the same measurement is repeated
        # as a test in tests/unit/test_rotation_geometry.py.
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        assert geo.raw_to_norm().apply(0.0, 0.0) == pytest.approx(corner, abs=1e-9)

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_the_visually_top_left_corner_maps_to_the_origin(self, rotation: int) -> None:
        # This is the property a viewer depends on and the one a naive y-flip
        # breaks: the raw corner that ends up visually top-left must map to the
        # display origin.  Which raw corner that is differs per rotation, so the
        # corner is looked up from the mapping table rather than hard-coded, and
        # the lookup itself is checked for agreement with the transform.
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        corner = DISPLAY_TO_RAW[rotation]["top_left"]
        assert geo.raw_to_norm().apply(*corner) == pytest.approx((0.0, 0.0), abs=1e-9)

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_the_visually_bottom_right_corner_maps_to_the_far_corner(
        self, rotation: int
    ) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        corner = DISPLAY_TO_RAW[rotation]["bottom_right"]
        assert geo.raw_to_norm().apply(*corner) == pytest.approx((1.0, 1.0), abs=1e-9)

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_the_mapping_table_agrees_with_the_transform(self, rotation: int) -> None:
        # Guards the table above: every raw corner named for a display position
        # must really land there, and the four names must be four distinct
        # corners.  Without this, a typo in the table would silently weaken the
        # two tests that consume it.
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        transform = geo.raw_to_norm()
        expected_display = {
            "top_left": (0.0, 0.0),
            "top_right": (1.0, 0.0),
            "bottom_left": (0.0, 1.0),
            "bottom_right": (1.0, 1.0),
        }
        named = DISPLAY_TO_RAW[rotation]
        assert sorted(named) == sorted(expected_display)
        assert len(set(named.values())) == 4, "the four names must be distinct corners"
        for name, corner in named.items():
            assert transform.apply(*corner) == pytest.approx(expected_display[name], abs=1e-9)

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_every_rotation_normalises_into_the_unit_square(self, rotation: int) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        for point in ((0.0, 0.0), (A4_W, 0.0), (A4_W, A4_H), (0.0, A4_H), (100.0, 200.0)):
            x, y = geo.raw_to_norm().apply(*point)
            assert -1e-9 <= x <= 1 + 1e-9, (rotation, point, x)
            assert -1e-9 <= y <= 1 + 1e-9, (rotation, point, y)

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_norm_to_raw_is_the_inverse_of_raw_to_norm(self, rotation: int) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        for point in ((0.0, 0.0), (0.25, 0.75), (A4_W / 2, A4_H / 3)):
            round_tripped = geo.norm_to_raw().apply(*geo.raw_to_norm().apply(*point))
            assert round_tripped == pytest.approx(point, abs=1e-6), (rotation, point)

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_box_normalisation_yields_an_ordered_in_range_box(self, rotation: int) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        box = geo.raw_to_norm_box(BBox(50, 100, 200, 300))
        assert box.x0 <= box.x1
        assert box.y0 <= box.y1
        assert 0 <= box.x0 <= 1 and 0 <= box.x1 <= 1
        assert 0 <= box.y0 <= 1 and 0 <= box.y1 <= 1

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_box_normalisation_round_trips(self, rotation: int) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=rotation)
        original = BBox(50, 100, 200, 300)
        round_tripped = geo.norm_to_raw_box(geo.raw_to_norm_box(original))
        assert round_tripped.as_tuple() == pytest.approx(original.as_tuple(), abs=1e-6)

    def test_aspect_ratio(self) -> None:
        geo = PageGeometry(A4_W, A4_H)
        assert geo.aspect_ratio == pytest.approx(A4_W / A4_H)


class TestCropBox:
    """A crop offset must move the origin.  Using the media box origin would
    shift every anchor by the offset, which no screenshot would reveal."""

    def test_crop_offset_moves_the_origin(self) -> None:
        crop = BBox(100, 50, 100 + A4_W, 50 + A4_H)
        geo = PageGeometry(A4_W + 200, A4_H + 100, crop=crop)
        # The displayed area's top-left corner is the crop box's top-left.
        assert geo.raw_to_norm().apply(100.0, 50.0 + A4_H) == pytest.approx(
            (0.0, 0.0), abs=1e-9
        )

    def test_display_size_uses_the_crop_box(self) -> None:
        crop = BBox(100, 50, 100 + A4_W, 50 + A4_H)
        geo = PageGeometry(A4_W + 200, A4_H + 100, crop=crop)
        assert geo.display_width == pytest.approx(A4_W)
        assert geo.display_height == pytest.approx(A4_H)

    def test_visible_defaults_to_the_media_box(self) -> None:
        geo = PageGeometry(A4_W, A4_H)
        assert geo.visible == BBox(0.0, 0.0, A4_W, A4_H)

    def test_crop_round_trip(self) -> None:
        crop = BBox(72, 36, 72 + A4_W, 36 + A4_H)
        geo = PageGeometry(A4_W + 144, A4_H + 72, crop=crop)
        point = (200.0, 400.0)
        assert geo.norm_to_raw().apply(*geo.raw_to_norm().apply(*point)) == pytest.approx(
            point, abs=1e-6
        )


class TestRotationConstraint:
    """An arbitrary skew cannot be expressed as a page rotation."""

    def test_non_multiple_of_90_rotation_is_rejected(self) -> None:
        # Skew is real (the corpus contains a 1.8 degree scan) but it is not a
        # page rotation: PDF /Rotate is restricted to quarter turns.  Allowing an
        # arbitrary angle here would silently produce a geometry whose display
        # dimensions and corner mapping are wrong.
        with pytest.raises(ValueError):
            PageGeometry(A4_W, A4_H, rotation=1.8)

    def test_skew_is_expressible_as_a_transform(self) -> None:
        # The supported way to represent deskew/skew is an AffineTransform
        # composed with the page geometry's transform.  The skew must be
        # expressed in raw page units and applied *before* normalisation:
        # composing it after would rotate about the point (W/2, H/2) inside a
        # 0..1 space, which is not where the page centre is.
        geo = PageGeometry(A4_W, A4_H)
        skew = AffineTransform.rotation_deg(-1.8, A4_W / 2, A4_H / 2)
        expected = geo.raw_to_norm().apply(A4_W / 2, A4_H / 2)
        combined = skew.then(geo.raw_to_norm())
        assert combined.apply(A4_W / 2, A4_H / 2) == pytest.approx(expected, abs=1e-9)

    def test_negative_rotation_is_normalised_to_a_quarter_turn(self) -> None:
        assert PageGeometry(A4_W, A4_H, rotation=-90).rotation == 270

    def test_rotation_of_450_normalises_to_90(self) -> None:
        assert PageGeometry(A4_W, A4_H, rotation=450).rotation == 90


class TestDegenerateGeometry:
    def test_media_box_must_have_positive_size(self) -> None:
        with pytest.raises(ValueError):
            PageGeometry(0.0, 0.0)

    def test_negative_size_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            PageGeometry(-1.0, 100.0)


class TestDisplayTransform:
    def test_display_to_norm_scales_by_display_size(self) -> None:
        geo = PageGeometry(A4_W, A4_H)
        assert geo.display_to_norm().apply(A4_W / 4, A4_H / 2) == pytest.approx((0.25, 0.5))

    def test_norm_to_display_round_trips(self) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=90)
        for point in ((0.1, 0.2), (0.5, 0.5), (0.9, 0.8)):
            raw = geo.norm_to_display().apply(*point)
            assert geo.display_to_norm().apply(*raw) == pytest.approx(point, abs=1e-9)

    def test_raw_to_display_box_is_ordered_and_in_range(self) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=270)
        box = geo.raw_to_display_box(BBox(50, 100, 200, 300))
        assert box.x0 <= box.x1 and box.y0 <= box.y1
        assert 0 <= box.x0 and box.x1 <= geo.display_width + 1e-9
        assert 0 <= box.y0 and box.y1 <= geo.display_height + 1e-9


class TestModuleLevelHelpers:
    def test_normalise_bbox_matches_the_geometry_method(self) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=90)
        box = BBox(100, 100, 200, 300)
        assert normalise_bbox(box, geo).as_tuple() == pytest.approx(
            geo.raw_to_display_box(box).as_tuple()
        )

    def test_coordinate_space_names_are_distinct(self) -> None:
        assert CoordinateSpace.RAW_PDF != CoordinateSpace.DISPLAY_NORM
        assert CoordinateSpace.RAW_IMAGE == "raw_image"

    def test_geometry_to_dict_records_the_derived_sizes(self) -> None:
        geo = PageGeometry(A4_W, A4_H, rotation=90, crop=BBox(10, 10, 10 + A4_W, 10 + A4_H))
        data = geo.to_dict()
        assert data["rotation"] == 90
        assert data["display_width"] == pytest.approx(A4_H)
        assert data["display_height"] == pytest.approx(A4_W)
        assert data["crop"] == [10.0, 10.0, 10.0 + A4_W, 10.0 + A4_H]
