"""Semantic block typing.

The layout stage decides *geometry* and *order*; it labels a paragraph only as
"paragraph" or "heading" and puts running heads in a separate list.  Nothing yet decides
whether a block is a title, an author line, an abstract, a bibliography entry or a
formula — and that distinction decides what is translated.

It matters more than it looks:

* A **bibliography entry** must keep DOIs, years and author names exactly as written.
  Sending ``[5] H. Statistician and I. Reviewer, "Co…"`` through a translator produces a
  plausible-looking reference that no longer identifies the paper.
* A **formula** must never be translated at all.
* A **running header** is furniture; translating it adds noise to every page.
* A **title** and an **abstract** are worth translating, and a translator that knows a
  block is a title will not treat it as a sentence fragment.

Every decision carries a confidence level, because a wrong "reference" silently strips a
paragraph from translation, and a wrong "paragraph" puts a citation through a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .coords import BBox
from .layout import Paragraph
from .models import BlockType, Confidence, ConfidenceLevel

__all__ = [
    "TypeDecision",
    "classify_paragraph",
    "body_font_size",
    "is_reference_like",
    "is_reference_section_heading",
    "is_formula_like",
    "in_references_section",
    "looks_like_author_line",
    "looks_like_abstract_start",
    "looks_like_title",
]

#: A heading deeper than this many characters is probably a paragraph that a font-size
#: heuristic misfired on.
HEADING_MAX_CHARS = 120

# Positions are compared against ``Paragraph.bbox``, which the layout contract defines as
# **normalised to the displayed page** (0-1, origin top-left).  These constants are
# therefore fractions of a page, not points — there is deliberately no page-height
# parameter, because passing one invites the mistake of passing the wrong frame.
TITLE_ZONE = 0.35         # the title sits in the top third of page one
MID_PAGE = 0.5            # above this a running head is a header, below it a footer
FOOTNOTE_ZONE = 0.82      # small type below this is a footnote

#: Sections whose contents are bibliography entries.
_REFERENCE_SECTION_RE = re.compile(
    r"^(references|reference|bibliography|works\s+cited|literature\s+cited|"
    r"参考文献|引用文献)$",
    re.IGNORECASE,
)

#: A leading section number, as in "7. References", "6 References" or "VII. Bibliography".
#: Stripped before the section check, because a numbered heading is the normal case and an
#: anchored pattern would reject it.
_SECTION_NUMBER_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[IVXLC]+[.)])\s+")

#: "[1] ", "[12].", "1. " at the start of a bibliography entry, or a bare "[3]".
_REFERENCE_LEAD_RE = re.compile(r"^\s*(\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}\.)\s+")
#: A bracketed lead, "``[3]``" or "``(3)``".  Unambiguous: section headings are not numbered
#: this way in any of the styles this reader targets.
_BRACKET_LEAD_RE = re.compile(r"^\s*(\[\d{1,3}\]|\(\d{1,3}\))\s+")
#: A bare numbered lead, "``3.``".  Ambiguous — it is how a bibliography entry is numbered
#: *and* how a section is numbered — so it is never evidence on its own.
_NUMBERED_LEAD_RE = re.compile(r"^\s*\d{1,3}\.\s+")

#: Things that only appear in a citation, and must survive verbatim.
_CITATION_MARKERS_RE = re.compile(
    r"(doi\s*:|https?://|arxiv|isbn|pp?\.\s*\d|\bvol\.\s*\d|\bno\.\s*\d|"
    r"\bet\s+al\.|\beds?\.|\bIn:\s|\bProceedings\b|\bJournal\b)",
    re.IGNORECASE,
)

#: A four-digit year anywhere, which in a short line strongly suggests a citation.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

#: Quoted title inside a citation, e.g. `"A representation of…"`.
_QUOTED_TITLE_RE = re.compile(r"[“\"][^”\"]{6,}[”\"]")

#: Real mathematical operators.  Deliberately EXCLUDES the hyphen, slash, underscore,
#: brackets and angle brackets: those occur constantly in ordinary prose, and counting
#: them made "born-digital" and "n/a" into formulas, which silently keeps a real word out
#: of the translation.
_MATH_OPERATORS = set("=+−±×÷∑∏∫√≤≥≈≠∈∉⊂⊆∪∩→←↔∂∇^")
_FORMULA_WORDS_RE = re.compile(r"\b(sin|cos|tan|log|exp|lim|max|min|arg|det|Pr|Var|E)\b")
#: A subscript or superscript, as in "x_i" or "w^2".
_SUBSUP_RE = re.compile(r"[A-Za-z]\s*[_^]\s*[\w{]")

_ABSTRACT_RE = re.compile(
    r"^\s*(abstract|summary|摘要)\b[\s:—–\-]*", re.IGNORECASE
)

#: "A. Author, B. Author, and C. Author" — initials, surnames, separators, no sentence.
_AUTHOR_LINE_RE = re.compile(
    r"^[A-Z]\.\s*[A-Z][\w'’\-]*"
    r"(\s*,\s*|\s+and\s+|\s*&\s*)"
    r"([A-Z]\.\s*[A-Z][\w'’\-]*|[A-Z][\w'’\-]*)+"
)
_SENTENCE_END_RE = re.compile(r"[.!?。！？]\s*$")

#: A single author, "O. Author", which the multi-author pattern above cannot match because
#: it requires a separator.  Kept to this exact shape — an initial and one surname — so it
#: cannot claim a title that merely happens to contain a full stop.
_SINGLE_AUTHOR_RE = re.compile(r"^[A-Z]\.\s*[A-Z][\w'’\-]*(\s*[¹²³*†‡])?$")

#: A figure or table caption, e.g. "Figure 1. Error rate as a function of page rotation."
_CAPTION_RE = re.compile(
    r"^\s*(figure|fig\.?|table|tab\.?|algorithm|listing|scheme)\s*\.?\s*\d+[a-z]?\b",
    re.IGNORECASE,
)
#: A caption names what the reader is looking at; it does not tell the reader about it.  If
#: the word after the label is a finite verb then this is prose *referring* to a figure —
#: "Table 2 shows the results" — and typing that as a caption would keep the sentence out of
#: translation, because captions are not translated as body prose.  This corpus has no such
#: sentence, so this guard is reasoned rather than measured; limitations.md 18 says so.
_CAPTION_CLAUSE_RE = re.compile(
    r"^\s*(figure|fig\.?|table|tab\.?|algorithm|listing|scheme)\s*\.?\s*\d+[a-z]?[.,:]?\s+"
    r"(\w+\s+)?(shows?|presents?|lists?|gives?|summari[sz]es?|depicts?|illustrates?|"
    r"reports?|compares?|contains?|provides?|is|are|was|were)\b",
    re.IGNORECASE,
)


@dataclass
class TypeDecision:
    """A block's type, how sure we are, and why."""

    type: BlockType
    confidence: Confidence
    reasons: list[str] = field(default_factory=list)


def _normalise(text: str) -> str:
    return " ".join(text.split())


def in_references_section(section_path: list[str]) -> bool:
    """Whether the block sits under a References / Bibliography heading."""
    for part in section_path:
        if is_reference_section_heading(part):
            return True
    return False


def is_reference_section_heading(text: str) -> bool:
    """Whether a block *is* the References heading rather than an entry under it.

    Needed because the section check would otherwise swallow the heading that opens the
    section, turning "References" itself into a bibliography entry.  A leading section
    number is stripped first: "7. References" is how the heading usually appears.
    """
    stripped = _normalise(text).strip(" .:：-")
    stripped = _SECTION_NUMBER_RE.sub("", stripped)
    return bool(_REFERENCE_SECTION_RE.match(stripped.strip()))


def is_reference_like(text: str) -> bool:
    """Whether a line reads as a bibliography entry rather than prose.

    A numbered lead marker is the strong signal, because that is how a bibliography is
    laid out.  Without one, the *whole* citation shape is required: a quoted title, a year
    and citation markers.  A looser rule — "contains a year and two citation markers" —
    was tried and measured: it typed **28 real paragraphs** as bibliography entries, since
    an ordinary research paragraph cites several papers and mentions a year.  Mistyping
    prose as a citation removes it from translation, so the false-positive direction is the
    expensive one and the rule is deliberately strict.
    """
    stripped = _normalise(text)
    if not stripped:
        return False

    has_lead = bool(_BRACKET_LEAD_RE.match(stripped))
    # A bare "3." is how a section heading is numbered as well as how a bibliography entry is,
    # so it cannot carry a decision by itself.  This mattered in practice: once paragraph
    # breaks were fixed (ADR 0014), "1. Introduction" merged with the paragraph under it, the
    # merged block cited two papers, and the numbered lead plus one citation marker typed a
    # whole section of prose as a bibliography — which removes it from translation.  The
    # comment below had already noticed the ambiguity but only guarded the shorter branch.
    numbered_lead = bool(_NUMBERED_LEAD_RE.match(stripped))
    marker_hits = len(_CITATION_MARKERS_RE.findall(stripped))
    has_year = bool(_YEAR_RE.search(stripped))
    has_quoted = bool(_QUOTED_TITLE_RE.search(stripped))

    # A very long block is only a bibliography if it is *dense* in citation markers.  Length
    # by itself must not imply a citation: any over-merged body block is long too, and a
    # bare length rule typed the merged author+abstract block of ``pdf_b_two_column`` as a
    # reference, which would have kept the whole abstract out of translation.
    if len(stripped) > 1200:
        density = marker_hits / (len(stripped) / 100.0)
        return marker_hits >= 3 and density >= 1.0 and has_year

    if has_lead:
        # "[3] Smith, J., Title, Journal, 2019, pp. 1-10."
        if has_year or has_quoted or marker_hits >= 1:
            return True
        # "[3] Smith, J." — a short entry whose continuation carries the year.  Only
        # accepted when it is clearly entry-shaped and not a numbered section heading.
        if len(stripped) > 30 and not stripped.rstrip().endswith(":"):
            return True
    if numbered_lead:
        # Accept a bare number only on the full entry shape: a year and a quoted title, which
        # is what distinguishes "3. Smith, J., “A title,” 2019." from "3. Method We model…".
        return has_year and has_quoted and marker_hits >= 2
    # No lead marker: require the full shape, which ordinary prose does not have.
    if has_quoted and has_year and marker_hits >= 2:
        return True
    return False


def is_formula_like(text: str) -> bool:
    """Whether a block is mathematical notation rather than prose.

    Requires a real mathematical operator, not merely punctuation that also appears in
    prose.  An earlier version counted hyphens, slashes and brackets as symbols, which
    typed ``born-digital`` and ``n/a`` as formulas — and a block typed as a formula is not
    translated, so that silently drops a real word from the document.
    """
    stripped = _normalise(text)
    if not stripped:
        return False
    if len(stripped) > 400:
        return False

    words = re.findall(r"[A-Za-z]{2,}", stripped)
    if len(words) >= 8:
        return False

    operators = sum(1 for ch in stripped if ch in _MATH_OPERATORS)
    if operators >= 1:
        return True
    # A subscript or superscript with very few words is notation even without a top-level
    # operator, e.g. a lone "x_i".
    if _SUBSUP_RE.search(stripped) and len(words) <= 3:
        return True
    if _FORMULA_WORDS_RE.search(stripped) and operators >= 1:
        return True
    return False


def has_author_shape(text: str) -> bool:
    """Whether a line has the *specific* shape of an author list.

    Narrower than :func:`looks_like_author_line` on purpose.  This one is used to veto the
    title test, so it must not fire on a title that merely contains a comma: the corpus
    titles are not affected, but "Bilingual Reading, Anchored Translation" would be caught
    by the looser comma-and-capitals rule and would then be refused as a title.
    """
    stripped = _normalise(text)
    if not stripped or _SENTENCE_END_RE.search(stripped):
        return False
    return bool(_AUTHOR_LINE_RE.match(stripped) or _SINGLE_AUTHOR_RE.match(stripped))


def looks_like_author_line(text: str) -> bool:
    """Whether a line is a list of author names."""
    stripped = _normalise(text)
    if not stripped or len(stripped) > 300:
        return False
    # An author line has no sentence-ending punctuation and is mostly proper nouns.
    if _SENTENCE_END_RE.search(stripped):
        return False
    if has_author_shape(stripped):
        return True
    # "Jane Doe¹, John Smith²" — comma separated, no lowercase function words.
    if "," in stripped and len(stripped.split()) <= 30:
        commas = stripped.count(",")
        if commas >= 1 and not re.search(r"\b(is|are|was|were|the|of|in|for|with)\b", stripped):
            initials = len(re.findall(r"\b[A-Z]\.", stripped))
            capitals = len(re.findall(r"\b[A-Z][a-z]+", stripped))
            if initials >= 1 or capitals >= 2:
                return True
    return False


def looks_like_abstract_start(text: str) -> bool:
    return bool(_ABSTRACT_RE.match(_normalise(text)))


def looks_like_title(
    paragraph: Paragraph,
    *,
    body_size: float | None,
    page_index: int,
    position: int,
) -> bool:
    """Whether a block is the paper's title.

    Restricted to the top of the first page and to a block set larger than the body, so a
    large-font section heading deep in the paper cannot be mistaken for the title.
    """
    if page_index != 0 or position > 4:
        return False
    stripped = _normalise(paragraph.text)
    if not stripped or len(stripped) > 400:
        return False
    if _SENTENCE_END_RE.search(stripped) and stripped.count(".") > 1:
        return False
    # An author list is short and is set larger than the body, which is exactly the signal
    # this test keys on, so without this the author line outranks the title and claims it.
    # Measured: all 7 multi-author corpus lines were typed as titles before this guard.
    if has_author_shape(stripped):
        return False
    # A numbered section heading is also short, is also set larger than the body, and — for
    # the first section — also sits near the top of page one, so `position` cannot separate
    # them.  Measured: "1. Introduction" was typed as the title in all 8 corpus documents
    # once the heading was split off from the paragraph under it, which is what made the
    # heading score 70/78.
    if _SECTION_NUMBER_RE.match(stripped):
        return False
    # Top of the first page, in normalised units.
    if paragraph.bbox.y0 > TITLE_ZONE:
        return False
    if body_size and paragraph.font_size and paragraph.font_size > body_size * 1.12:
        return True
    # No font information: a short, non-sentence first block above the abstract.
    return bool(paragraph.font_size is None) and len(stripped.split()) <= 25


def body_font_size(paragraphs: list[Paragraph]) -> float | None:
    """The most common font size among paragraph-like blocks.

    A median would be dragged around by the few headings and captions, and it is exactly
    those outliers we want to detect.
    """
    from collections import Counter

    sizes = [
        round(p.font_size, 1)
        for p in paragraphs
        if p.font_size and p.kind not in {"page_number", "furniture"}
    ]
    if not sizes:
        return None
    return Counter(sizes).most_common(1)[0][0]


def classify_paragraph(
    paragraph: Paragraph,
    *,
    position: int,
    page_index: int,
    body_size: float | None,
    section_path: list[str] | None = None,
) -> TypeDecision:
    """Decide what a paragraph is, with a confidence and the reasons for it."""
    reasons: list[str] = []
    text = _normalise(paragraph.text)
    path = section_path if section_path is not None else list(paragraph.section_path)

    # 1. Furniture is already identified by the layout stage; trust its own labels.
    if paragraph.kind == "page_number":
        return TypeDecision(BlockType.PAGE_NUMBER, Confidence(1.0, ConfidenceLevel.HIGH, "layout"), ["layout label"])
    if paragraph.kind == "furniture":
        # A running head sits at the top, a footer at the bottom; the layout stage does
        # not distinguish them, and the distinction is visible from the position.
        kind = BlockType.HEADER if paragraph.bbox.y0 < MID_PAGE else BlockType.FOOTER
        return TypeDecision(kind, Confidence(0.9, ConfidenceLevel.HIGH, "layout"), ["layout label"])

    # The order of the checks below encodes which evidence is trusted most.  Two
    # orderings were wrong when measured, and both are recorded because they look
    # reasonable written down:
    #
    #   * Abstract and title must be checked BEFORE the layout's heading label.  A title
    #     is short and set larger than the body, which is exactly the layout stage's own
    #     test for a heading, so it arrives here labelled "heading" — and trusting that
    #     label first typed all 8 corpus titles as headings.
    #   * The heading label must be checked BEFORE the bibliography tests.  A paragraph
    #     that cites several papers looks citation-like, and a heading is short enough to
    #     look like a short entry.

    # 2. The abstract, by its own keyword.  This is the most reliable signal in the file,
    #    so it goes first: "Abstract—We describe…" is also long, and a length-based title
    #    test would otherwise claim it.
    if looks_like_abstract_start(text):
        return TypeDecision(
            BlockType.ABSTRACT, Confidence(0.9, ConfidenceLevel.HIGH, "heuristic"),
            ["starts with Abstract"],
        )

    # 3. The title: only the top of page one, and only before the body starts.
    if looks_like_title(paragraph, body_size=body_size, page_index=page_index, position=position):
        reasons.append("top of the first page")
        if body_size and paragraph.font_size and paragraph.font_size > body_size * 1.12:
            reasons.append(f"font {paragraph.font_size:.1f} > body {body_size:.1f}")
            return TypeDecision(BlockType.TITLE, Confidence(0.85, ConfidenceLevel.HIGH, "heuristic"), reasons)
        return TypeDecision(BlockType.TITLE, Confidence(0.6, ConfidenceLevel.MEDIUM, "heuristic"), reasons)

    # 4. An author line, which follows the title.  Checked before the heading label for
    #    the same reason as the title.
    if position <= 6 and page_index == 0 and looks_like_author_line(text):
        return TypeDecision(
            BlockType.AUTHOR, Confidence(0.75, ConfidenceLevel.MEDIUM, "heuristic"),
            ["author-like line near the top"],
        )

    # 5. Bibliography, checked BEFORE the layout's heading label.  A bibliography entry is
    #    short and often set slightly larger, which is the layout stage's own test for a
    #    heading, so accepting that label first typed 5 corpus references as headings — and
    #    a reference read as a heading is translated, DOIs and author names included.
    if is_reference_like(text):
        return TypeDecision(
            BlockType.REFERENCE, Confidence(0.85, ConfidenceLevel.HIGH, "heuristic"),
            ["bibliography entry shape"],
        )

    if in_references_section(path) and not is_reference_section_heading(text):
        return TypeDecision(
            BlockType.REFERENCE, Confidence(0.8, ConfidenceLevel.HIGH, "heuristic"),
            ["inside a references section"],
        )

    # 6. A figure or table caption.  The layout stage never emits one — it has no notion of a
    #    caption — so before this rule existed every caption in the corpus came back as an
    #    ordinary paragraph (measured: 0 of 4).  Checked after the bibliography tests and
    #    before the heading label, because a caption is short and can be set larger than the
    #    body, which is the heading test; and after the abstract, because "Table 1. …" is not
    #    an abstract.  The clause guard keeps prose that merely mentions a table out of here.
    if _CAPTION_RE.match(text) and not _CAPTION_CLAUSE_RE.match(text):
        return TypeDecision(
            BlockType.CAPTION, Confidence(0.7, ConfidenceLevel.MEDIUM, "heuristic"),
            ["figure or table caption label"],
        )

    # 7. A footnote: small type low on the page.  Checked BEFORE the heading label, because
    #    a footnote is short and is therefore labelled a heading by the layout stage — the
    #    corpus footnote "2 Correspondence: author@example.invalid" arrives as a heading.
    #    Position plus type size is stronger evidence than shortness.  Without font
    #    information this cannot be told from a paragraph, and guessing would be worse.
    low_on_page = paragraph.bbox.y0 > FOOTNOTE_ZONE
    small = bool(body_size and paragraph.font_size and paragraph.font_size < body_size * 0.92)
    if low_on_page and small:
        return TypeDecision(
            BlockType.FOOTNOTE, Confidence(0.6, ConfidenceLevel.MEDIUM, "heuristic"),
            ["small type low on the page"],
        )

    # 8. A heading label from the layout stage.  The section's own heading reaches here,
    #    which is why the check above excludes it.
    is_heading = paragraph.kind == "heading" or paragraph.level is not None
    if is_heading:
        confidence = 0.9 if len(text) <= HEADING_MAX_CHARS else 0.6
        return TypeDecision(
            BlockType.HEADING,
            Confidence(confidence, ConfidenceLevel.HIGH if confidence >= 0.8 else ConfidenceLevel.MEDIUM, "layout"),
            ["layout labelled it a heading"],
        )

    # 9. A formula, which is kept verbatim.
    if is_formula_like(text):
        return TypeDecision(BlockType.FORMULA, Confidence(0.7, ConfidenceLevel.MEDIUM, "heuristic"), ["symbol density"])

    return TypeDecision(BlockType.PARAGRAPH, Confidence(0.8, ConfidenceLevel.HIGH, "layout"), ["default body block"])
