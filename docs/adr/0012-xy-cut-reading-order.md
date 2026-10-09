# ADR 0012: Reading order comes from a recursive whitespace cut, not from sorting by y

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Fixes: two-column pages were read left, right, left, right
* Related: ADR 0008 (the coordinate contract), ADR 0011 (a measurement defect that hid this one)

## Context

`segment_bands()` sorted every line on a page by vertical position and then split that
sequence wherever the vertical gap exceeded a multiple of the local line height. Each
resulting band was passed to `detect_columns()`, which looked for a gutter inside it.

On a page that mixes a full-width header with a two-column body this cannot work.
Sorting by y *interleaves* the title, both columns and the abstract, so a title spanning
the page centre ends up adjacent in the ordering to body lines from both columns. The
header lines then occupy the body's gutter, `detect_columns()` correctly refuses to split
a band whose gutter is not empty, the two-column body is measured as one column, and the
page is read left, right, left, right down to the foot.

The grouping has to happen *before* the ordering. That is the whole defect.

## What was measured

A matcher-independent measure was needed, because the existing acceptance metric could
not see this. `adjacent_order_accuracy` counts only adjacent pairs where **both** blocks
were matched, so a block the pipeline moved out of order and the matcher then failed to
pair is excluded from the score entirely. With the matcher itself fixed (ADR 0011 and the
same-page, vertical-plausibility constraints), the acceptance items read PASS — order
0.9858, region 0.9593 — while the pipeline was still emitting left, right, left, right.

`scripts/measure_reading_order.py` compares, per page, the reference sequence of words
against the pipeline's sequence of words with a longest-common-subsequence score, with no
matcher in the loop. That reported:

| | before | after |
| --- | --- | --- |
| `pdf_b_two_column` page 0 | 0.5376 | **0.9733** |
| `pdf_f_two_column_wide_table` page 0 | 0.5303 | **0.9722** |
| `pdf_h_no_references` page 0 | 0.5396 | **0.9792** |
| single-column pages | 1.0000 | 1.0000 |
| **pooled** | **0.8698** | **0.9915** |

Single-column pages were always 1.0000, which localised the fault to the column split
rather than to ordering in general.

## Decision

Replace the sort-then-split segmentation with a recursive whitespace cut (XY-cut). The
region is divided by its whitespace, alternating between horizontal cuts (a full-width
vertical gap, separating stacked blocks) and vertical cuts (a gutter, separating columns),
until nothing can be cut. The leaves, emitted depth-first, are the reading order.

Each leaf becomes one `Band`, so `analyse()` keeps its existing shape and `detect_columns()`
now almost always sees a single column.

Four details were each necessary, and each was found by measuring a wrong version:

1. **The vertical cut is tried first.** Splitting columns is the decision that must not be
   skipped; a vertical gap also appears inside a single column wherever a line ends early,
   so the *gap* test is the one needing a guard, not the one to avoid.
2. **Lines crossing the corridor form their own group.** Requiring every line to lie wholly
   on one side (`x1 <= cut` / `x0 >= cut`) leaves a side empty as soon as the corridor
   overlaps the start of the right column — the normal case, because one indented line
   drags the corridor off the true gutter. Partitioning by each line's *centre* instead
   forces a centred page title onto one side and puts it inside that column's text.
   Crossing lines are their own group, which is also what they are on the page.
3. **Two sides are columns only if they overlap vertically.** A single column has corridors
   wherever lines are short; cutting there fragmented a 101-line page from 33 paragraphs to
   8. Side-by-side columns overlap almost completely; stacked groups do not overlap at all.
4. **The cut's occupancy tolerance is looser than column detection's.** The cut only has to
   *separate* groups, and a few full-width lines crossing the centre is exactly what it must
   see through; requiring a clean corridor meant no cut fired and 101 lines came back as one
   region. Precision comes from the recursion isolating the spanning lines first.

## Alternatives considered

**A. Keep the band split and pre-classify lines by horizontal region.** Attempted first;
measured *worse* (order 0.8264, region 0.7729, 86 bands, new "ordering may be unreliable"
warnings) and was reverted. It needed special cases for spanning lines that the recursive
cut handles structurally.

**B. Improve `detect_columns()` instead** — relax its emptiness threshold, or weight the
projection profile by line length. Rejected: the information needed to separate the header
from the columns is *vertical*, and `detect_columns()` only sees one band. Fixing the band
boundaries is the correct level.

**C. Detect columns once for the whole page and order by column.** Rejected: it fails on
the pages that legitimately change structure partway down (an abstract spanning the top,
or a wide table interrupting a column), which the recursion handles.

**D. Accept the defect and note the limitation.** Rejected: reading order is what
translation anchoring, annotation and export all consume, so the defect would have been
copied into every downstream feature.

## Consequences

* Pooled reading order **0.8698 → 0.9915**; every page in the corpus is now ≥ 0.95.
* The measurement lesson is as important as the fix. Two metric defects had made a
  *broken* layout read as PASS. Any acceptance item whose metric only looks at matched
  items can be blinded by the matcher; the reading-order item now has an independent
  measure that no matcher can influence, and both are reported.
* `adjacent_order_accuracy` is kept for its per-document figures but is **not sufficient
  alone**, and that is stated where it is defined.
* Page-and-region accuracy reads 0.8952 and is not trusted for a related but separate
  reason (a paragraph's union box for the whole page versus the pipeline's per-page
  fragments); that is recorded in `docs/limitations.md` §3 rather than worked around.

## Verification

* `python scripts/measure_reading_order.py` — the pooled word-LCS figure above.
* `python scripts/verify_order_by_eye.py` — prints the reference order and the pipeline
  order side by side for a two-column page, so the sequences can be compared without any
  metric.
* `python scripts/measure_layout.py` — the per-document acceptance figures.
* `python -m pytest tests/unit` — 364 tests.
