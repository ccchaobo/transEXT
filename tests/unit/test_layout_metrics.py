"""Tests for the layout comparison metrics.

The layout acceptance items are decided by these functions, so a defect in them is
worse than a defect in the layout code: it can report success for a system that does
not work, or report failure for one that does.

Both defects found here were of that second kind and both made a *working* layout look
broken:

* the matcher paired blocks with paragraphs on other pages, and
* even on the right page it paired purely on text, ignoring position.

Together they accounted for almost the whole measured shortfall (order 0.8571 before,
0.9858 after), so the "layout reads two-column pages in the wrong order" conclusion was
an artefact of the measurement.  The tests that pin the contract are here.
"""

from __future__ import annotations

import pytest

from rr_m0.layout_metrics import (
    BODY_TYPES,
    STRUCTURE_ONLY_TYPES,
    TruthBlock,
    adjacent_order_accuracy,
    body_coverage,
    match_blocks_to_paragraphs,
    normalise,
    region_accuracy,
)


def block(
    index: int,
    text: str,
    *,
    page: int = 0,
    order: int = 0,
    bbox: tuple[float, float, float, float] = (0.1, 0.1, 0.9, 0.2),
    block_type: str = "paragraph",
) -> TruthBlock:
    return TruthBlock(
        index=index,
        block_type=block_type,
        text=text,
        page_index=page,
        reading_order=order,
        bbox=bbox,
    )


class TestTruthBlockKey:
    def test_key_includes_the_page_so_a_split_block_stays_distinct(self) -> None:
        # A paragraph crossing a page break is represented once per page. The same
        # index on two pages must not collide, or the order metric silently keeps
        # only one of them.
        first = block(7, "a", page=0)
        second = block(7, "b", page=1)
        assert first.key != second.key

    def test_structure_only_types_are_known(self) -> None:
        assert "table" in STRUCTURE_ONLY_TYPES
        assert "figure" in STRUCTURE_ONLY_TYPES
        assert "paragraph" in BODY_TYPES
        assert not (BODY_TYPES & STRUCTURE_ONLY_TYPES)


class TestBodyCoverage:
    def test_identical_text_is_full_coverage(self) -> None:
        coverage, missing, length = body_coverage("the cat sat", "the cat sat")
        assert coverage == 1.0
        assert missing == 0
        # Length is the character count of the whitespace-folded string, spaces
        # included: only *runs* of whitespace are folded, not the single spaces.
        assert length == len("the cat sat") == 11

    def test_missing_characters_reduce_coverage(self) -> None:
        coverage, missing, _length = body_coverage("abcdefgh", "abcd")
        assert missing == 4
        assert coverage == pytest.approx(0.5)

    def test_empty_reference_is_not_an_error(self) -> None:
        # An empty reference has nothing to account for, so it cannot fail.
        assert body_coverage("", "anything") == (1.0, 0, 0)

    def test_repeated_characters_are_counted_not_just_present(self) -> None:
        # Multiset, not set: two 'a's in the reference need two in the output.
        _coverage, missing, _length = body_coverage("aa", "a")
        assert missing == 1


class TestNormalise:
    """`normalise` folds whitespace only — it is not a case or punctuation fold."""

    def test_whitespace_runs_collapse(self) -> None:
        assert normalise("a   b\n\tc") == "a b c"

    def test_case_is_preserved(self) -> None:
        # Case is content for a coverage measure, so it must not be folded away.
        assert normalise("A1 b2") == "A1 b2"

    def test_punctuation_is_preserved(self) -> None:
        # Straight and curly quotes are distinct characters and stay distinct.
        assert normalise("\u201cquoted\u201d") != normalise('"quoted"')

    def test_smart_quotes_count_as_content_when_missing(self) -> None:
        # This is why the residual shortfall in the measurement is quotation marks:
        # they are content to this measure, and the extractor emits them.
        coverage, missing, _ = body_coverage("\u201ccat\u201d", "cat")
        assert missing == 2
        assert coverage < 1.0


class TestMatchRequiresSamePage:
    def test_a_block_is_never_paired_with_another_pages_paragraph(self) -> None:
        # The exact defect: a page-0 sentence and a page-1 sentence that share
        # characters must not be paired, because no paragraph on page 1 can be the
        # extracted form of text drawn on page 0.
        blocks = [block(0, "shared words here", page=0, bbox=(0.1, 0.1, 0.9, 0.2))]
        paragraphs = [
            ("shared words here", (1,), (0.1, 0.1, 0.9, 0.2)),
        ]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        assert report.matches == []
        assert [b.index for b in report.unmatched_blocks] == [0]

    def test_a_paragraph_spanning_a_page_break_may_still_match_both_pages(self) -> None:
        # A paragraph whose text was drawn on pages 0 and 1 legitimately answers for
        # the block's continuation entry on page 1.  Rejecting that would lose a real
        # match, so the test is containment in the paragraph's page set.
        text = "a paragraph that runs across the page break"
        blocks = [
            block(0, text, page=0, order=0, bbox=(0.1, 0.8, 0.9, 0.9)),
            block(0, text, page=1, order=1, bbox=(0.1, 0.1, 0.9, 0.2)),
        ]
        paragraphs = [(text, (0, 1), (0.1, 0.8, 0.9, 0.9))]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        # One paragraph may answer for only one block, so exactly one pairs.
        assert len(report.matches) == 1
        assert report.matches[0].block.page_index in (0, 1)


class TestMatchRequiresVerticalPlausibility:
    def test_a_distant_paragraph_does_not_claim_a_block(self) -> None:
        # Same text, same page, but at the far end of the page.  Before this guard the
        # greedy pass took it on similarity alone, which pushed the correct paragraph
        # out and showed up as an ordering inversion the layout had not committed.
        blocks = [block(0, "identical text", page=0, bbox=(0.1, 0.2, 0.9, 0.25))]
        paragraphs = [("identical text", (0,), (0.1, 0.8, 0.9, 0.9))]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        assert report.matches == []

    def test_the_nearby_paragraph_wins_over_the_distant_identical_one(self) -> None:
        blocks = [block(0, "same words entirely", page=0, bbox=(0.1, 0.30, 0.9, 0.36))]
        paragraphs = [
            ("same words entirely", (0,), (0.1, 0.80, 0.9, 0.90)),  # far away
            ("same words entirely", (0,), (0.1, 0.29, 0.9, 0.37)),  # overlapping
        ]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        assert len(report.matches) == 1
        assert report.matches[0].paragraph_index == 1

    def test_a_paragraph_that_absorbed_a_heading_is_still_allowed(self) -> None:
        # A heading block can legitimately be merged into the taller paragraph that
        # follows it.  The overlap test is a share of the *block's* height, so a
        # paragraph taller than the block is not rejected.
        #
        # The paragraph is only a little longer than the heading: if it were much
        # longer the length guard would reject it, which is a different and correct
        # behaviour (a heading must not claim a whole paragraph).
        blocks = [
            block(0, "3.1 Data and annotation", page=0, bbox=(0.1, 0.20, 0.5, 0.23))
        ]
        paragraphs = [
            (
                "3.1 Data and annotation Our pilot study",
                (0,),
                (0.1, 0.20, 0.9, 0.30),
            )
        ]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        assert len(report.matches) == 1
        # The pairing is allowed because the block sits inside the paragraph's extent,
        # which is what a merge looks like.
        assert report.matches[0].block.page_index == 0


class TestAdjacentOrderAccuracy:
    def test_perfect_order_scores_one(self) -> None:
        blocks = [
            block(0, "first", order=0, bbox=(0.1, 0.1, 0.9, 0.2)),
            block(1, "second", order=1, bbox=(0.1, 0.3, 0.9, 0.4)),
        ]
        paragraphs = [
            ("first", (0,), (0.1, 0.1, 0.9, 0.2)),
            ("second", (0,), (0.1, 0.3, 0.9, 0.4)),
        ]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        accuracy, correct, total = adjacent_order_accuracy(
            [m.block for m in report.matches], report.matches
        )
        assert accuracy == 1.0
        assert (correct, total) == (1, 1)

    def test_swapped_order_scores_zero(self) -> None:
        blocks = [
            block(0, "first", order=0, bbox=(0.1, 0.1, 0.9, 0.2)),
            block(1, "second", order=1, bbox=(0.1, 0.3, 0.9, 0.4)),
        ]
        # The pipeline emitted them the other way round.
        paragraphs = [
            ("second", (0,), (0.1, 0.3, 0.9, 0.4)),
            ("first", (0,), (0.1, 0.1, 0.9, 0.2)),
        ]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        accuracy, correct, total = adjacent_order_accuracy(
            [m.block for m in report.matches], report.matches
        )
        assert accuracy == 0.0
        assert (correct, total) == (0, 1)

    def test_unmeasurable_returns_none_not_a_vacuous_pass(self) -> None:
        # Fewer than two comparable pairs cannot show an ordering error, and reporting
        # 1.0 there would be a pass earned by having no evidence.
        blocks = [block(0, "only one", order=0, bbox=(0.1, 0.1, 0.9, 0.2))]
        paragraphs = [("only one", (0,), (0.1, 0.1, 0.9, 0.2))]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        accuracy, correct, total = adjacent_order_accuracy(
            [m.block for m in report.matches], report.matches
        )
        assert accuracy is None
        assert (correct, total) == (0, 0)


class TestRegionAccuracy:
    def test_overlapping_paragraph_on_the_right_page_counts(self) -> None:
        blocks = [block(0, "text here", page=0, bbox=(0.1, 0.1, 0.9, 0.2))]
        paragraphs = [("text here", (0,), (0.1, 0.1, 0.9, 0.2))]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        region = region_accuracy(report.matches)
        assert region["considered"] == 1
        assert region["correct"] == 1
        assert region["accuracy"] == 1.0

    def test_a_displaced_paragraph_is_not_region_correct(self) -> None:
        # Overlaps vertically enough to be paired, but shifted down the page, so the
        # highlight would land in the wrong place.  The overlap is deliberately above
        # the pairing threshold so this tests the *region* measure, not the pairing.
        blocks = [block(0, "text here", page=0, bbox=(0.1, 0.10, 0.9, 0.30))]
        paragraphs = [("text here", (0,), (0.1, 0.22, 0.9, 0.60))]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        assert len(report.matches) == 1
        region = region_accuracy(report.matches)
        assert region["considered"] == 1
        assert region["correct"] == 0
        assert region["accuracy"] == 0.0

    def test_a_wrong_page_paragraph_is_reported_separately(self) -> None:
        # Not reachable through the matcher any more, which refuses cross-page pairs,
        # so the reporting is exercised directly: a wrong-page match must be counted
        # in wrong_page rather than as a region failure.
        from rr_m0.layout_metrics import MatchReport, MatchResult

        a_block = block(3, "some text", page=0, bbox=(0.1, 0.1, 0.9, 0.2))
        match = MatchResult(
            block=a_block,
            paragraph_index=0,
            paragraph_text="some text",
            similarity=0.9,
            page_index=1,
            bbox=(0.1, 0.1, 0.9, 0.2),
        )
        report = MatchReport()
        report.matches.append(match)
        region = region_accuracy(report.matches)
        assert region["considered"] == 1
        assert region["correct"] == 0
        assert region["wrong_page"] == [((3, 0), 0, 1)]
