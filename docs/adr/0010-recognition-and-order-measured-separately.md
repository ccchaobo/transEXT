# ADR 0010: Recognition accuracy and reading order are measured separately

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Refines: spec §12.2
* Related: ADR 0004 (the engine this is used to evaluate)

## Context

The spec sets a recognition threshold (a provisional 5% character error rate on
clear scans) and a separate structural threshold (adjacent-block order ≥ 95%).
Both have to be measured against something, and the something is a labelled corpus.

Three defects occurred while building that measurement, and all three produced
**confident wrong numbers** rather than errors:

1. **One metric asked two questions.** A whole page — up to 96 lines, 4125
   characters — was compared against a reference built by concatenating the page's
   blocks in *semantic* order into one string. On a single-column page that happens
   to resemble one long line. On a two-column page it guarantees misalignment, and
   the harness reported a character error rate of **0.77** for text that was
   character-by-character about 96% correct. The engine was blamed for a defect in
   the metric.

2. **An unmeasurable score returned a perfect value.** The reading-order metric
   returned `1.0` when fewer than two lines could be matched. Combined with defect
   3, this meant every page reported a reading-order score of exactly 1.0 —
   including pages where nothing had been compared.

3. **Ground truth had no line structure.** Each paragraph was recorded as a single
   flattened string, so a two-column page holding ~100 drawn lines was recorded as
   ~20 one-line paragraphs. Line-level alignment against it was impossible.

Defects 2 and 3 were mutually reinforcing: the ground truth made the comparison
impossible, and the metric reported the impossible comparison as perfect. Neither
would have been noticed from the output alone, because a perfect score looks like
success.

## Decision

**Measure recognition and order as two separate quantities, and make "not
measured" a representable outcome.**

* **Recognition** is the multiset character error rate: characters are pooled and
  compared as multisets, so it answers "were the right characters read the right
  number of times" without depending on line order. Order is explicitly not this
  metric's business, because spec §8 assigns reading order to
  `document_core.layout`, not to the recogniser.
* **Reading order** is the fraction of adjacent *matched* reference lines that keep
  their relative order. It returns `None` — not `1.0` — when fewer than two lines
  can be matched, and callers must report that as not measured.
* **Line pairing requires a length guard**, not just similarity. Similarity is
  normalised by total length, so a short line inside a long one scores highly:
  `"paragraph"` matched a whole paragraph at 0.55 and passed the pairing threshold.
  A pair now requires the shorter line to be at least half the longer one's length
  **and** 80% contained within it. Both thresholds are loose enough that a
  correctly recognised line always pairs with its reference.
* **Ground truth records drawn lines.** `visible_lines` is the sequence of lines
  actually drawn, in reading order; `visible_text` remains the flattened form for
  character comparison. Tests assert the two agree in content, so they cannot drift
  into describing different pages.

## Alternatives considered

**A. One combined score.** A single "page accuracy" number is easier to report.
Rejected: it cannot distinguish a recogniser that misreads characters from one that
returns correct text in the wrong order, and those two failures need opposite
responses. It is also what produced the 0.77 false alarm.

**B. Strict edit distance on the concatenated page text.** The most familiar
metric. Rejected as the *primary* measure: it conflates character accuracy with line
sequencing, so it is only meaningful when the two strings are in the same order —
which for a two-column page they are not. It is retained and reported as
`cer_strict`, and it is the figure that still looks bad (0.75) on column pages. That
is expected and it is why it is not the acceptance metric: the reference lines are
in visual order while the recogniser emits a row-major walk across columns.

**C. Score reading order from the recogniser's output.** Simple. Rejected as the
*acceptance* measure: the recogniser's output sequence is an input to layout, not
the reading order the reader sees. It is reported anyway, because it is real
evidence about the engine, and it is labelled as the recogniser's sequence so it
cannot be mistaken for the product's reading order.

**D. Have the recogniser reorder lines itself.** Superficially attractive, because
it would make the recogniser's sequence look good. Rejected: it duplicates the
layout module's job in a place that has no page model, puts column detection in the
wrong layer, and would hide layout failures behind a recogniser that had already
guessed.

**E. Two separate metrics with an explicit "not measured" outcome.** Chosen.

## Consequences

* The acceptance table now separates the two rows, and a reader can see that
  recognition passes while order does not.
* `reading_order_score` returning `None` means every caller must decide what to do
  with it. That is deliberate: silently substituting a value is how defect 2
  happened.
* The corpus is larger, because each page now carries its drawn lines as well as
  its flattened text. This is worth it: without line structure the order metric
  cannot be computed at all.
* The layout module's reading order is now a *separate* measurement that has not
  been made yet, and is listed as PENDING. Making the distinction visible turned an
  unexamined assumption into a named gap.
* A metric change can invalidate previously reported figures. Both defects here
  invalidated a published number, so metric changes are recorded rather than
  quietly corrected.

## Verification

* `python -m pytest tests/unit/test_ocr_metrics.py` — 39 tests, including the
  specific regressions: order is not `1.0` when unmeasurable, `min_pairs=1` is
  refused rather than dividing by zero, and a word does not match a paragraph.
* `python -m pytest tests/unit/test_corpus_truth.py` — 13 tests, including that
  every page records `visible_lines`, that `visible_text` and `visible_lines` agree,
  and that a page of prose is not flattened into fewer than 10 lines.
* `python scripts/spike_ocr.py` — prints recognition and order per page, and states
  explicitly which pages could not be measured.
