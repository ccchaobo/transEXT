"""Rotation geometry, verified against rendered pixels rather than asserted.

``PageGeometry.raw_to_norm`` must agree with the renderer the reader actually
uses.  A restatement of the transform would pass even if the transform were wrong,
and an earlier version was exactly that: it had the wrong origin for every
rotation, so anchors on a rotated page pointed at the wrong place while an
unrotated page looked perfect.

So the checks here are derived from an independent measurement.  Labelled markers
are drawn at known raw positions, the page is rendered, and each marker is located
in the resulting pixels.  The transform's answer must point at the same place.

Two things are deliberately not conflated here:

* **agreement** -- does the transform land on the pixels the renderer produced;
* **corner identity** -- which raw corner becomes which displayed corner.

Agreement is measured with markers placed fully inside the page, because a marker
clipped by the page edge has a biased centroid and would blur the comparison.
Corner identity is an exact property and is checked in ``test_coords.py``.

Requires PyMuPDF, which is a project dependency.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

from document_core.coords import BBox, PageGeometry

PAGE_W = 400.0
PAGE_H = 600.0
MARK = 24.0
DPI = 150
#: Inset at which markers sit fully inside the page, so their centroids are not
#: biased by clipping.
INSET = MARK

#: Positions at which markers are drawn, in **raw PDF space** (origin
#: bottom-left, y upwards).  They are inset so each marker lies fully inside the
#: page; a clipped marker has a biased centroid.
POSITIONS = {
    "low_left": (INSET, INSET),
    "low_right": (PAGE_W - INSET, INSET),
    "high_left": (INSET, PAGE_H - INSET),
    "high_right": (PAGE_W - INSET, PAGE_H - INSET),
}
#: Distinct hues, so each marker is identifiable in a single render.  Greys were
#: tried first and were unreliable: the resampling that a rotation implies leaves
#: a gradient of near-marker pixels, and any threshold wide enough to capture a
#: marker also captured background, which pulled the reported position toward the
#: image centre.  A hue test on a colour channel cannot be satisfied by grey
#: background at all.
COLOURS = {
    "low_left": (1.0, 0.0, 0.0),   # red
    "low_right": (0.0, 1.0, 0.0),  # green
    "high_left": (0.0, 0.0, 1.0),  # blue
    "high_right": (1.0, 1.0, 0.0), # yellow
}


def _to_page_units(x: float, y: float) -> tuple[float, float]:
    """Convert a raw PDF point to PyMuPDF page units.

    ``Page.draw_rect`` takes the page's own coordinate system, whose origin is the
    top-left of the *unrotated* media box with y increasing downwards, whereas the
    marker positions here are stated in raw PDF space with y increasing upwards.
    Forgetting this flip is what made an earlier version of this test compare a
    predicted bottom-left point against a marker drawn at the top-left.
    """
    return x, PAGE_H - y


def _build_marked_pdf(rotation: int, directory: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    for name, (raw_x, raw_y) in POSITIONS.items():
        colour = COLOURS[name]
        x, y = _to_page_units(raw_x, raw_y)
        page.draw_rect(
            pymupdf.Rect(x - MARK / 2, y - MARK / 2, x + MARK / 2, y + MARK / 2),
            color=colour,
            fill=colour,
        )
    page.set_rotation(rotation)
    path = directory / f"corners_{rotation}.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _locate(image: Image.Image, colour: tuple[float, float, float]) -> tuple[float, float]:
    """Centre of the marker of the given colour, in normalised top-left coords.

    A pixel belongs to the marker when its dominant channel is the marker's
    dominant channel.  For the yellow marker the blue channel is the one required
    to be low, so the test is stated as "the channel the marker lacks is dim",
    which covers all four colours: red lacks green and blue, green lacks red and
    blue, blue lacks red and green, yellow lacks blue.
    """
    array = np.asarray(image.convert("RGB")).astype("float32")
    red, green, blue = array[:, :, 0], array[:, :, 1], array[:, :, 2]
    present = [channel for channel, value in enumerate(colour) if value > 0.5]
    absent = [channel for channel, value in enumerate(colour) if value <= 0.5]
    channels = (red, green, blue)
    # The marker's own channels must be strong.
    mask = np.ones_like(red, dtype=bool)
    for channel in present:
        mask &= channels[channel] > 110.0
    # Every other channel must be weak; yellow has two present and one absent.
    for channel in absent:
        mask &= channels[channel] < 110.0
    assert mask.any(), f"marker with colour {colour} not found in the render"
    rows, columns = np.nonzero(mask)
    height, width = red.shape
    return (
        (columns.min() + columns.max() + 1) / 2.0 / width,
        (rows.min() + rows.max() + 1) / 2.0 / height,
    )


def _render(path: Path) -> Image.Image:
    doc = pymupdf.open(str(path))
    page = doc.load_page(0)
    pixmap = page.get_pixmap(dpi=DPI, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    doc.close()
    return image


def _geometry_for(path: Path) -> PageGeometry:
    doc = pymupdf.open(str(path))
    page = doc.load_page(0)
    geometry = PageGeometry(
        media_width=page.mediabox.width,
        media_height=page.mediabox.height,
        rotation=int(page.rotation),
    )
    doc.close()
    return geometry


@pytest.fixture(scope="module")
def marked(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Path]:
    directory = tmp_path_factory.mktemp("rotation")
    return {rotation: _build_marked_pdf(rotation, directory) for rotation in (0, 90, 180, 270)}


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_geometry_agrees_with_rendered_pixels(marked: dict[int, Path], rotation: int) -> None:
    path = marked[rotation]
    geometry = _geometry_for(path)
    image = _render(path)
    # Measured agreement with colour matching is within one pixel, so this allows
    # two.  It is far smaller than the distance between markers, so a transform
    # that sent a point to the wrong marker still fails.
    tolerance = 2.0 / min(image.size)

    transform = geometry.raw_to_norm()
    for name, raw in POSITIONS.items():
        predicted = transform.apply(*raw)
        measured = _locate(image, COLOURS[name])
        assert predicted[0] == pytest.approx(measured[0], abs=tolerance), (
            f"rotation {rotation} {name}: x predicted {predicted[0]:.4f} "
            f"but the pixel is at {measured[0]:.4f}"
        )
        assert predicted[1] == pytest.approx(measured[1], abs=tolerance), (
            f"rotation {rotation} {name}: y predicted {predicted[1]:.4f} "
            f"but the pixel is at {measured[1]:.4f}"
        )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_markers_keep_their_relative_arrangement(
    marked: dict[int, Path], rotation: int
) -> None:
    """The four markers must not collide or swap places.

    Agreement alone could be satisfied by a transform that collapsed distinct
    points together, so the measured positions are also required to be distinct
    and to keep the arrangement the raw coordinates describe.
    """
    geometry = _geometry_for(marked[rotation])
    image = _render(marked[rotation])
    measured = {name: _locate(image, COLOURS[name]) for name in POSITIONS}

    points = list(measured.values())
    for i, first in enumerate(points):
        for second in points[i + 1 :]:
            assert abs(first[0] - second[0]) + abs(first[1] - second[1]) > 0.05, (
                rotation,
                measured,
            )

    # The transform must order them the same way the pixels do.
    transform = geometry.raw_to_norm()
    for name, raw in POSITIONS.items():
        predicted = transform.apply(*raw)
        for other, other_raw in POSITIONS.items():
            if other == name:
                continue
            other_predicted = transform.apply(*other_raw)
            predicted_closer = (
                abs(predicted[0] - measured[name][0]) + abs(predicted[1] - measured[name][1])
                < abs(other_predicted[0] - measured[name][0])
                + abs(other_predicted[1] - measured[name][1])
            )
            assert predicted_closer, (rotation, name, other)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_every_normalised_point_is_inside_the_unit_square(
    marked: dict[int, Path], rotation: int
) -> None:
    # A coordinate outside [0, 1] cannot be rendered and would silently clamp.
    geometry = _geometry_for(marked[rotation])
    transform = geometry.raw_to_norm()
    corners = {
        "tl": (0.0, PAGE_H),
        "tr": (PAGE_W, PAGE_H),
        "bl": (0.0, 0.0),
        "br": (PAGE_W, 0.0),
        "centre": (PAGE_W / 2, PAGE_H / 2),
    }
    for name, point in corners.items():
        x, y = transform.apply(*point)
        assert 0.0 <= x <= 1.0, f"rotation {rotation} {name}: x={x}"
        assert 0.0 <= y <= 1.0, f"rotation {rotation} {name}: y={y}"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_display_corners_helper_matches_an_explicit_corner_mapping(
    marked: dict[int, Path], rotation: int
) -> None:
    """``display_corners`` must agree with applying the transform to the corners.

    The helper is what callers use to reason about orientation, so it is checked
    against the transform computed from the visible rectangle's own corners
    rather than against itself.
    """
    geometry = _geometry_for(marked[rotation])
    visible = geometry.visible
    raw_corners = {
        "raw_tl": (visible.x0, visible.y1),
        "raw_tr": (visible.x1, visible.y1),
        "raw_br": (visible.x1, visible.y0),
        "raw_bl": (visible.x0, visible.y0),
    }
    expected = geometry.raw_to_display()
    corners = geometry.display_corners()
    assert sorted(corners) == sorted(raw_corners)
    for name, raw in raw_corners.items():
        assert corners[name] == pytest.approx(expected.apply(*raw))
    # And the four corners must really be four distinct points.
    assert len({(round(x, 6), round(y, 6)) for x, y in corners.values()}) == 4


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_display_size_matches_the_render_aspect(
    marked: dict[int, Path], rotation: int
) -> None:
    geometry = _geometry_for(marked[rotation])
    image = _render(marked[rotation])
    rendered_aspect = image.size[0] / image.size[1]
    assert geometry.aspect_ratio == pytest.approx(rendered_aspect, rel=0.02)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_norm_to_raw_round_trips(marked: dict[int, Path], rotation: int) -> None:
    geometry = _geometry_for(marked[rotation])
    forward = geometry.raw_to_norm()
    backward = geometry.norm_to_raw()
    for raw in POSITIONS.values():
        assert backward.apply(*forward.apply(*raw)) == pytest.approx(raw, abs=1e-6)


def test_crop_offset_shifts_the_mapping() -> None:
    """A cropped page's origin is the crop corner, not the media corner."""
    geometry = PageGeometry(
        PAGE_W + 100,
        PAGE_H + 100,
        crop=BBox(50, 50, 50 + PAGE_W, 50 + PAGE_H),
    )
    # The crop's own top-left raw corner must map to the display origin.
    assert geometry.raw_to_norm().apply(50.0, 50.0 + PAGE_H) == pytest.approx(
        (0.0, 0.0), abs=1e-9
    )
    # And its bottom-right to the far corner.
    assert geometry.raw_to_norm().apply(50.0 + PAGE_W, 50.0) == pytest.approx(
        (1.0, 1.0), abs=1e-9
    )
    # A point inside the crop but outside it in the media frame must still be
    # normalised against the crop, not the media box.
    x, y = geometry.raw_to_norm().apply(50.0 + PAGE_W / 2, 50.0 + PAGE_H / 2)
    assert (x, y) == pytest.approx((0.5, 0.5), abs=1e-9)


class TestQuickCheckTableStaysTrue:
    """``scripts/check_rotation.py`` holds a table that once went stale.

    The stale table reported the rotated cases as failures while the implementation
    was correct, because it had been taken from an earlier, wrong derivation.  A
    hard-coded expectation that nothing re-measures is exactly how the original
    rotation bug survived, so the table is asserted against the implementation here
    as well as inside the script that owns it.
    """

    def test_the_scripts_measured_table_matches_the_implementation(self) -> None:
        from document_core.coords import PageGeometry

        from scripts.check_rotation import H, MEASURED, RAW, W

        for rotation, expected in MEASURED.items():
            geometry = PageGeometry(W, H, rotation=rotation)
            transform = geometry.raw_to_norm()
            for name, raw_point in RAW.items():
                got = transform.apply(*raw_point)
                want = expected[name]
                assert got == pytest.approx(want, abs=1e-9), (
                    f"rotation {rotation} corner {name}: "
                    f"the table says {want} but the transform gives {got}"
                )

    def test_the_table_covers_every_rotation(self) -> None:
        from scripts.check_rotation import MEASURED

        assert set(MEASURED) == {0, 90, 180, 270}

    def test_the_table_names_four_corners_per_rotation(self) -> None:
        from scripts.check_rotation import MEASURED

        for rotation, corners in MEASURED.items():
            assert set(corners) == {"bl", "br", "tl", "tr"}, rotation
