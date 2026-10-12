"""Tests for semantic block typing.

The types decide what is translated, so the tests are organised around that: the
false-positive direction (calling prose a citation, which removes it from translation) is
the expensive one and gets more tests than the cosmetic confusions.

The strings are the real ones from the corpus, because the heuristics were tuned against
them and a made-up example would not catch the regressions these guard.
"""

from __future__ import annotations

import pytest

from document_core.blocks import (
    body_font_size,
    classify_paragraph,
    has_author_shape,
    in_references_section,
    is_formula_like,
    is_reference_like,
    is_reference_section_heading,
    looks_like_abstract_start,
    looks_like_author_line,
)
from document_core.coords import BBox
from document_core.layout import Paragraph
from document_core.models import BlockType

# Real corpus strings.
CORPUS_TITLE = "A Reproducible Pipeline for Extraction"
CORPUS_AUTHOR = "A. Author, B. Author, and C. Author"
CORPUS_ABSTRACT = (
    "Abstract—We describe a reproducible pipeline for extracting and evaluating the "
    "reading order of research articles."
)
CORPUS_HEADING = "1. Introduction"
CORPUS_SUBHEADING = "2.1 Translation of scientific text"
CORPUS_PARAGRAPH = (
    "Reading order and evidence tracking are prerequisites for reliable automatic "
    "processing of research articles."
)
CORPUS_REFERENCE = (
    "[5] H. Statistician and I. Reviewer, “Confidence intervals for rare-event recall,” "
    "Synthetic Statistics Letters, vol. 8, pp. 55–67, 2018. doi:10.1000/ssl.2018.55"
)
CORPUS_REFERENCE_FIRST = (
    "[1] A. Author and B. Author, “A representative baseline for sequence labeling,” "
    "Journal of Synthetic Methods, vol. 12, no. 3, pp. 210–225, 2019. doi:10.1000/jsm.2019.210"
)
CORPUS_FOOTNOTE = "2 Correspondence: author@example.invalid"
CORPUS_CAPTION = (
    "Figure 1. Error rate as a function of page rotation. Markers show measured values; "
    "the curve is a guide to the eye only, and no functional form is claimed."
)
CORPUS_SINGLE_AUTHOR = "O. Author"


def make_para(
    text: str,
    *,
    y0: float = 0.2,
    font_size: float | None = 10.0,
    kind: str = "paragraph",
    level: int | None = None,
    page: int = 0,
    section_path: list[str] | None = None,
) -> Paragraph:
    return Paragraph(
        text=text,
        raw_text=text,
        lines=[],
        page_indices=[page],
        bbox=BBox(0.1, y0, 0.9, y0 + 0.04),
        font_size=font_size,
        bold=False,
        kind=kind,
        level=level,
        section_path=section_path or [],
    )


def classify(para: Paragraph, *, position: int = 10, body_size: float | None = 10.0):
    return classify_paragraph(
        para,
        position=position,
        page_index=para.page_indices[0],
        body_size=body_size,
    ).type


class TestProseIsNotACitation:
    """The expensive direction: mistyping prose as a citation removes it from translation.

    A looser rule was tried and measured: it typed 28 real paragraphs as bibliography
    entries, because a research paragraph cites several papers and mentions a year.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "In this paper we study how a structure-aware pipeline recovers reading order, "
            "and we compare against the approach of Smith et al. [1] from 2018.",
            "As shown previously [1,2], the gap between columns is not always empty. "
            "We therefore measure it in two ways.",
            "Our contributions are threefold. First, we describe a reproducible pipeline. "
            "Second, we report coverage [3]. Third, we release the corpus.",
        ],
    )
    def test_a_paragraph_that_cites_things_is_not_a_reference(self, text: str) -> None:
        assert is_reference_like(text) is False
        assert classify(make_para(text)) == BlockType.PARAGRAPH

    def test_a_year_alone_does_not_make_a_citation(self) -> None:
        assert is_reference_like("The paper was published in 2019 and revised in 2020.") is False

    def test_two_citation_markers_without_a_lead_or_title_are_not_enough(self) -> None:
        text = "See the earlier work on vol. 3 and the discussion of pp. 12 for details."
        assert is_reference_like(text) is False

    def test_a_long_body_block_is_not_a_reference(self) -> None:
        # A bare "long text is a bibliography" rule typed the merged author+abstract block
        # of pdf_b_two_column as a reference, which keeps a whole abstract out of
        # translation.  Any over-merged body block is long.
        text = (
            "D. Researcher and E. Scholar Abstract—Two-column layouts remain the dominant "
            "format in the physical sciences. We study reading-order recovery on such pages "
            "and report an accuracy for adjacent block pairs, with column splitting "
            "accounting for most residual errors. A band-segmentation step that restarts "
            "column flow after full-width objects raises accuracy substantially. We use "
            "placeholders for protected tokens and verify their completeness mechanically. "
        ) * 3
        assert len(text) > 1200
        assert is_reference_like(text) is False

    def test_a_long_run_of_dense_entries_is_a_reference(self) -> None:
        entry = (
            "[{n}] A. Author and B. Author, “A study of things,” Journal of Synthetic "
            "Methods, vol. 12, no. 3, pp. 210–225, 2019. doi:10.1000/jsm.2019.210 "
        )
        text = " ".join(entry.format(n=i) for i in range(1, 12))
        assert len(text) > 1200
        assert is_reference_like(text) is True


class TestCitationsAreCitations:
    def test_a_corpus_reference_is_recognised(self) -> None:
        assert is_reference_like(CORPUS_REFERENCE) is True
        assert classify(make_para(CORPUS_REFERENCE, section_path=["References"])) == BlockType.REFERENCE

    def test_a_reference_is_not_translatable(self) -> None:
        # The whole point of the type: DOIs, author names and journal titles must survive.
        assert BlockType.REFERENCE.is_translatable is False

    def test_a_reference_labelled_a_heading_by_layout_stays_a_reference(self) -> None:
        # A short bibliography entry is set large enough to look like a heading, and the
        # layout stage labels it as one.  Accepting that label first typed 5 corpus
        # references as headings, which puts their DOIs through the translator.
        para = make_para(CORPUS_REFERENCE, kind="heading", font_size=12.0, section_path=["References"])
        assert classify(para) == BlockType.REFERENCE

    def test_a_first_reference_with_a_compact_lead_is_recognised(self) -> None:
        assert classify(make_para(CORPUS_REFERENCE_FIRST, section_path=["References"])) == BlockType.REFERENCE

    def test_anything_under_a_references_heading_is_a_reference(self) -> None:
        # Entries whose continuation carries the year have no lead marker of their own, so
        # the section is what identifies them.
        para = make_para("2018. doi:10.1000/ssl.2018.55", section_path=["References"])
        assert classify(para) == BlockType.REFERENCE

    def test_the_references_heading_itself_is_a_heading(self) -> None:
        # Otherwise the section check swallows the heading that opens it.
        assert is_reference_section_heading("References") is True
        assert is_reference_section_heading("7. References") is True
        assert is_reference_section_heading("Bibliography") is True
        assert is_reference_section_heading("Introduction") is False
        assert is_reference_section_heading("Related work") is False

    def test_the_references_heading_is_not_turned_into_an_entry(self) -> None:
        para = make_para("References", kind="heading", section_path=["References"])
        assert classify(para) == BlockType.HEADING

    def test_a_chinese_references_heading_is_recognised(self) -> None:
        assert in_references_section(["参考文献"]) is True

    @pytest.mark.parametrize("text", [CORPUS_REFERENCE, CORPUS_REFERENCE_FIRST,
        '[3] Smith et al. reported a 2019 baseline; our results improve it.',
        'Smith, "An example study", Journal of Methods, vol. 12, pp. 20-30, 2019.'])
    def test_citation_shapes_outside_references_remain_translatable(self, text) -> None:
        decision = classify(make_para(text, section_path=["Related work"]))
        assert decision is not BlockType.REFERENCE
        assert decision.is_translatable

    @pytest.mark.parametrize("heading", ["References", "7. References", "参考文献", "Bibliography"])
    def test_plain_reference_heading_opens_a_section_without_font_evidence(self, heading) -> None:
        from document_core.layout import build_sections

        paragraphs = [make_para(CORPUS_REFERENCE), make_para(heading),
            make_para(CORPUS_REFERENCE_FIRST, kind="heading", page=1),
            make_para("2018. doi:10.1000/continuation", page=1),
            make_para("Appendix", kind="heading", page=1),
            make_para(CORPUS_REFERENCE, page=1)]
        build_sections(paragraphs)
        assert classify(paragraphs[0]).is_translatable
        assert classify(paragraphs[1]) is BlockType.HEADING
        assert classify(paragraphs[2]) is BlockType.REFERENCE
        assert classify(paragraphs[3]) is BlockType.REFERENCE
        assert classify(paragraphs[5]).is_translatable


class TestFrontMatter:
    def test_the_title_is_a_title_even_though_layout_calls_it_a_heading(self) -> None:
        # The corpus title is short and set larger than the body, which is exactly the
        # layout stage's test for a heading.  Trusting that label first typed all 8 corpus
        # titles as headings.
        para = make_para(CORPUS_TITLE, y0=0.10, font_size=16.0, kind="heading")
        assert classify(para, position=0) == BlockType.TITLE

    def test_a_large_heading_deep_in_the_paper_is_not_a_title(self) -> None:
        para = make_para("3. Method", y0=0.10, font_size=16.0, kind="heading", page=2)
        assert classify(para, position=0) == BlockType.HEADING

    def test_a_large_heading_late_on_page_one_is_not_a_title(self) -> None:
        para = make_para("5. Conclusion", y0=0.10, font_size=16.0, kind="heading")
        assert classify(para, position=40) == BlockType.HEADING

    def test_a_heading_low_on_page_one_is_not_a_title(self) -> None:
        para = make_para("3. Method", y0=0.70, font_size=16.0, kind="heading")
        assert classify(para, position=1) == BlockType.HEADING

    def test_the_author_line_is_an_author(self) -> None:
        assert looks_like_author_line(CORPUS_AUTHOR) is True
        assert classify(make_para(CORPUS_AUTHOR, y0=0.18), position=1) == BlockType.AUTHOR

    def test_a_sentence_is_not_an_author_line(self) -> None:
        assert looks_like_author_line("We describe a reproducible pipeline.") is False

    def test_the_abstract_is_recognised_by_its_keyword(self) -> None:
        assert looks_like_abstract_start(CORPUS_ABSTRACT) is True
        assert classify(make_para(CORPUS_ABSTRACT, y0=0.25)) == BlockType.ABSTRACT

    def test_the_abstract_is_checked_before_the_title(self) -> None:
        # "Abstract—We describe…" is long and sits high on page one, so a length- or
        # position-based title test would claim it.
        para = make_para(CORPUS_ABSTRACT, y0=0.10, font_size=16.0)
        assert classify(para, position=1) == BlockType.ABSTRACT

    def test_a_mixed_language_abstract_is_recognised(self) -> None:
        text = "Abstract—Mixed-language documents occur when authors supply 中文摘要."
        assert looks_like_abstract_start(text) is True

    def test_a_sentence_mentioning_an_abstract_is_not_one(self) -> None:
        assert looks_like_abstract_start("The abstract of the paper is short.") is False


class TestHeadings:
    def test_a_numbered_heading_is_a_heading(self) -> None:
        assert classify(make_para(CORPUS_HEADING, kind="heading")) == BlockType.HEADING

    def test_a_subheading_is_a_heading(self) -> None:
        assert classify(make_para(CORPUS_SUBHEADING, kind="heading")) == BlockType.HEADING

    def test_a_level_alone_marks_a_heading(self) -> None:
        para = make_para("Method", kind="paragraph", level=2)
        assert classify(para) == BlockType.HEADING


class TestFurniture:
    def test_a_page_number_is_a_page_number(self) -> None:
        assert classify(make_para("12", kind="page_number")) == BlockType.PAGE_NUMBER

    def test_a_running_head_above_the_middle_is_a_header(self) -> None:
        assert classify(make_para("Journal of Things", y0=0.04, kind="furniture")) == BlockType.HEADER

    def test_a_running_foot_below_the_middle_is_a_footer(self) -> None:
        assert classify(make_para("Journal of Things", y0=0.96, kind="furniture")) == BlockType.FOOTER

    def test_furniture_is_not_translatable(self) -> None:
        assert BlockType.HEADER.is_translatable is False
        assert BlockType.PAGE_NUMBER.is_translatable is False


class TestFormulas:
    def test_a_formula_is_recognised(self) -> None:
        assert is_formula_like("f(x) = sum_i w_i x_i") is True

    def test_a_formula_is_not_translatable(self) -> None:
        assert BlockType.FORMULA.is_translatable is False

    def test_prose_mentioning_an_equation_is_not_a_formula(self) -> None:
        text = (
            "The score is computed as in the previous section, where we define the "
            "objective f and minimise it over the parameters of the model."
        )
        assert is_formula_like(text) is False

    def test_a_long_mathematical_paragraph_is_not_treated_as_a_formula(self) -> None:
        assert is_formula_like("x = 1 " * 200) is False

    @pytest.mark.parametrize(
        "text",
        [
            "born-digital",
            "n/a",
            "state-of-the-art",
            "docx",
            "end-to-end",
            "(see Figure 2)",
            "pp. 55-67",
        ],
    )
    def test_a_word_with_punctuation_is_not_a_formula(self, text: str) -> None:
        # A block typed as a formula is not translated, so a false positive here silently
        # drops a real word.  Counting the hyphen and the slash as operators typed
        # "born-digital" and "n/a" as formulas.
        assert is_formula_like(text) is False

    def test_a_subscript_expression_is_a_formula(self) -> None:
        assert is_formula_like("x_i") is True

    def test_a_comparison_operator_is_a_formula(self) -> None:
        assert is_formula_like("a ≤ b") is True


class TestBodyFontSize:
    def test_the_commonest_size_wins_over_outliers(self) -> None:
        paragraphs = [
            make_para("a", font_size=16.0),   # title
            make_para("b", font_size=10.0),
            make_para("c", font_size=10.0),
            make_para("d", font_size=10.0),
            make_para("e", font_size=13.0),   # heading
        ]
        assert body_font_size(paragraphs) == 10.0

    def test_no_font_information_returns_none(self) -> None:
        assert body_font_size([make_para("a", font_size=None)]) is None

    def test_furniture_is_ignored(self) -> None:
        paragraphs = [
            make_para("12", font_size=7.0, kind="page_number"),
            make_para("a", font_size=10.0),
            make_para("b", font_size=10.0),
        ]
        assert body_font_size(paragraphs) == 10.0


class TestAuthorIsNotTheTitle:
    """All 7 multi-author corpus lines were typed as titles before this guard.

    An author list is short and is set larger than the body, which is exactly the signal the
    title test keys on, so the title test claimed the author line first.  Position alone
    cannot separate them: both sit in the top zone of page one.
    """

    @pytest.mark.parametrize(
        "text",
        [
            CORPUS_AUTHOR,
            "D. Researcher and E. Scholar",
            "F. Analyst, G. Engineer, and H. Statistician",
            CORPUS_SINGLE_AUTHOR,
        ],
    )
    def test_an_author_line_is_not_a_title(self, text: str) -> None:
        para = make_para(text, y0=0.10, font_size=12.5)
        assert classify(para, position=1) is BlockType.AUTHOR

    def test_the_title_is_still_a_title(self) -> None:
        para = make_para(CORPUS_TITLE, y0=0.05, font_size=16.0)
        assert classify(para, position=0) is BlockType.TITLE

    @pytest.mark.parametrize(
        "text",
        ["1. Introduction", "2. Related Work", "3.1 Data and annotation", "IV. Method"],
    )
    def test_a_numbered_section_heading_is_not_the_title(self, text: str) -> None:
        # A numbered heading is short, is set larger than the body, and — for the first
        # section — sits near the top of page one, so `position` cannot separate them.
        # Measured: once the heading was split off from the paragraph under it, "1.
        # Introduction" was typed as the title in all 8 corpus documents, which is what made
        # the heading score 70/78.
        para = make_para(text, y0=0.30, font_size=12.5, kind="heading", level=1)
        assert classify(para, position=3) is BlockType.HEADING

    def test_an_unnumbered_heading_near_the_top_is_still_not_the_title(self) -> None:
        para = make_para("Acknowledgements", y0=0.60, font_size=12.5, kind="heading", level=1)
        assert classify(para, position=3) is not BlockType.TITLE

    def test_a_title_with_a_comma_is_not_mistaken_for_authors(self) -> None:
        # The veto must use the narrow shape test.  The looser author rule fires on any comma
        # plus two capitalised words, which would refuse a real title like this one.
        title = "Bilingual Reading, Anchored Translation"
        assert has_author_shape(title) is False
        assert classify(make_para(title, y0=0.05, font_size=16.0), position=0) is BlockType.TITLE

    def test_the_narrow_shape_test_rejects_prose_with_initials(self) -> None:
        assert has_author_shape("Reading order and evidence tracking are prerequisites.") is False


class TestCaptions:
    """No caption rule existed, so all 4 corpus captions came back as paragraphs."""

    def test_a_figure_caption_is_a_caption(self) -> None:
        assert classify(make_para(CORPUS_CAPTION, y0=0.5)) is BlockType.CAPTION

    @pytest.mark.parametrize(
        "text",
        [
            "Table 1. OCR character error rate by scan quality.",
            "Figure 3: Page-turn latency at the 95th percentile.",
            "Algorithm 2. Recursive whitespace cut.",
            "Fig. 4 Precision against recall for both columns.",
        ],
    )
    def test_the_usual_label_forms_are_recognised(self, text: str) -> None:
        assert classify(make_para(text, y0=0.5)) is BlockType.CAPTION

    @pytest.mark.parametrize(
        "text",
        [
            "Table 2 shows the results of the ablation study described above.",
            "Figure 5 illustrates the same effect on a scanned page.",
            "Algorithm 1 presents the procedure we follow in the rest of this section.",
        ],
    )
    def test_prose_that_merely_mentions_a_table_is_not_a_caption(self, text: str) -> None:
        # A caption names what the reader is looking at; it does not tell the reader about it.
        # Mistyping this prose as a caption keeps a real sentence out of translation.
        assert classify(make_para(text, y0=0.5)) is BlockType.PARAGRAPH

    def test_a_caption_is_translatable(self) -> None:
        assert BlockType.CAPTION.is_translatable


class TestEveryDecisionCarriesConfidence:
    def test_confidence_is_always_reported_with_a_reason(self) -> None:
        for text, kind in (
            (CORPUS_PARAGRAPH, "paragraph"),
            (CORPUS_HEADING, "heading"),
            (CORPUS_REFERENCE, "paragraph"),
            (CORPUS_ABSTRACT, "paragraph"),
            ("12", "page_number"),
        ):
            decision = classify_paragraph(
                make_para(text, kind=kind), position=3, page_index=0, body_size=10.0
            )
            assert 0.0 <= (decision.confidence.score or 0.0) <= 1.0
            assert decision.confidence.level is not None
            assert decision.reasons, f"no reason given for {decision.type}"

    def test_default_body_blocks_are_top_level_paragraphs(self) -> None:
        assert classify(make_para(CORPUS_PARAGRAPH)) == BlockType.PARAGRAPH
        assert BlockType.PARAGRAPH.is_translatable is True
        assert BlockType.PARAGRAPH.is_body is True
