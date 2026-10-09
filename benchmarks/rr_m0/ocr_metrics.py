"""Text-comparison metrics for OCR evaluation.

Two distinct quantities are needed, and conflating them produced a wrong result
once already:

**Recognition accuracy** asks whether the glyphs were read correctly.  It must
not depend on the order in which lines are reported, because line order is the
layout module's job (spec §8 assigns reading order to ``document_core.layout``,
not to the recogniser) and because a two-column page's ground truth has a
different natural line order from a left-to-right render scan.

**Reading-order accuracy** asks whether the lines came back in the intended
sequence.  It is measured separately.

An earlier harness compared a whole page -- up to 96 lines, or 4125 characters --
against a reference built by concatenating the page's blocks in semantic order
into a single string.  On a single-column page that happened to look like one
long line; on a two-column page it guaranteed a misalignment and reported a
character error rate of 0.77 for text that was, character by character, about 96%
correct.  The metrics here keep the two questions apart.
"""

from __future__ import annotations

from collections import Counter

__all__ = [
    "normalise",
    "edit_distance",
    "character_error_rate",
    "multiset_character_error_rate",
    "align_lines",
    "reading_order_score",
    "line_similarity",
]


def normalise(text: str) -> str:
    """Fold away differences that are not recognition errors.

    Whitespace runs collapse to a single space because line-wrapping position is
    not part of the text, and the comparison is performed on whitespace-collapsed
    content throughout.
    """
    return " ".join(text.split())


def edit_distance(reference: str, hypothesis: str) -> int:
    """Levenshtein distance with a rolling row, so memory is O(len(reference))."""
    if reference == hypothesis:
        return 0
    if not reference:
        return len(hypothesis)
    if not hypothesis:
        return len(reference)
    previous = list(range(len(hypothesis) + 1))
    for i, ref_char in enumerate(reference, start=1):
        current = [i]
        for j, hyp_char in enumerate(hypothesis, start=1):
            cost = 0 if ref_char == hyp_char else 1
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over reference length.

    This is the strict measure, and it is only meaningful when the two strings are
    in the same line order.  Use :func:`multiset_character_error_rate` when line
    order is not part of the question.
    """
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return edit_distance(reference, hypothesis) / len(reference)


def multiset_character_error_rate(reference: str, hypothesis: str) -> float:
    """Character error rate that ignores the order of characters.

    Characters are pooled and compared as multisets, so the score answers exactly
    "were the right characters read the right number of times" without being
    sensitive to whether lines were reported top-to-bottom, column by column, or
    in any other sequence.

    This is a genuine weakening of the strict metric and is reported as such: a
    recogniser that returned every line in a scrambled order would score 0 here
    while scoring poorly on the strict measure.  That is why reading order is
    measured separately by :func:`reading_order_score` rather than quietly
    folded into this number.
    """
    reference = normalise(reference)
    hypothesis = normalise(hypothesis)
    if not reference:
        return 0.0 if not hypothesis else 1.0
    reference_counts = Counter(reference)
    hypothesis_counts = Counter(hypothesis)
    # Characters present in the hypothesis but not the reference are insertions;
    # characters missing from the hypothesis are deletions.  Substitutions appear
    # as one of each, which matches how substitutions are counted per character.
    insertions = sum((hypothesis_counts - reference_counts).values())
    deletions = sum((reference_counts - hypothesis_counts).values())
    return (insertions + deletions) / len(reference)


def align_lines(
    reference_lines: list[str], hypothesis_lines: list[str]
) -> tuple[float, list[tuple[str, str, str]]]:
    """Order-insensitive line-level alignment.

    Returns the matched fraction and the list of ``(tag, reference, hypothesis)``
    pairs, where the tag is one of ``match``, ``reference_only`` or
    ``hypothesis_only``.

    Lines are paired by best character overlap rather than by position, so a
    page whose columns were reported in a different order still matches.
    """
    remaining = list(range(len(hypothesis_lines)))
    pairs: list[tuple[str, str, str]] = []
    matched = 0
    for reference in reference_lines:
        best_index = None
        best_score = 0.0
        for index in remaining:
            score = line_similarity(reference, hypothesis_lines[index])
            if score > best_score:
                best_score = score
                best_index = index
        if best_index is not None and best_score >= 0.5:
            remaining.remove(best_index)
            matched += 1
            pairs.append(("match", reference, hypothesis_lines[best_index]))
        else:
            pairs.append(("reference_only", reference, ""))
    for index in remaining:
        pairs.append(("hypothesis_only", "", hypothesis_lines[index]))
    total = max(1, len(reference_lines))
    return matched / total, pairs


def reading_order_score(
    reference_lines: list[str], hypothesis_lines: list[str], *, min_pairs: int = 2
) -> float | None:
    """Fraction of adjacent matched reference lines that keep their relative order.

    Only lines that were actually matched are considered, so this measures order
    independently of recognition quality.

    Returns ``None`` when fewer than two reference lines can be matched, because a
    single position has no adjacent pair and the score is undefined.  ``min_pairs``
    cannot be lowered below 2 for that reason, and attempting to is a programming
    error rather than a configuration choice.

    **An earlier version returned ``1.0`` when unmeasurable**, which was a defect: a
    two-column page whose reference held one flattened paragraph against 96
    recognised lines produced a perfect reading-order score while measuring nothing
    at all.  A perfect score for an unmeasurable quantity is worse than no score,
    because it is indistinguishable from a real pass.  Callers must handle ``None``
    as "not measured" rather than as success.
    """
    if min_pairs < 2:
        raise ValueError(
            "min_pairs must be at least 2: an order score needs an adjacent pair"
        )
    positions: list[int] = []
    remaining = list(range(len(hypothesis_lines)))
    for reference in reference_lines:
        best_index = None
        best_score = 0.0
        for index in remaining:
            score = line_similarity(reference, hypothesis_lines[index])
            if score > best_score:
                best_score = score
                best_index = index
        if best_index is not None and best_score >= 0.5:
            remaining.remove(best_index)
            positions.append(best_index)
    if len(positions) < min_pairs:
        return None
    increasing = sum(
        1 for a, b in zip(positions, positions[1:], strict=False) if b > a
    )
    return increasing / (len(positions) - 1)


def _overlap(a: str, b: str) -> float:
    """Dice similarity in [0, 1], order-insensitive and cheap."""
    a, b = normalise(a), normalise(b)
    if not a or not b:
        return 0.0
    counts_a, counts_b = Counter(a), Counter(b)
    shared = sum((counts_a & counts_b).values())
    return 2.0 * shared / (len(a) + len(b))


def _containment(shorter: str, longer: str) -> float:
    """Fraction of ``shorter``'s characters that also occur in ``longer``."""
    if not shorter:
        return 0.0
    counts_short, counts_long = Counter(shorter), Counter(longer)
    return sum((counts_short & counts_long).values()) / len(shorter)


def line_similarity(a: str, b: str, *, min_ratio: float = 0.5) -> float:
    """Similarity between two *lines*, with a length guard.

    Dice similarity alone is not enough to pair lines.  It is normalised by the
    total length, so a short line that appears inside a long one still scores
    highly: ``"paragraph"`` matched a whole reference paragraph at 0.55, which
    passed the pairing threshold and made a page whose reference held one line per
    paragraph look as though its reading order had been measured.  That is how a
    vacuous "order = 1.0" was produced.

    Two conditions are required, and both are necessary:

    * the shorter line's characters must be at least ``0.8`` contained in the
      longer one, so a genuine match with OCR errors still qualifies;
    * the shorter line must be at least ``min_ratio`` of the longer line's length,
      so a fragment cannot match a passage.

    Both thresholds are loose enough that a correctly recognised line always
    matches its reference, which is what the acceptance metric depends on.
    """
    a, b = normalise(a), normalise(b)
    if not a or not b:
        return 0.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < min_ratio * len(longer):
        return 0.0
    if _containment(shorter, longer) < 0.8:
        return 0.0
    return _overlap(a, b)
