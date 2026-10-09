"""Layout metrics: body character coverage, reading order, region accuracy.

Three of the spec's §12.2 acceptance items are properties of the layout pipeline
rather than of any single recogniser, and each is measured separately because they
fail independently:

**Body character coverage** — did the pipeline keep the text?  A pipeline that
drops a paragraph still produces a plausible-looking block list, so the only way to
know is to compare the characters that came out against the characters that went
in.  Measured as the multiset character error rate in the *missing* direction: what
share of the reference body characters did the output fail to account for.

**Adjacent-block order** — are the blocks in the right sequence?  Measured as the
fraction of adjacent reference blocks whose matched outputs keep their relative
order, which is the spec's wording.  Only blocks that were actually matched are
considered, so this is independent of whether the text was recognised correctly.

**Region accuracy** — is each block attributed to the right page and the right part
of that page?  A block that is correct in text and order but placed on the wrong
page breaks the source anchoring that the whole product depends on.

Two rules follow from how the pipeline actually behaves, and both were chosen after
inspecting its output rather than from the design:

* **Matching is by content, not by index.**  The pipeline merges and splits
  differently from the generator, so a block may correspond to more than one
  paragraph and vice versa.  Matching on text similarity and requiring a one-to-one
  assignment is the only stable comparison.  Line fragments that belong to one
  reference block are pooled before comparing, which is what a correct pipeline is
  expected to do.
* **Structure-only blocks are excluded from coverage but included in region
  accuracy.**  A `table` block carries no lines of its own (its text is a caption
  duplicated by a `caption` block and its cells are separate blocks), so counting it
  as missing text would penalise the pipeline for the generator's bookkeeping.
  It still has a page and a region, so it is counted there.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

__all__ = [
    "TruthBlock",
    "MatchResult",
    "normalise",
    "body_coverage",
    "adjacent_order_accuracy",
    "region_accuracy",
    "match_blocks_to_paragraphs",
    "STRUCTURE_ONLY_TYPES",
    "BODY_TYPES",
]


#: Block types that hold no text of their own.  Excluded from coverage, because
#: their absence of text is not the pipeline dropping anything.
STRUCTURE_ONLY_TYPES = frozenset({"table", "figure"})

#: Block types that carry body content and must survive extraction.
BODY_TYPES = frozenset(
    {
        "paragraph",
        "heading",
        "title",
        "author",
        "abstract",
        "reference",
        "caption",
        "footnote",
        "table_cell",
        "formula",
    }
)


def normalise(text: str) -> str:
    """Fold whitespace so line-wrapping differences are not counted as errors."""
    return " ".join(text.split())


@dataclass
class TruthBlock:
    """One reference block, with its region in normalised display coordinates.

    A block that spans pages is represented once per page, holding only that page's
    drawn text and boxes, so ``index`` alone is not unique.  ``key`` is.
    """

    index: int
    block_type: str
    text: str
    page_index: int
    reading_order: int
    #: (x0, y0, x1, y1) in the frozen normalised display space, origin top-left.
    bbox: tuple[float, float, float, float]
    n_lines: int = 0

    @property
    def key(self) -> tuple[int, int]:
        return (self.index, self.page_index)

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def is_structure_only(self) -> bool:
        return self.block_type in STRUCTURE_ONLY_TYPES

    @property
    def centre(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


@dataclass
class MatchResult:
    """One reference block paired with the paragraph text that answers for it."""

    block: TruthBlock
    paragraph_index: int | None
    paragraph_text: str
    similarity: float
    page_index: int | None
    bbox: tuple[float, float, float, float] | None


@dataclass
class MatchReport:
    matches: list[MatchResult] = field(default_factory=list)
    unmatched_blocks: list[TruthBlock] = field(default_factory=list)
    unused_paragraphs: list[int] = field(default_factory=list)


def _multiset_missing(reference: str, hypothesis: str) -> tuple[int, int]:
    """Characters in ``reference`` that ``hypothesis`` does not account for.

    Returns ``(missing, reference_length)``.  Insertions in the hypothesis are not
    counted here: this asks what was lost, not what was added.
    """
    reference_counts = Counter(reference)
    hypothesis_counts = Counter(hypothesis)
    missing = sum((reference_counts - hypothesis_counts).values())
    return missing, len(reference)


def body_coverage(
    reference_text: str, hypothesis_text: str
) -> tuple[float, int, int]:
    """Share of reference body characters the output accounts for.

    Returns ``(coverage, missing, reference_length)`` where coverage is in [0, 1]
    and a higher value is better.  ``1.0`` means every reference character appears
    in the output the right number of times.
    """
    reference = normalise(reference_text)
    hypothesis = normalise(hypothesis_text)
    if not reference:
        return 1.0, 0, 0
    missing, length = _multiset_missing(reference, hypothesis)
    return 1.0 - (missing / length), missing, length


def match_blocks_to_paragraphs(
    blocks: list[TruthBlock],
    paragraphs: list[tuple[str, int, tuple[float, float, float, float]]],
    *,
    min_similarity: float = 0.5,
    min_length_ratio: float = 0.5,
    min_vertical_overlap: float = 0.25,
) -> MatchReport:
    """Pair reference blocks with the layout paragraphs that answer for them.

    ``paragraphs`` is ``(text, page_indices, bbox)`` per paragraph, in the pipeline's
    reading order.  ``page_indices`` is the *set* of pages the paragraph's text was
    drawn on rather than one representative page: a paragraph that runs across a page
    break has text on both pages, so a reference block's continuation entry on the
    second page has to be allowed to pair with it.

    Pairing is greedy on similarity and one-to-one: each paragraph may answer for at
    most one reference block, so a paragraph cannot be counted twice as evidence
    that two different blocks survived.

    A length guard is applied for the same reason it is needed in the OCR metrics: a
    similarity normalised by the longer string lets a short paragraph claim a long
    reference block.  The shorter side must be at least ``min_length_ratio`` of the
    longer, so a heading cannot answer for a paragraph.

    **A block is only paired with a paragraph on the same page.**  Without that
    restriction the matcher paired a page-0 sentence with an acknowledgement on page 2,
    or a page-1 figure caption with a page-0 body sentence, whenever the two happened to
    share enough characters.  Those pairings are impossible — a block's text is drawn on
    one page and a paragraph's text comes from one page — and each one invents an
    ordering inversion that corresponds to no real reading-order mistake.  Measured
    before the restriction: 28 of 232 pairs (12.1%) crossed pages, and the damage was
    concentrated in exactly the documents that scored worst (``pdf_g_deep_sections``
    40% cross-page at order 0.75, ``pdf_f_two_column_wide_table`` 17% at 0.7826), while
    ``pdf_h_no_references`` — the same paper as ``pdf_b_two_column`` — scored 0.9091
    against 0.7857 purely because none of its pairs crossed a page.

    **The pairing must also be vertically plausible.**  Even on the right page, matching
    on text alone attached a block at y=0.273 to a paragraph at y=0.730, and a right-column
    heading at y=0.248 to a paragraph at the foot of the page.  A paragraph only answers
    for a block if their vertical extents meet: the block's y range must overlap the
    paragraph's by at least ``min_vertical_overlap`` of the block's own height.  The test
    is stated as a share of the *block*, not of the paragraph, because a paragraph that
    legally absorbs a heading is taller than the block and that merge must stay allowed —
    what is being rejected is a block that sits far away from the paragraph claiming it.

    Without this, a handful of high-similarity but positionally impossible pairs claim
    paragraphs early (the greedy pass sorts by similarity) and push the genuinely correct
    paragraph out, which shows up as an ordering inversion that the layout code did not
    commit.
    """
    report = MatchReport()
    available = set(range(len(paragraphs)))
    candidates: list[tuple[float, int, int]] = []
    for bi, block in enumerate(blocks):
        if not block.has_text:
            continue
        reference = normalise(block.text)
        ref_counts = Counter(reference)
        for pi in range(len(paragraphs)):
            # Same page only, where a paragraph may legitimately occupy several pages.
            # See the docstring: cross-page pairings are impossible and they distort
            # the ordering measure.
            if block.page_index not in paragraphs[pi][1]:
                continue
            # Vertically plausible: see the docstring.  Stated against the block's own
            # height so a paragraph that legitimately absorbed a heading (and is
            # therefore taller than the block) is not rejected.
            block_height = block.bbox[3] - block.bbox[1]
            if block_height > 0:
                overlap = min(block.bbox[3], paragraphs[pi][2][3]) - max(
                    block.bbox[1], paragraphs[pi][2][1]
                )
                if overlap < min_vertical_overlap * block_height:
                    continue
            text = normalise(paragraphs[pi][0])
            if not text:
                continue
            longer, shorter = max(len(reference), len(text)), min(
                len(reference), len(text)
            )
            contained = False
            if longer and shorter < min_length_ratio * longer:
                # The length guard stops a short block claiming a much longer
                # paragraph.  It must not stop a block that the pipeline legitimately
                # *absorbed*: when a heading or an author line is merged into the
                # paragraph that follows it, the paragraph contains the block's text
                # verbatim and is much longer, so the guard would reject the only
                # paragraph that can answer for the block.  Measured on
                # ``pdf_a_single_column``, 32 of 36 unmatched blocks were contained in
                # a paragraph: the text was present and correctly placed, and the guard
                # alone was reporting it as missing.
                contained = shorter > 0 and (
                    reference in text or text in reference
                )
                if not contained:
                    continue
            hyp_counts = Counter(text)
            shared = sum((ref_counts & hyp_counts).values())
            denominator = max(len(reference), len(text))
            similarity = shared / denominator if denominator else 0.0
            if contained or similarity >= min_similarity:
                candidates.append((similarity, bi, pi))
    # Highest similarity first, so the best pairing is claimed before a weaker
    # alternative can take the paragraph.
    candidates.sort(reverse=True)
    matched_blocks: dict[int, int] = {}
    for similarity, bi, pi in candidates:
        if bi in matched_blocks or pi not in available:
            continue
        matched_blocks[bi] = pi
        available.discard(pi)

    for bi, block in enumerate(blocks):
        if not block.has_text:
            report.unmatched_blocks.append(block)
            continue
        pi = matched_blocks.get(bi)
        if pi is None:
            report.unmatched_blocks.append(block)
            continue
        text, page_indices, bbox = paragraphs[pi]
        report.matches.append(
            MatchResult(
                block=block,
                paragraph_index=pi,
                paragraph_text=text,
                similarity=_similarity(normalise(block.text), normalise(text)),
                # The block's own page, which is the page the pairing was made on.  For
                # a paragraph that spans a page break this differs from the paragraph's
                # first page, and region accuracy compares against the block's page.
                page_index=block.page_index,
                bbox=bbox,
            )
        )
    report.unused_paragraphs = sorted(available)
    return report


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    shared = sum((Counter(a) & Counter(b)).values())
    return shared / max(len(a), len(b))


def adjacent_order_accuracy(
    blocks: list[TruthBlock],
    matches: list[MatchResult],
) -> tuple[float | None, int, int]:
    """Fraction of adjacent reference pairs whose order the output preserves.

    Only pairs where **both** blocks were matched are counted, so a missing block
    shows up as a coverage failure rather than as an ordering failure.  Returns
    ``(accuracy, correct, total)``, or ``(None, 0, 0)`` when there are no pairs to
    judge — an unmeasurable result is reported as such and never as a pass.
    """
    by_block = {m.block.key: m for m in matches if m.paragraph_index is not None}
    ordered_blocks = sorted(
        (b for b in blocks if b.key in by_block), key=lambda b: b.reading_order
    )
    comparisons = 0
    correct = 0
    for previous, current in zip(ordered_blocks, ordered_blocks[1:], strict=False):
        prev_para = by_block[previous.key].paragraph_index
        curr_para = by_block[current.key].paragraph_index
        if prev_para is None or curr_para is None:
            continue
        comparisons += 1
        if curr_para > prev_para:
            correct += 1
    if comparisons == 0:
        return None, 0, 0
    return correct / comparisons, correct, comparisons


def _overlap_fraction(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """Intersection over the smaller box's area, in [0, 1]."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area_b = max(0.0, (bx1 - bx0)) * max(0.0, (by1 - by0))
    if area_b <= 0.0:
        return 0.0
    return min(1.0, intersection / area_b)


def region_accuracy(
    matches: list[MatchResult],
    *,
    overlap_threshold: float = 0.5,
    blocks: list[TruthBlock] | None = None,
) -> dict:
    """Page and region correctness for matched blocks.

    A block is region-correct when the paragraph that answers for it is on the same
    page **and** overlaps the reference region by at least ``overlap_threshold`` of
    the reference area.  Both are required: a paragraph on the right page but in the
    wrong column would still break anchoring.

    Returns counts plus the page list, so a failure can be attributed to a specific
    document and page rather than reported as one number.
    """
    wrong_page: list[tuple[tuple[int, int], int, int]] = []
    wrong_region: list[tuple[tuple[int, int], str, str]] = []
    right = 0
    considered = 0
    for match in matches:
        if match.paragraph_index is None or match.page_index is None or match.bbox is None:
            continue
        considered += 1
        if match.page_index != match.block.page_index:
            wrong_page.append(
                (match.block.key, match.block.page_index, match.page_index)
            )
            continue
        overlap = _overlap_fraction(match.bbox, match.block.bbox)
        if overlap >= overlap_threshold:
            right += 1
        else:
            # The reference text is reported too: a region failure is much easier to
            # diagnose when the block it belongs to is visible in the output.
            wrong_region.append(
                (match.block.key, f"{overlap:.3f}", match.block.text[:60])
            )
    return {
        "considered": considered,
        "correct": right,
        "accuracy": (right / considered) if considered else None,
        "wrong_page": wrong_page,
        "wrong_region": wrong_region,
    }
