"""Tests for the OCR comparison metrics.

These metrics decide whether an acceptance item passes, so a defect in them is
worse than a defect in the recogniser: it can report success for a system that
does not work.  Both metric defects found in this project were of that kind, and
the tests that would have caught them are here.
"""

from __future__ import annotations

import pytest

from rr_m0.ocr_metrics import (
    align_lines,
    character_error_rate,
    edit_distance,
    line_similarity,
    multiset_character_error_rate,
    normalise,
    reading_order_score,
)


class TestNormalise:
    def test_whitespace_runs_collapse(self) -> None:
        # Line-wrapping position is not part of the text, so it must not count as
        # an error.
        assert normalise("a   b\n\tc") == "a b c"

    def test_empty_stays_empty(self) -> None:
        assert normalise("   \n ") == ""


class TestEditDistance:
    def test_identical_strings_are_zero(self) -> None:
        assert edit_distance("abc", "abc") == 0

    def test_a_substitution_costs_one(self) -> None:
        assert edit_distance("abc", "abd") == 1

    def test_empty_reference_is_the_hypothesis_length(self) -> None:
        assert edit_distance("", "abc") == 3

    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("kitten", "sitting", 3),
            ("flaw", "lawn", 2),
            ("", "", 0),
            ("a", "", 1),
        ],
    )
    def test_known_distances(self, a: str, b: str, expected: int) -> None:
        assert edit_distance(a, b) == expected

    def test_it_is_symmetric_for_these_cases(self) -> None:
        # Levenshtein is symmetric; a rolling-row implementation that iterated the
        # wrong way would not be.
        for a, b in (("kitten", "sitting"), ("abc", "xyz"), ("a", "abcdef")):
            assert edit_distance(a, b) == edit_distance(b, a)


class TestCharacterErrorRate:
    def test_an_exact_match_is_zero(self) -> None:
        assert character_error_rate("hello", "hello") == 0.0

    def test_it_is_relative_to_the_reference_length(self) -> None:
        assert character_error_rate("abcd", "abce") == pytest.approx(0.25)

    def test_a_missing_hypothesis_scores_one(self) -> None:
        # Nothing recognised for a page that has text is a total failure, not an
        # undefined result.
        assert character_error_rate("abc", "") == pytest.approx(1.0)

    def test_both_empty_is_zero(self) -> None:
        assert character_error_rate("", "") == 0.0


class TestMultisetCharacterErrorRate:
    def test_it_ignores_the_order_of_lines(self) -> None:
        # The defect this metric exists to fix: two-column ground truth is in
        # semantic order while a render is scanned in visual order, so a strict
        # comparison reported a large error for text that was character-perfect.
        reference = "alpha beta gamma"
        hypothesis = "gamma alpha beta"
        assert multiset_character_error_rate(reference, hypothesis) == 0.0
        assert character_error_rate(reference, hypothesis) > 0.0

    def test_a_missing_character_is_counted(self) -> None:
        assert multiset_character_error_rate("abcd", "abc") == pytest.approx(0.25)

    def test_an_extra_character_is_counted(self) -> None:
        assert multiset_character_error_rate("abc", "abcd") == pytest.approx(1 / 3)

    def test_a_substitution_counts_as_two_edits(self) -> None:
        # One insertion plus one deletion, which matches how a per-character
        # substitution is counted.
        assert multiset_character_error_rate("abcd", "abce") == pytest.approx(0.5)

    def test_it_is_order_insensitive_in_characters_too(self) -> None:
        assert multiset_character_error_rate("abc", "cba") == 0.0

    def test_nothing_recognised_scores_one(self) -> None:
        assert multiset_character_error_rate("abc", "") == pytest.approx(1.0)


class TestLineSimilarity:
    """The length guard that stopped a fragment matching a passage."""

    def test_identical_lines_match(self) -> None:
        assert line_similarity("the quick brown fox", "the quick brown fox") > 0.9

    def test_a_line_with_small_ocr_errors_still_matches(self) -> None:
        # Acceptance depends on this: a correctly recognised line must pair with
        # its reference even when a character or two is wrong.
        assert line_similarity(
            "the quick brown fox jumps", "the qulck brown fox jumps"
        ) > 0.9

    def test_a_word_inside_a_paragraph_does_not_match_it(self) -> None:
        # The defect: Dice similarity alone scored this 0.55 and passed the 0.5
        # pairing threshold, which made a flattened-paragraph reference look like a
        # measurable line-level comparison.
        assert line_similarity("a long paragraph of text", "paragraph") == 0.0

    def test_a_short_fragment_inside_a_long_line_does_not_match(self) -> None:
        assert line_similarity("experimental results confirm the hypothesis", "results") == 0.0

    def test_two_short_lines_of_similar_length_can_match(self) -> None:
        # The guard must not block legitimate short lines such as headings.
        assert line_similarity("2. Methods", "2 Methods") > 0.8

    def test_completely_different_lines_do_not_match(self) -> None:
        assert line_similarity("alpha beta", "gamma delta") == 0.0

    def test_an_empty_line_never_matches(self) -> None:
        assert line_similarity("", "anything") == 0.0
        assert line_similarity("anything", "") == 0.0


class TestAlignLines:
    def test_lines_are_paired_regardless_of_position(self) -> None:
        # Pairing by content, not by index, is what lets a two-column page match
        # even when its columns were reported in a different order.
        reference = ["first line", "second line"]
        hypothesis = ["second line", "first line"]
        matched, pairs = align_lines(reference, hypothesis)
        assert matched == 1.0
        assert all(tag == "match" for tag, _r, _h in pairs)

    def test_an_unmatched_reference_line_is_reported(self) -> None:
        matched, pairs = align_lines(["present", "absent"], ["present"])
        assert matched == 0.5
        assert any(tag == "reference_only" for tag, _r, _h in pairs)

    def test_an_extra_hypothesis_line_is_reported(self) -> None:
        _matched, pairs = align_lines(["present"], ["present", "spurious"])
        assert any(tag == "hypothesis_only" for tag, _r, _h in pairs)

    def test_one_reference_line_against_many_is_reported_as_a_line_mismatch(self) -> None:
        # The ground-truth shape that made reading order unmeasurable: a whole
        # paragraph flattened to one line against many recognised lines.
        matched, _pairs = align_lines(["a long paragraph of text"], ["a", "long", "paragraph"])
        assert matched == 0.0


class TestReadingOrderScore:
    def test_correct_order_scores_one(self) -> None:
        lines = ["alpha one", "beta two", "gamma three"]
        assert reading_order_score(lines, lines) == pytest.approx(1.0)

    def test_reversed_order_scores_zero(self) -> None:
        reference = ["alpha one", "beta two", "gamma three"]
        hypothesis = ["gamma three", "beta two", "alpha one"]
        assert reading_order_score(reference, hypothesis) == pytest.approx(0.0)

    def test_a_single_swap_is_penalised_proportionally(self) -> None:
        reference = ["alpha one", "beta two", "gamma three", "delta four"]
        hypothesis = ["alpha one", "gamma three", "beta two", "delta four"]
        score = reading_order_score(reference, hypothesis)
        assert score is not None
        assert 0.0 < score < 1.0

    def test_an_unmeasurable_comparison_returns_none_not_one(self) -> None:
        # The defect: one reference line against many recognised lines cannot be
        # scored, and returning 1.0 reported a perfect reading order while
        # measuring nothing.  None means "not measured" and a caller must not
        # treat it as success.
        reference = ["a long paragraph of text"]
        hypothesis = ["a", "long", "paragraph", "of", "text"]
        assert reading_order_score(reference, hypothesis) is None

    def test_no_lines_at_all_is_unmeasurable(self) -> None:
        assert reading_order_score([], ["anything"]) is None
        assert reading_order_score([], []) is None

    def test_exactly_one_matched_pair_is_unmeasurable(self) -> None:
        # One pair has no adjacent pair, so there is nothing to order.
        assert reading_order_score(["only line"], ["only line"]) is None

    def test_a_threshold_below_two_is_refused(self) -> None:
        # One matched pair has no adjacent pair, so the score is undefined.  Asking
        # for one anyway is a programming error, not a configuration choice, and an
        # earlier version divided by zero here.
        with pytest.raises(ValueError):
            reading_order_score(["only line"], ["only line"], min_pairs=1)

    def test_a_single_reference_line_cannot_start_a_long_line_match(self) -> None:
        # A long reference line must not be paired with one short recognised line,
        # because that would report a line match and then let the order score look
        # measurable on the strength of it.
        matched, _pairs = align_lines(
            ["a long paragraph of text"], ["text"]
        )
        assert matched == 0.0
