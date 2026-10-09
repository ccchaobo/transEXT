"""Tests for the OCR adapter.

The engine is not loaded here.  A fake engine that returns the tuple shape
RapidOCR returns is injected instead, so these tests check the adapter's own
logic - coordinate normalisation, degenerate input handling, confidence
accounting - without needing the model or several seconds of start-up.

The engine's *accuracy* is measured separately against the corpus
(``scripts/spike_ocr.py``); this file tests the contract around it.
"""

from __future__ import annotations

import pytest

from document_core.coords import PageGeometry
from rr_adapters.ocr import (
    MIN_LINE_CONFIDENCE,
    OCR_RENDER_DPI,
    OCRProvider,
    OCRResult,
    RapidOCRProvider,
    _box_to_normalised,
    _image_size,
    available_engine,
    ocr_lines_to_text_lines,
)

A4 = PageGeometry(595.276, 841.89)

# The shape RapidOCR returns: (quadrilateral, text, score).  The quadrilateral is
# four corner points in image pixels.
QUAD = [[100.0, 200.0], [900.0, 200.0], [900.0, 260.0], [100.0, 260.0]]


class FakeEngine:
    """Returns a fixed engine result and records how often it was called."""

    def __init__(self, result):
        self.result = result
        self.calls = 0
        self.last_image = None

    def __call__(self, image):
        self.calls += 1
        self.last_image = image
        return self.result, 0.0


def make_provider(result) -> tuple[RapidOCRProvider, FakeEngine]:
    engine = FakeEngine(result)
    return RapidOCRProvider(engine=engine), engine


class FakeImage:
    """Minimal stand-in exposing only ``shape``, as an array would."""

    def __init__(self, height: int, width: int):
        self.shape = (height, width, 3)


class TestBoxNormalisation:
    def test_a_full_page_box_becomes_the_unit_square(self) -> None:
        box = _box_to_normalised(
            [[0, 0], [100, 0], [100, 200], [0, 200]], 100, 200
        )
        assert box is not None
        assert (box.x0, box.y0, box.x1, box.y1) == (0.0, 0.0, 1.0, 1.0)

    def test_a_centred_box_is_normalised_by_the_image_extent(self) -> None:
        box = _box_to_normalised(
            [[100, 200], [900, 200], [900, 260], [100, 260]], 1000, 1000
        )
        assert box is not None
        assert box.x0 == pytest.approx(0.1)
        assert box.x1 == pytest.approx(0.9)
        assert box.y0 == pytest.approx(0.2)
        assert box.y1 == pytest.approx(0.26)

    def test_a_tilted_quadrilateral_becomes_its_axis_aligned_hull(self) -> None:
        # The engine may return a slightly rotated quad.  The stored contract is
        # an axis-aligned box, so the tilt is dropped deliberately.
        box = _box_to_normalised(
            [[10, 20], [100, 30], [95, 80], [5, 70]], 100, 100
        )
        assert box is not None
        assert box.x0 == pytest.approx(0.05)
        assert box.x1 == pytest.approx(1.0)
        assert box.y0 == pytest.approx(0.2)
        assert box.y1 == pytest.approx(0.8)

    def test_points_beyond_the_image_are_clamped(self) -> None:
        # A box reported one pixel outside the image must not produce a
        # coordinate outside 0-1, because every consumer assumes the unit square.
        box = _box_to_normalised(
            [[-5, -5], [105, -5], [105, 205], [-5, 205]], 100, 200
        )
        assert box is not None
        assert 0.0 <= box.x0 <= 1.0
        assert 0.0 <= box.y1 <= 1.0

    @pytest.mark.parametrize(
        "box,width,height",
        [
            ([], 100, 200),                     # no points
            (None, 100, 200),                   # absent
            ([[0, 0], [10, 0], [10, 10], [0, 10]], 0, 200),   # zero width
            ([[0, 0], [10, 0], [10, 10], [0, 10]], 100, 0),   # zero height
            ([[5, 5], [5, 5], [5, 5], [5, 5]], 100, 100),     # degenerate
        ],
    )
    def test_unusable_boxes_are_rejected(self, box, width, height) -> None:
        # Returning None rather than a zero-size box keeps the caller from
        # storing an anchor that can never be clicked.
        assert _box_to_normalised(box, width, height) is None


class TestImageSize:
    def test_reads_an_array_shape(self) -> None:
        assert _image_size(FakeImage(200, 100)) == (200, 100)

    def test_rejects_an_object_without_a_size(self) -> None:
        with pytest.raises(TypeError):
            _image_size(object())


class TestRecognition:
    def test_lines_are_returned_in_normalised_page_coordinates(self) -> None:
        provider, _engine = make_provider([(QUAD, "First line", 0.95)])
        result = provider.recognize(FakeImage(1000, 1000), A4)

        assert len(result.lines) == 1
        line = result.lines[0]
        assert line.text == "First line"
        assert 0.0 <= line.bbox.x0 < line.bbox.x1 <= 1.0
        assert 0.0 <= line.bbox.y0 < line.bbox.y1 <= 1.0

    def test_the_image_extent_is_used_not_the_page_size(self) -> None:
        # The engine's boxes are image pixels.  Dividing by the page's display
        # size would be right only when the render matches the page aspect
        # exactly, so the normalisation is checked against an image whose aspect
        # deliberately differs from A4's.
        provider, _engine = make_provider([(QUAD, "text", 0.9)])
        result = provider.recognize(FakeImage(500, 1000), A4)
        line = result.lines[0]
        assert line.bbox.x0 == pytest.approx(100 / 1000)
        assert line.bbox.y1 == pytest.approx(260 / 500)

    def test_the_engine_is_called_once_per_recognition(self) -> None:
        provider, engine = make_provider([(QUAD, "text", 0.9)])
        provider.recognize(FakeImage(1000, 1000), A4)
        assert engine.calls == 1

    def test_the_render_resolution_is_recorded(self) -> None:
        # Provenance: an anchor extracted at one resolution must say so, because
        # the resolution changes what was recognised.
        provider, _engine = make_provider([(QUAD, "text", 0.9)])
        result = provider.recognize(FakeImage(1000, 1000), A4)
        assert result.render_dpi == OCR_RENDER_DPI
        assert result.image_width == 1000
        assert result.image_height == 1000

    def test_an_empty_result_is_reported_as_an_issue_not_a_failure(self) -> None:
        # A blank page legitimately has no text.  The adapter cannot tell that
        # from a recognition failure, so it records the fact and lets the parser
        # judge it; silently returning nothing would hide a real problem.
        provider, _engine = make_provider(None)
        result = provider.recognize(FakeImage(1000, 1000), A4)
        assert result.lines == []
        assert "no_text_detected" in result.issues

    def test_blank_and_malformed_entries_are_dropped(self) -> None:
        provider, _engine = make_provider(
            [
                (QUAD, "kept", 0.9),
                (QUAD, "   ", 0.9),          # whitespace only
                (QUAD, "", 0.9),             # empty
                (QUAD, None, 0.9),           # missing text
                (None, "no box", 0.9),       # unusable box
            ]
        )
        result = provider.recognize(FakeImage(1000, 1000), A4)
        assert [line.text for line in result.lines] == ["kept"]

    def test_an_unparseable_score_does_not_discard_the_text(self) -> None:
        # Losing a line of text is worse than losing its confidence, so a bad
        # score degrades to 0.0 and the text survives.
        provider, _engine = make_provider([(QUAD, "text", "not-a-number")])
        result = provider.recognize(FakeImage(1000, 1000), A4)
        assert len(result.lines) == 1
        assert result.lines[0].confidence == 0.0

    def test_a_string_score_is_coerced(self) -> None:
        # The engine can yield a string score through a text path; a str would
        # otherwise poison the weighted mean with a TypeError.
        provider, _engine = make_provider([(QUAD, "text", "0.87")])
        result = provider.recognize(FakeImage(1000, 1000), A4)
        assert result.lines[0].confidence == pytest.approx(0.87)


class TestConfidenceAccounting:
    def test_mean_confidence_is_weighted_by_line_length(self) -> None:
        # One short uncertain line must not dominate a page of long confident
        # ones, which an unweighted mean would let it do.
        provider, _engine = make_provider(
            [
                (QUAD, "a" * 100, 0.9),
                (QUAD, "b", 0.1),
            ]
        )
        result = provider.recognize(FakeImage(1000, 1000), A4)
        assert result.mean_confidence > 0.85

    @pytest.mark.parametrize(
        "score,expected_low",
        [
            (0.99, 0),
            (MIN_LINE_CONFIDENCE + 0.01, 0),
            (MIN_LINE_CONFIDENCE - 0.01, 1),
            (0.0, 1),
        ],
    )
    def test_low_confidence_lines_are_counted_not_dropped(
        self, score: float, expected_low: int
    ) -> None:
        # Dropping a line hides it; counting it lets the reader flag it.  The
        # text must survive either way.
        provider, _engine = make_provider([(QUAD, "text", score)])
        result = provider.recognize(FakeImage(1000, 1000), A4)
        assert len(result.lines) == 1
        assert result.low_confidence_lines == expected_low
        if expected_low:
            assert any(issue.startswith("low_confidence_lines") for issue in result.issues)

    def test_confidence_is_not_used_to_choose_between_attempts(self) -> None:
        # A guard on the documented boundary: ADR 0004 measured confidence as a
        # *positive* correlate of error, so no code path here may treat a higher
        # score as a better result.  This test exists to fail if such a helper is
        # added to this module.
        import rr_adapters.ocr as module

        forbidden = [
            name
            for name in dir(module)
            if "best" in name.lower() and "confidence" in name.lower()
        ]
        assert forbidden == [], (
            "confidence-based selection was measured as worse than chance and must "
            f"not be added here: {forbidden}"
        )


class TestTextLineAdaptation:
    def test_lines_become_layout_lines_marked_as_ocr(self) -> None:
        provider, _engine = make_provider([(QUAD, "Recognised text", 0.93)])
        result = provider.recognize(FakeImage(1000, 1000), A4)
        lines = ocr_lines_to_text_lines(result, page_index=3)

        assert len(lines) == 1
        assert lines[0].text == "Recognised text"
        assert lines[0].source == "ocr"
        assert lines[0].page_index == 3
        assert lines[0].confidence == pytest.approx(0.93)

    def test_wrap_hyphens_are_not_removed_from_a_single_line(self) -> None:
        # A line ending in a hyphen is the input to the layout module's
        # de-hyphenation, which needs the following line to decide.  Cleaning it
        # here would destroy the evidence before layout ever sees it.
        provider, _engine = make_provider([(QUAD, "experi-", 0.9)])
        result = provider.recognize(FakeImage(1000, 1000), A4)
        lines = ocr_lines_to_text_lines(result)
        assert lines[0].text.endswith("-")


class TestEvidenceMatchesProduction:
    """The acceptance script must measure what the product actually does."""

    def test_the_ocr_spike_uses_the_production_render_resolution(self) -> None:
        # An earlier version of the evidence hard-coded 200 dpi while the code
        # calibrated 250, so the acceptance run passed at a resolution the product
        # never used.  This fails if the script grows its own default again.
        from scripts.spike_ocr import _production_dpi

        assert _production_dpi() == OCR_RENDER_DPI

    def test_the_spike_has_no_hard_coded_dpi_default(self) -> None:
        import inspect

        import scripts.spike_ocr as module

        source = inspect.getsource(module.main)
        assert '"--dpi"' in source
        # The default must be resolved from the adapter, not written as a literal.
        assert "default=None" in source
        assert "_production_dpi()" in source


class TestInterface:
    def test_the_abstract_provider_refuses_to_recognise(self) -> None:
        with pytest.raises(NotImplementedError):
            OCRProvider().recognize(FakeImage(10, 10), A4)

    def test_the_result_defaults_are_usable(self) -> None:
        result = OCRResult()
        assert result.lines == []
        assert result.text == ""
        assert result.issues == []

    def test_engine_availability_is_reported_without_importing_it(self) -> None:
        # Callers use this to degrade honestly rather than reporting an empty
        # page, so it must return a value rather than raise.
        found = available_engine()
        assert found is None or (isinstance(found, tuple) and len(found) == 2)
