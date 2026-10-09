"""Authored paper content for the acceptance corpus.

Everything here was written for this project.  The prose is deliberately shaped
like academic writing because the parser and translator have to cope with its
structure: long sentences, connective phrases, hedging, numbers, units,
citations, figure references and hyphenated compounds that wrap across lines.

The content is organised as :class:`~rr_m0.corpus_layout.ContentItem` sequences
so that the generator decides where each item lands.
"""

from __future__ import annotations

from .corpus_layout import ContentItem

__all__ = ["paper_a", "paper_b", "paper_c", "paper_d", "paper_e", "paper_f", "paper_g", "paper_h", "paper_i", "paper_j"]


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

REFERENCES = [
    "[1] A. Author and B. Author, \u201cA representative baseline for sequence labeling,\u201d Journal of Synthetic Methods, vol. 12, no. 3, pp. 210\u2013225, 2019. doi:10.1000/jsm.2019.210",
    "[2] C. Researcher, D. Scholar, and E. Writer, \u201cOn the evaluation of structured predictors,\u201d in Proceedings of the Synthetic Conference on Learning, 2020, pp. 1\u201312.",
    "[3] F. Analyst, \u201cA survey of annotation protocols,\u201d arXiv:2101.01234, 2021.",
    "[4] G. Engineer, \u201cReproducibility notes for the baseline corpus,\u201d Technical report TR-2021-07, 2021.",
    "[5] H. Statistician and I. Reviewer, \u201cConfidence intervals for rare-event recall,\u201d Synthetic Statistics Letters, vol. 8, pp. 55\u201367, 2018. doi:10.1000/ssl.2018.55",
]

ACKNOWLEDGEMENT = (
    "The authors thank the anonymous reviewers for detailed comments on the "
    "evaluation protocol, and the synthetic data team for preparing the "
    "annotated development split."
)


def _intro(topic: str, metric: str) -> list[ContentItem]:
    return [
        ContentItem("heading", text="1. Introduction", level=1),
        ContentItem(
            "paragraph",
            text=(
                f"Reading order and evidence tracking are prerequisites for reliable "
                f"automatic processing of {topic}. In this paper we study how a "
                f"structure-aware pipeline can keep every generated statement attached "
                f"to the passage it came from. Prior work has reported strong "
                f"{metric} on carefully cleaned collections, yet those numbers are "
                f"known to degrade when the input layout differs from the development "
                f"set [1], [2]. We therefore treat layout variation as a "
                f"first-class experimental factor rather than a preprocessing detail."
            ),
        ),
        ContentItem(
            "paragraph",
            text=(
                "Our contributions are threefold. First, we describe a reproducible "
                "annotation protocol for two-column and single-column documents that "
                "records, for every block, the region of the page it occupies. "
                "Second, we show that a small set of layout heuristics recovers the "
                "intended reading order on the majority of pages without any "
                "learned component. Third, we quantify how much a long-lived cache "
                "changes measured cost, and we argue that reported cost figures "
                "should always state whether cache hits are included."
            ),
        ),
        ContentItem(
            "paragraph",
            text=(
                "The remainder of the paper is organised as follows. Section 2 "
                "reviews related work on document layout analysis and on evaluation "
                "of machine translation for scientific text. Section 3 defines the "
                "document model and the coordinate contract we rely on. Section 4 "
                "describes the pipeline, Section 5 reports results, and Section 6 "
                "discusses limitations and threats to validity."
            ),
        ),
    ]


def _related(topic: str) -> list[ContentItem]:
    return [
        ContentItem("heading", text="2. Related Work", level=1),
        ContentItem(
            "paragraph",
            text=(
                f"Layout analysis for {topic} has a long history. Rule-based column "
                f"detection using projection profiles remains competitive on clean "
                f"born-digital pages, while learned detectors dominate on scanned "
                f"material where skew and noise break the projection assumption [3]. "
                f"Our approach follows the rule-based tradition for born-digital "
                f"pages and treats scanned pages as a separate, explicitly flagged "
                f"input class."
            ),
        ),
        ContentItem("heading", text="2.1 Translation of scientific text", level=2),
        ContentItem(
            "paragraph",
            text=(
                "Scientific translation differs from general translation in ways that "
                "matter for evaluation. Negation, quantifiers, units and uncertainty "
                "markers carry most of the meaning, and a fluent rendering that "
                "reverses a negation is worse than a literal one that preserves it. "
                "We therefore score protected content separately from fluency, and we "
                "report both."
            ),
        ),
        ContentItem(
            "paragraph",
            text=(
                "A second difference is citation structure. Reference markers, "
                "equation numbers, figure labels and DOIs must survive unchanged; "
                "translating a DOI or dropping an equation number breaks the reader's "
                "ability to check the claim against the original. We use placeholders "
                "for these tokens and verify their completeness mechanically."
            ),
        ),
    ]


def _method(topic: str, with_formula: bool = False) -> list[ContentItem]:
    items: list[ContentItem] = [
        ContentItem("heading", text="3. Method", level=1),
        ContentItem(
            "paragraph",
            text=(
                "We model a document as an ordered sequence of typed blocks. Each "
                "block carries the text as extracted, a cleaned variant, and one or "
                "more source regions. Regions are expressed in normalised page "
                "coordinates with the origin at the top-left of the displayed page, "
                "so that zoom, rotation and cropping never change the stored value."
            ),
        ),
        ContentItem(
            "paragraph",
            text=(
                "Reading order is recovered in three steps. Pages are first split "
                "into horizontal bands separated by unusually large vertical gaps; "
                "such a gap usually means a full-width object such as a figure or a "
                "table interrupted the column flow. Inside each band we search for a "
                "vertical gutter using a projection profile, and order the resulting "
                "columns from left to right. Finally, blocks that continue across a "
                "column or page boundary are merged when the preceding line lacks "
                "sentence-final punctuation and the following line begins in lower "
                "case."
            ),
        ),
    ]
    if with_formula:
        items += [
            ContentItem(
                "paragraph",
                text=(
                    "Let $x_i$ denote the feature vector for token i and let $y_i$ "
                    "denote its assigned label. The score of a labelling is defined "
                    "in Eq. (1), where the transition term encourages label "
                    "consistency between adjacent tokens."
                ),
            ),
            ContentItem("formula", text="S(x, y) = \u03a3_i \u03b8 \u00b7 f(x_i, y_i) + \u03a3_i T(y_i, y_{i+1})", number="(1)"),
            ContentItem(
                "paragraph",
                text=(
                    "Parameters are estimated by maximising the conditional "
                    "likelihood of the training labels subject to an L2 penalty of "
                    "0.01. We use a batch size of 32 and train for at most 40 epochs, "
                    "selecting the checkpoint with the best development-set F1."
                ),
            ),
        ]
    items += [
        ContentItem("heading", text="3.1 Data and annotation", level=2),
        ContentItem(
            "paragraph",
            text=(
                "The corpus contains 24 documents: 8 born-digital PDFs, 6 scanned or "
                "mixed PDFs, 4 page images and 6 word-processing documents. Two "
                "annotators labelled every page independently and disagreements were "
                "resolved by discussion; the resulting agreement was 0.91 by Cohen's "
                "kappa. Pages were scanned at 300 dpi, and 20% of the scanned pages "
                "were rotated by 2 degrees to introduce a realistic skew."
            ),
        ),
        ContentItem(
            "paragraph",
            text=(
                "Text extracted from a PDF is not assumed to be usable merely because "
                "a text layer exists. We measure the proportion of replacement "
                "characters and the density of alphanumeric characters, and route a "
                "page to optical recognition when the layer fails either test."
            ),
        ),
    ]
    return items


def _results(with_table: bool, with_figure: bool) -> list[ContentItem]:
    items: list[ContentItem] = [
        ContentItem("heading", text="4. Results", level=1),
        ContentItem(
            "paragraph",
            text=(
                "Table 1 summarises coverage and reading-order accuracy. Body "
                "character coverage reaches 99.1% on born-digital pages, and "
                "adjacent-block order is correct for 96.4% of the annotated pairs. "
                "The two-column subset is reported separately because column "
                "splitting is the dominant source of ordering errors."
            ),
        ),
    ]
    if with_table:
        items.append(
            ContentItem(
                "table",
                caption="Table 1. Coverage and order accuracy by input class.",
                header=["Input class", "Docs", "Coverage", "Order acc."],
                rows=[
                    ["born-digital", "8", "99.1%", "96.4%"],
                    ["scanned", "6", "97.8%", "95.1%"],
                    ["images", "4", "98.2%", "n/a"],
                    ["docx", "6", "98.5%", "n/a"],
                ],
            )
        )
    if with_figure:
        items.append(
            ContentItem(
                "figure",
                caption=(
                    "Figure 1. Error rate as a function of page rotation. Markers show "
                    "measured values; the curve is a guide to the eye only, and no "
                    "functional form is claimed."
                ),
                label="Figure 1",
                height=126.0,
            )
        )
    items += [
        ContentItem("heading", text="4.1 Ablation", level=2),
        ContentItem(
            "paragraph",
            text=(
                "Removing band segmentation reduces order accuracy on pages "
                "containing full-width objects from 96.4% to 81.2%, and removing the "
                "gutter search reduces accuracy on two-column pages to 74.5%. "
                "Dehyphenation has a much smaller effect, changing character coverage "
                "by 0.3 percentage points, but it is kept because the failures it "
                "prevents are visible to the reader."
            ),
        ),
        ContentItem(
            "paragraph",
            text=(
                "Cache behaviour is reported in the same table. With a warm cache, "
                "median cost per document falls by 68%, and the effect is larger for "
                "documents whose sections repeat standard phrasing. We emphasise that "
                "this is a property of the input distribution, not of the method."
            ),
        ),
    ]
    return items


def _discussion() -> list[ContentItem]:
    return [
        ContentItem("heading", text="5. Discussion and Limitations", level=1),
        ContentItem(
            "paragraph",
            text=(
                "Three limitations deserve emphasis. First, our evaluation uses "
                "synthetic documents authored for this study; it cannot establish "
                "performance on published literature, and no claim of that kind is "
                "made. Second, the annotation covers layout and coverage, not "
                "semantic correctness of translation. Third, the scanned subset is "
                "clean printed text; handwritten annotations and photographs of "
                "curved pages are out of scope for this version."
            ),
        ),
        ContentItem(
            "paragraph",
            text=(
                "We also note that a page-level fallback is not equivalent to a "
                "paragraph-level anchor. When no reliable region association exists, "
                "the interface must say so rather than presenting an approximate "
                "match as exact."
            ),
        ),
        ContentItem("heading", text="6. Conclusion", level=1),
        ContentItem(
            "paragraph",
            text=(
                "We presented a structure-aware pipeline for bilingual reading of "
                "research documents, with an explicit coordinate contract, "
                "per-format adapters and anchored translations. The system preserves "
                "the original page for inspection while giving the reader a "
                "translated view that can always be traced back to its source."
            ),
        ),
        ContentItem("heading", text="Acknowledgements", level=1),
        ContentItem("paragraph", text=ACKNOWLEDGEMENT),
    ]


def _references_section() -> list[ContentItem]:
    return [ContentItem("heading", text="References", level=1)] + [
        ContentItem("reference", text=ref) for ref in REFERENCES
    ]


# ---------------------------------------------------------------------------
# Individual papers
# ---------------------------------------------------------------------------


def paper_a() -> list[ContentItem]:
    """Single-column born-digital paper."""
    return (
        [ContentItem("abstract", text=(
            "We describe a reproducible pipeline for extracting structured text from "
            "single-column research articles and for anchoring a translation to the "
            "extracted blocks. The pipeline separates extraction from presentation, "
            "records the region of the page associated with every block, and reports "
            "coverage and ordering accuracy against a labelled corpus of 24 "
            "documents. We find that a small set of layout heuristics recovers the "
            "intended reading order for 96.4% of adjacent block pairs without any "
            "learned component."
        ))]
        + _intro("research articles", "accuracy")
        + _related("research articles")
        + _method("research articles")
        + _results(with_table=True, with_figure=True)
        + _discussion()
        + _references_section()
    )


def paper_b() -> list[ContentItem]:
    """Two-column born-digital paper with formula and table."""
    return (
        [ContentItem("abstract", text=(
            "Two-column layouts remain the dominant format in the physical sciences. "
            "We study reading-order recovery on such pages and report an accuracy of "
            "96.4% for adjacent block pairs, with column splitting accounting for "
            "most residual errors. A band-segmentation step that restarts column "
            "flow after full-width objects raises accuracy from 81.2% to 96.4%."
        ))]
        + _intro("two-column articles", "ordering accuracy")
        + _related("two-column articles")
        + _method("two-column articles", with_formula=True)
        + _results(with_table=True, with_figure=True)
        + _discussion()
        + _references_section()
    )


def paper_c() -> list[ContentItem]:
    """Single-column paper with formula, footnotes and cross-page paragraph."""
    long_paragraph = (
        "This section deliberately contains a paragraph that is longer than a "
        "single column can hold, so that it must continue across a page boundary. "
        "A reader who jumps to the continuation must land on the same translated "
        "block rather than on a second, disconnected fragment, and an annotation "
        "attached to the beginning of the paragraph must remain attached to the "
        "whole. " * 3
    ) + (
        "We use this case to check that a block may own several source regions on "
        "different pages, and that the interface reports such a block honestly "
        "instead of pretending it fits on one page."
    )
    return (
        [ContentItem("abstract", text=(
            "Long paragraphs, footnotes and equations stress the relationship "
            "between a semantic block and the regions it occupies. We describe how a "
            "block may own several source regions and how a page-level fallback is "
            "distinguished from an exact paragraph anchor."
        ))]
        + _intro("mathematical articles", "coverage")
        + [
            ContentItem("heading", text="2. Formal Setting", level=1),
            ContentItem("paragraph", text=long_paragraph),
            ContentItem("formula", text="p(y | x) = exp(S(x, y)) / \u03a3_{y'} exp(S(x, y'))", number="(2)"),
            ContentItem("paragraph", text=(
                "Equation (2) normalises the score over all labellings. In practice "
                "the sum is computed by dynamic programming, which is exact for "
                "linear-chain models and approximate otherwise."
            )),
            ContentItem("footnote", text="1 Supported by synthetic grant SYN-2021-004."),
            ContentItem("footnote", text="2 Correspondence: author@example.invalid"),
        ]
        + _method("mathematical articles", with_formula=True)
        + _results(with_table=True, with_figure=False)
        + _discussion()
        + _references_section()
    )


def paper_d() -> list[ContentItem]:
    """Single-column paper with a Chinese/English mixed abstract."""
    return (
        [ContentItem("abstract", text=(
            "Mixed-language documents occur when authors supply a Chinese abstract "
            "alongside an English body. 本文研究了中英文混排文档的阅读顺序恢复问题, "
            "并在 24 份标注文档上报告了结果。 We treat script mixing as an input "
            "property rather than an error condition."
        ))]
        + [
            ContentItem("heading", text="1. 引言 Introduction", level=1),
            ContentItem("paragraph", text=(
                "中英文混排文档在阅读顺序恢复时会遇到额外的困难: 字符宽度不一致, "
                "标点符号属于不同书写系统, 而且断行位置常常落在中文字符之间。 "
                "In this paper we describe how a projection-profile method copes with "
                "these inputs, and we report the cases where it does not."
            )),
            ContentItem("paragraph", text=(
                "We measured character coverage separately for the two scripts. "
                "中文部分的字符覆盖率达到了 99.4%, 英文部分为 99.1%, 差异主要来自 "
                "中英文之间的空格处理。"
            )),
        ]
        + _intro("mixed-language documents", "coverage")
        + _method("mixed-language documents")
        + _results(with_table=True, with_figure=False)
        + _discussion()
        + _references_section()
    )


def paper_e() -> list[ContentItem]:
    """Single-column paper with deliberately hyphen-heavy wrapped compounds."""
    return (
        [ContentItem("abstract", text=(
            "Hyphenation is the most common source of silent corruption in extracted "
            "text. We distinguish word-break hyphens from genuine compound hyphens "
            "using the fragment that precedes the break and the word that follows "
            "it, and we report the residual error rate on a set of hand-checked "
            "wrapped compounds."
        ))]
        + [
            ContentItem("heading", text="1. Introduction", level=1),
            ContentItem("paragraph", text=(
                "A state-of-the-art system that relies on a well-known heuristic can "
                "still corrupt text when a high-quality multi-modal pipeline joins a "
                "real-time, end-to-end, non-linear, co-variate, or cross-domain "
                "compound that happened to wrap. We therefore treat every line-final "
                "hyphen as ambiguous until the following fragment resolves it."
            )),
            ContentItem("paragraph", text=(
                "The distinction matters because a wrong join is invisible: "
                "'multi-modal' fused into 'multimodal' changes the token while "
                "'experimental' split as 'experi-mental' is joined correctly. Both "
                "cases look identical at the line break, so the resolving evidence "
                "must come from the fragments themselves."
            )),
            ContentItem("paragraph", text=(
                "We evaluate on a hand-checked set of 200 wrapped compounds drawn "
                "from the synthetic corpus, reporting precision and recall for the "
                "join decision rather than a single accuracy figure, because the two "
                "error directions have different costs."
            )),
        ]
        + _method("hyphen-heavy documents")
        + _results(with_table=True, with_figure=True)
        + _discussion()
        + _references_section()
    )


def paper_f() -> list[ContentItem]:
    """Two-column paper with a wide table and a rotation page."""
    return (
        [ContentItem("abstract", text=(
            "We report an evaluation of anchored translation for two-column "
            "articles, including the case where a table spans both columns and "
            "interrupts the reading flow."
        ))]
        + _intro("two-column articles", "anchoring accuracy")
        + _method("two-column articles", with_formula=True)
        + [
            ContentItem("heading", text="4. Wide Objects", level=1),
            ContentItem("paragraph", text=(
                "A full-width table or figure forces the column flow to restart after "
                "the object. Treating the whole page as two independent columns puts "
                "every block after the object into the wrong order, which is why band "
                "segmentation precedes column detection in our pipeline."
            )),
            ContentItem(
                "table",
                caption="Table 2. Full-width summary of anchoring results.",
                header=["Condition", "Blocks", "Exact", "Page-level", "Unmatched"],
                rows=[
                    ["single column", "1204", "96.9%", "2.4%", "0.7%"],
                    ["two column", "2311", "95.2%", "3.9%", "0.9%"],
                    ["rotated page", "188", "92.0%", "6.4%", "1.6%"],
                    ["scanned", "942", "94.1%", "4.8%", "1.1%"],
                ],
            ),
            ContentItem("paragraph", text=(
                "Rotation is handled in the display layer: the stored region is "
                "normalised against the displayed page, so a reader who rotates the "
                "view never invalidates an anchor."
            )),
        ]
        + _results(with_table=False, with_figure=True)
        + _discussion()
        + _references_section()
    )


def paper_g() -> list[ContentItem]:
    """Short single-column paper with many short sections."""
    items: list[ContentItem] = [
        ContentItem("abstract", text=(
            "A short paper used to exercise the heading tree and section context "
            "passed to translation. Its sections nest three levels deep."
        )),
        ContentItem("heading", text="1. Introduction", level=1),
        ContentItem("paragraph", text=(
            "Section context helps a translator resolve ambiguous terms. Passing the "
            "nearest enclosing headings costs a few tokens and measurably reduces "
            "term drift across a long document."
        )),
        ContentItem("heading", text="1.1 Background", level=2),
        ContentItem("paragraph", text=(
            "The background is deliberately brief. The point of this paper is the "
            "shape of the section tree, not the content of any section."
        )),
        ContentItem("heading", text="1.1.1 Terminology", level=3),
        ContentItem("paragraph", text=(
            "We use 'block' for a semantic unit and 'region' for the page area it "
            "occupies. A block may have several regions; a region belongs to exactly "
            "one block."
        )),
        ContentItem("heading", text="1.2 Scope", level=2),
        ContentItem("paragraph", text=(
            "The scope is limited to born-digital single-column documents in this "
            "paper; other formats are covered elsewhere."
        )),
        ContentItem("heading", text="2. Method", level=1),
        ContentItem("paragraph", text=(
            "Headings are clustered by numbering depth when a numbering scheme "
            "exists, and by font size otherwise. The two signals can disagree, in "
            "which case numbering wins because it is explicit."
        )),
    ]
    return items + _results(with_table=False, with_figure=False) + _discussion() + _references_section()


def paper_h() -> list[ContentItem]:
    """Two-column paper without references, used for variation."""
    return (
        [ContentItem("abstract", text=(
            "This document omits a reference list and a conclusion to test that the "
            "parser does not rely on either being present."
        ))]
        + _intro("technical reports", "robustness")
        + _method("technical reports")
        + [
            ContentItem("heading", text="4. Results", level=1),
            ContentItem("paragraph", text=(
                "Omitting references has no measurable effect on layout recovery, "
                "which is the expected result: references are ordinary blocks that "
                "happen to carry identifiers we protect from translation."
            )),
            ContentItem("paragraph", text=(
                "We nevertheless keep a dedicated block type for references, because "
                "the reader's expectation differs: author names, journal titles and "
                "DOIs should normally be left as published."
            )),
        ]
        + _results(with_table=True, with_figure=False)
    )


def paper_i() -> list[ContentItem]:
    """Scanned-source paper: same layout, used to render degraded images."""
    return paper_a()


def paper_j() -> list[ContentItem]:
    """DOCX-source paper with tables, figures and formulas."""
    return (
        [ContentItem("abstract", text=(
            "Word-processing documents reach us without fixed pagination, so the "
            "source is preserved unchanged while a derived paginated rendering "
            "supplies the coordinates used for anchoring."
        ))]
        + _intro("word-processing documents", "fidelity")
        + _method("word-processing documents", with_formula=True)
        + _results(with_table=True, with_figure=True)
        + _discussion()
        + _references_section()
    )
