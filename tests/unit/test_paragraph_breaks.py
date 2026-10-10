"""Paragraph breaks: the two conditions that were both wrong.

`_same_column_flow` had an inverted lower bound and a useless upper bound.  Measured on the
corpus, the left column of `pdf_b_two_column` page 0 produced 33 breaks across 47 lines,
every one of them at a gap of exactly -0.94 pt, while the upper bound of 15.5 pt was larger
than the median real paragraph gap of 6.9 pt and so never fired.  The numbers here are the
measured ones converted to normalised page units (page height 842, so 1 pt = 0.001188).

The corpus records a constant 12.40 pt of leading and a 10.00 pt line box, giving a
within-paragraph gap of +2.40 pt.  PyMuPDF reports the same lines with a 13.34 pt box
(ascender to descender), which makes the same gap -0.94 pt.  Both spaces are exercised below,
because a threshold that only works in one of them is not a fix.
"""

from __future__ import annotations

import pytest

from document_core.coords import BBox
from document_core.layout import LayoutParams, TextLine, _same_column_flow, _vertical_gap


def point(value: float) -> float:
    """Convert points to normalised page units."""
    return value / 842.0


LINE_HEIGHT = point(13.34)
TYPICAL = LINE_HEIGHT


def line(y0_pt: float, x0_pt: float = 80.0, x1_pt: float = 300.0, text: str = "text") -> TextLine:
    return TextLine(
        text=text,
        bbox=BBox(point(x0_pt), point(y0_pt), point(x1_pt), point(y0_pt + 13.34)),
    )


def flows(above: TextLine, below: TextLine, params: LayoutParams | None = None) -> bool:
    return _same_column_flow(above, below, 1.0, params or LayoutParams(), TYPICAL)


class TestTheLowerBoundIsNotZero:
    """The guard that broke at almost every line."""

    def test_consecutive_lines_of_one_paragraph_overlap_slightly(self) -> None:
        # 12.40 pt of leading against a 13.34 pt box: the next line starts 0.94 pt above the
        # previous line's foot.  This is what normal text looks like, and the old test called
        # it a paragraph break.
        above = line(100.0)
        below = line(112.40)
        assert _vertical_gap(above, below) == pytest.approx(-0.00112, abs=5e-5)
        assert flows(above, below) is True

    def test_the_measured_column_jump_is_rejected(self) -> None:
        # The reading order leaving the bottom of the left column for the top of the right
        # one.  This is the case the guard exists for, and it is three orders of magnitude
        # away from the overlap above.
        above = line(760.0)
        below = line(70.0)
        assert _vertical_gap(above, below) < -0.5
        assert flows(above, below) is False

    def test_a_modest_backwards_step_still_breaks(self) -> None:
        # Backwards by more than one line, yet nowhere near a column jump.  The reading order
        # is wrong here whichever way the page is cut, so it must not be merged.
        above = line(100.0)
        below = line(80.0)
        assert flows(above, below) is False

    def test_overlap_tolerance_scales_with_the_type_size(self) -> None:
        params = LayoutParams(line_overlap_tolerance=0.15)
        # A 20 pt body gives a 0.15 x 20 = 3 pt tolerance, so a 2 pt overlap is still a merge.
        above = TextLine(text="a", bbox=BBox(0.1, point(100.0), 0.4, point(120.0)))
        below = TextLine(text="b", bbox=BBox(0.1, point(118.0), 0.4, point(138.0)))
        assert _same_column_flow(above, below, 1.0, params, point(20.0)) is True


class TestTheUpperBoundFiresOnRealParagraphGaps:
    """The 15.5 pt bound that could never fire."""

    def test_the_measured_within_paragraph_gap_merges(self) -> None:
        # In the corpus's own coordinates: 12.40 pt step, 10.00 pt box, +2.40 pt of space.
        above = TextLine(text="a", bbox=BBox(0.1, point(100.0), 0.4, point(110.0)))
        below = TextLine(text="b", bbox=BBox(0.1, point(112.40), 0.4, point(122.40)))
        assert _vertical_gap(above, below) == pytest.approx(point(2.40))
        assert flows(above, below) is True

    def test_the_measured_between_paragraph_gap_breaks(self) -> None:
        # 16.90 pt step, the corpus median between blocks, against the same 10 pt box: 6.90 pt
        # of space.  The old threshold of 15.5 pt merged this, which is why no real paragraph
        # boundary was ever detected by gap.
        above = TextLine(text="a", bbox=BBox(0.1, point(100.0), 0.4, point(110.0)))
        below = TextLine(text="b", bbox=BBox(0.1, point(116.90), 0.4, point(126.90)))
        assert _vertical_gap(above, below) == pytest.approx(point(6.90))
        assert flows(above, below) is False

    def test_the_smallest_measured_paragraph_gap_breaks(self) -> None:
        # The 5th percentile of between-block gaps is 4.16 pt, only 1.76 pt above the largest
        # within-paragraph gap.  That is the valley the threshold has to sit in, so this is
        # the tightest case that must still be caught.
        above = TextLine(text="a", bbox=BBox(0.1, point(100.0), 0.4, point(110.0)))
        below = TextLine(text="b", bbox=BBox(0.1, point(114.16), 0.4, point(124.16)))
        assert flows(above, below) is False

    def test_the_default_factor_sits_inside_the_measured_valley(self) -> None:
        params = LayoutParams()
        # Measured valley in points: within-paragraph 2.40, between-paragraph 5th percentile
        # 4.16.  The threshold must be strictly between them, not merely somewhere below one.
        threshold_pt = params.paragraph_gap_factor * 13.34
        assert 2.40 < threshold_pt < 4.16

    def test_a_larger_factor_swallows_real_paragraph_boundaries(self) -> None:
        # The old default was 1.55, i.e. 20.7 pt against this type size, which is larger than
        # the 6.9 pt median paragraph gap — so it swallowed every boundary in the corpus.  This
        # pins the failure mode: the smallest measured boundary, 4.16 pt, survives 0.25 but not
        # 0.40.
        above = TextLine(text="a", bbox=BBox(0.1, point(100.0), 0.4, point(110.0)))
        below = TextLine(text="b", bbox=BBox(0.1, point(114.16), 0.4, point(124.16)))
        assert flows(above, below, LayoutParams(paragraph_gap_factor=0.25)) is False
        assert flows(above, below, LayoutParams(paragraph_gap_factor=0.40)) is True

    def test_the_old_default_would_never_break_a_paragraph(self) -> None:
        # The corpus median between-block gap is 6.9 pt.  At the old factor it was merged,
        # which is why no paragraph boundary was ever found by gap.
        above = TextLine(text="a", bbox=BBox(0.1, point(100.0), 0.4, point(110.0)))
        below = TextLine(text="b", bbox=BBox(0.1, point(116.90), 0.4, point(126.90)))
        assert flows(above, below, LayoutParams(paragraph_gap_factor=1.55)) is True


class TestATypeSizeChangeStartsANewBlock:
    """How a section heading is separated from the paragraph under it.

    The gap tests cannot do it: a heading's box is taller than a body line, so the gap to the
    next line comes out negative and the overlap tolerance accepts it.  Measured in
    `scripts/calibrate_heading_split.py`: within a block the size ratio is a constant 1.000
    across 501 pairs, and across all 86 heading-to-next-block pairs it is at most 0.926.
    """

    def sized(self, y0_pt: float, size: float, text: str = "text") -> TextLine:
        return TextLine(
            text=text,
            bbox=BBox(point(80.0), point(y0_pt), point(300.0), point(y0_pt + size)),
            font_size=size,
        )

    def test_a_heading_then_body_text_breaks(self) -> None:
        # The corpus sizes: a 12.5 pt heading over 10 pt body, ratio 0.800.
        above = self.sized(100.0, 12.5, "1. Introduction")
        below = self.sized(112.40, 10.0, "Reading order and evidence tracking")
        assert flows(above, below) is False

    def test_the_tightest_measured_heading_pair_breaks(self) -> None:
        # 10.8 pt subheading over 10 pt body, ratio 0.926 — the narrowest case present.
        above = self.sized(100.0, 10.8, "2.1 Translation of scientific text")
        below = self.sized(112.40, 10.0, "Scientific translation differs")
        assert flows(above, below) is False

    def test_a_title_then_the_author_line_breaks(self) -> None:
        above = self.sized(100.0, 17.0, "Reading Order Recovery")
        below = self.sized(112.40, 10.5, "D. Researcher and E. Scholar")
        assert flows(above, below) is False

    def test_a_heading_absorbed_from_above_is_also_split(self) -> None:
        # The same defect seen from the other side: body text then a larger heading.  The rule
        # is symmetric on purpose, so a heading is never absorbed into the paragraph before it.
        above = self.sized(100.0, 10.0, "previous paragraph")
        below = self.sized(112.40, 12.5, "2. Related Work")
        assert flows(above, below) is False

    def test_uniform_body_text_never_splits(self) -> None:
        above = self.sized(100.0, 10.0)
        below = self.sized(112.40, 10.0)
        assert flows(above, below) is True

    def test_a_rounding_difference_does_not_split(self) -> None:
        # Two per cent is inside the tolerance, so a font reported as 9.8 in one line and 10.0
        # in the next does not chop a paragraph in half.
        above = self.sized(100.0, 10.0)
        below = self.sized(112.40, 9.8)
        assert flows(above, below) is True

    def test_the_tolerance_is_smaller_than_the_narrowest_measured_change(self) -> None:
        params = LayoutParams()
        assert params.paragraph_size_tolerance < 1.0 - 0.926

    def test_no_font_information_falls_back_to_the_gap_tests(self) -> None:
        # Without a size the rule cannot speak, and staying silent is the only safe option:
        # guessing a size would split paragraphs that were never a heading.
        above = TextLine(text="a", bbox=BBox(0.1, point(100.0), 0.4, point(112.40)))
        below = TextLine(text="b", bbox=BBox(0.1, point(112.40), 0.4, point(124.80)))
        assert flows(above, below) is True


class TestTheOtherGuardsStillApply:
    def test_lines_in_different_columns_do_not_flow(self) -> None:
        above = line(100.0, 80.0, 300.0)
        below = line(112.40, 440.0, 660.0)
        assert flows(above, below) is False

    def test_a_large_first_line_indent_breaks(self) -> None:
        above = line(100.0, 80.0, 300.0)
        below = line(112.40, 140.0, 300.0)
        assert flows(above, below) is False

    def test_a_small_indent_does_not_break(self) -> None:
        above = line(100.0, 80.0, 300.0)
        below = line(112.40, 90.0, 300.0)
        assert flows(above, below) is True
