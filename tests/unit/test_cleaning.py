"""Tests for the text cleaning pipeline (spec §5.2, §7).

Cleaning is where silent corruption happens: hyphenation joining, line joining
and placeholder protection all rewrite text, and a mistake is invisible until a
translated sentence contains a spurious space or a lost formula.  These tests
pin the behaviour of each stage and of their order.
"""

from __future__ import annotations

import pytest

from document_core.cleaning import (
    check_placeholders,
    clean_text,
    count_words,
    dehyphenate,
    drop_soft_hyphens,
    garbage_ratio,
    join_lines,
    looks_like_garbage,
    normalise_unicode,
    protect_spans,
    restore_spans,
    split_sentences,
    strip_control_chars,
)


class TestStripControlChars:
    def test_removes_null_and_control_characters(self) -> None:
        assert strip_control_chars("a\x00b\x01c") == "abc"

    def test_keeps_newline_and_tab(self) -> None:
        assert strip_control_chars("a\n\tb") == "a\n\tb"

    def test_keeps_ordinary_text(self) -> None:
        assert strip_control_chars("Hello, world!") == "Hello, world!"


class TestNormaliseUnicode:
    def test_nfkc_folds_fullwidth_latin(self) -> None:
        assert normalise_unicode("\uff28\uff45\uff4c\uff4c\uff4f") == "Hello"

    def test_soft_hyphen_becomes_a_visible_hyphen(self) -> None:
        # U+00AD is the module's marker for "a hyphen was at a line break"; for
        # display it must become an ordinary hyphen, because NFKC alone would map
        # it to a hyphen anyway and leaving it invisible would silently swallow a
        # character the author wrote.
        assert normalise_unicode("ex\u00adample") == "ex-ample"

    def test_ligature_is_expanded(self) -> None:
        assert normalise_unicode("\ufb01nal") == "final"

    def test_collapses_nbsp_to_space(self) -> None:
        assert normalise_unicode("a\u00a0b") == "a b"

    def test_surrounding_text_is_preserved(self) -> None:
        # The other dash forms are deliberately not folded: folding them would
        # make a numeric range compare equal to a hyphenated word, and the
        # protected-token machinery matches ranges by their original characters.
        for dash in ("\u2013", "\u2014", "\u2212"):
            assert normalise_unicode(f"10{dash}15") == f"10{dash}15"


class TestDropSoftHyphens:
    def test_marker_becomes_a_hyphen(self) -> None:
        # Named for the invisible character it removes, not for the hyphen: the
        # visible hyphen is what the author typed.
        assert drop_soft_hyphens("a\u00adb\u00adc") == "a-b-c"

    def test_text_without_the_marker_is_unchanged(self) -> None:
        assert drop_soft_hyphens("plain-text") == "plain-text"


class TestDehyphenate:
    def test_joins_a_word_broken_across_lines(self) -> None:
        assert dehyphenate("experi-\nmental") == "experimental"

    def test_joins_a_compound_broken_across_lines(self) -> None:
        # A genuine compound keeps a soft-hyphen *marker* so that join_lines can
        # fuse it without a space; it is not fused here, because dehyphenate
        # cannot know whether "state-of-the-art" or "state-of-the-art" was meant
        # and a wrong fusion corrupts a word silently.
        assert dehyphenate("state-of-\nthe-art") == "state-of\u00ad\nthe-art"

    def test_does_not_join_a_numeric_range(self) -> None:
        # "10-\n15" is a range, not a broken word.  It keeps its hyphen and its
        # line break here; the cleaning pipeline turns the break into a space.
        assert dehyphenate("10-\n15") == "10-\n15"

    def test_keeps_hyphen_when_next_line_starts_a_new_word(self) -> None:
        # "well-\nknown" could be either; the guard list decides, and the decided
        # behaviour is pinned here so a change is deliberate.  The soft hyphen is
        # the evidence that a hyphen was written.
        assert dehyphenate("well-\nknown") == "well\u00ad\nknown"

    def test_preserved_break_keeps_the_following_text(self) -> None:
        # A bug where a preserved break dropped the remainder of the text.
        result = dehyphenate("The state-of-\nthe-art method works")
        assert "method works" in result
        assert "the-art" in result

    def test_multiple_breaks_in_one_passage(self) -> None:
        text = "random-\nized and experi-\nmental results"
        result = dehyphenate(text)
        assert "randomized" in result
        assert "experimental" in result

    def test_no_break_leaves_text_unchanged(self) -> None:
        assert dehyphenate("plain text") == "plain text"

    def test_trailing_hyphen_at_end_of_text_is_kept(self) -> None:
        assert dehyphenate("a well-") == "a well-"


class TestJoinLines:
    def test_joins_lines_with_a_space(self) -> None:
        assert join_lines("hello\nworld") == "hello world"

    def test_does_not_insert_a_leading_space(self) -> None:
        assert join_lines("\nworld") == "world"

    def test_uses_the_soft_hyphen_marker_to_fuse_without_a_space(self) -> None:
        # The marker is how dehyphenate communicates "fuse these".  join_lines
        # fuses without inserting a space; it deliberately leaves the marker in
        # place, because the marker is also the record that a hyphen was written
        # and a later pipeline stage turns it back into a visible hyphen.
        assert join_lines("experi\u00ad\nmental") == "experi\u00admental"

    def test_paragraph_breaks_are_preserved(self) -> None:
        # A blank line separates paragraphs and must survive joining.
        result = join_lines("one\n\ntwo")
        assert "one" in result and "two" in result
        assert "\n\n" in result

    def test_fused_marker_becomes_a_visible_hyphen_in_the_pipeline(self) -> None:
        # The end-to-end guarantee that matters: a compound wrapped across lines
        # reads with a hyphen and without a spurious space, and no invisible
        # character survives into the stored text.
        result = clean_text("It is a state-of-\nthe-art method.")
        assert "state-of-the-art" in result
        assert "\u00ad" not in result
        assert "state-of- the-art" not in result


class TestCleanTextPipeline:
    def test_end_to_end_on_a_realistic_paragraph(self) -> None:
        raw = "We present a repro-\nducible pipeline.\n\nIt handles lay-\nout analysis."
        result = clean_text(raw)
        # A hyphen at a line break inside a word is fused.
        assert "reproducible" in result
        # A paragraph break survives, so the two paragraphs stay distinct.
        assert "\n\n" in result
        assert "pipeline." in result

    def test_collapses_whitespace_runs(self) -> None:
        # Whitespace within a paragraph collapses to single spaces, while a blank
        # line remains a paragraph separator.  Collapsing the separator too would
        # silently merge two paragraphs into one.
        assert clean_text("a    b\n\n\n   c") == "a b\n\nc"

    def test_output_contains_no_soft_hyphen(self) -> None:
        result = clean_text("soft\u00adhyphen and a bro-\nken word")
        assert "\u00ad" not in result
        # The hyphen the author wrote is still visible.
        assert "soft-hyphen" in result
        assert "broken" in result

    def test_output_contains_no_control_characters(self) -> None:
        assert "\x00" not in clean_text("bad\x00text")

    def test_numeric_range_survives_cleaning(self) -> None:
        # The range must not be fused into "1015"; the hyphen is meaningful.
        result = clean_text("the range 10-\n15 was tested")
        assert "1015" not in result
        assert "10-" in result

    def test_empty_input(self) -> None:
        assert clean_text("") == ""

    def test_whitespace_only_input(self) -> None:
        assert clean_text("   \n\t  ") == ""

    def test_cjk_text_is_preserved(self) -> None:
        text = "\u4e2d\u6587\u6458\u8981"
        assert clean_text(text) == text

    def test_mixed_script_paragraph(self) -> None:
        result = clean_text("\u4e2d\u6587\u6458\u8981 abstract text here")
        assert "\u4e2d\u6587\u6458\u8981" in result


class TestPlaceholderProtection:
    def test_protect_and_restore_round_trip(self) -> None:
        text = "See Eq. (1) and Table 2 for details."
        protected, spans = protect_spans(text)
        assert "Eq. (1)" not in protected
        restored = restore_spans(protected, spans)
        assert restored == text

    def test_dollar_math_is_protected(self) -> None:
        text = "Let $x_i$ denote the vector."
        protected, spans = protect_spans(text)
        assert "$x_i$" not in protected
        assert restore_spans(protected, spans) == text

    def test_citation_is_protected(self) -> None:
        text = "Prior work [12, 13] showed this."
        protected, spans = protect_spans(text)
        assert "[12, 13]" not in protected
        assert restore_spans(protected, spans) == text

    def test_doi_is_protected(self) -> None:
        text = "Available at doi:10.1234/example.5678 today."
        protected, spans = protect_spans(text)
        assert restore_spans(protected, spans) == text

    def test_url_is_protected(self) -> None:
        text = "See https://example.org/path?q=1 for details."
        protected, spans = protect_spans(text)
        assert restore_spans(protected, spans) == text

    def test_percentage_is_protected(self) -> None:
        text = "Accuracy rose to 96.4% overall."
        protected, spans = protect_spans(text)
        assert "96.4%" not in protected
        assert restore_spans(protected, spans) == text

    def test_placeholder_integrity_check_passes_for_round_trip(self) -> None:
        text = "Eq. (1) and 96.4% and [12] and $y = mx + c$."
        protected, spans = protect_spans(text)
        report = check_placeholders(protected, spans)
        # The report is a dict rather than a bare boolean so a failure says
        # *which* token was lost, which is what a reviewer needs.
        assert report["ok"] is True
        assert report["missing"] == []
        assert report["duplicated"] == []
        assert report["extra"] == []
        assert restore_spans(protected, spans) == text

    def test_placeholder_integrity_check_detects_a_lost_placeholder(self) -> None:
        text = "Eq. (1) and 96.4%."
        protected, spans = protect_spans(text)
        # Truncating the string removes one of the two placeholders.
        broken = protected[: len(protected) // 2]
        report = check_placeholders(broken, spans)
        assert report["ok"] is False
        assert report["missing"], "a lost placeholder must be named, not just counted"

    def test_text_without_protectable_content_is_unchanged(self) -> None:
        text = "plain words only"
        protected, spans = protect_spans(text)
        assert protected == text
        assert spans == []


class TestCountWords:
    def test_counts_latin_words(self) -> None:
        assert count_words("one two three") == 3

    def test_counts_cjk_characters_individually(self) -> None:
        # A CJK "word" is not whitespace delimited, so counting tokens would
        # report 1 for a whole sentence.
        assert count_words("\u4e2d\u6587\u6458\u8981") >= 4

    def test_counts_mixed_script(self) -> None:
        assert count_words("abstract \u4e2d\u6587") >= 3

    def test_empty_text_is_zero(self) -> None:
        assert count_words("") == 0

    def test_punctuation_only_is_zero(self) -> None:
        assert count_words("... --- ...") == 0


class TestSplitSentences:
    def test_splits_on_full_stops(self) -> None:
        assert split_sentences("One. Two. Three.") == ["One.", "Two.", "Three."]

    def test_does_not_split_an_abbreviation(self) -> None:
        # "et al." and "Fig." are the common cases in this domain.
        result = split_sentences("Smith et al. reported this. It was confirmed.")
        assert len(result) == 2

    def test_does_not_split_a_decimal_number(self) -> None:
        result = split_sentences("Accuracy was 96.4% overall. It improved.")
        assert len(result) == 2

    def test_handles_cjk_terminator(self) -> None:
        result = split_sentences("\u8fd9\u662f\u7b2c\u4e00\u53e5\u3002\u8fd9\u662f\u7b2c\u4e8c\u53e5\u3002")
        assert len(result) == 2

    def test_empty_text_gives_no_sentences(self) -> None:
        assert split_sentences("") == []

    def test_text_without_terminator_is_one_sentence(self) -> None:
        assert split_sentences("no terminator here") == ["no terminator here"]


class TestGarbageDetection:
    def test_clean_text_is_not_garbage(self) -> None:
        assert looks_like_garbage("This is a normal sentence about research.") is False

    def test_replacement_characters_are_garbage(self) -> None:
        assert looks_like_garbage("\ufffd\ufffd\ufffd\ufffd\ufffd") is True

    def test_symbol_soup_is_garbage(self) -> None:
        assert looks_like_garbage("\u25a1\u25a1\u25a1 \u2591\u2592\u2593 " * 5) is True

    def test_garbage_ratio_of_clean_text_is_low(self) -> None:
        assert garbage_ratio("Clean text with normal punctuation.") < 0.05

    def test_garbage_ratio_of_garbage_is_high(self) -> None:
        assert garbage_ratio("\ufffd" * 40) > 0.5

    def test_empty_text_ratio(self) -> None:
        assert garbage_ratio("") == 0.0

    def test_text_layer_with_few_real_words_is_detected(self) -> None:
        # A page of isolated symbols with no words is unusable even though it has
        # plenty of characters.
        assert looks_like_garbage("| ||| || ||| |||| | ||||| " * 4) is True
