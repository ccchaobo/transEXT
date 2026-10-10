# 0014. Calibrating the paragraph break against the corpus

Status: accepted
Date: 2026-10-10
Depends on: 0013 (which fixed the ordering half of the same defect)

## The defect

`assemble_paragraphs` groups ordered lines into paragraphs. `_same_column_flow` decided
whether the next line continued the current paragraph with two conditions that were both
wrong, in opposite directions:

```python
if gap < 0:
    return False
if gap > params.paragraph_gap_factor * typical_height:
    return False
```

1. **The lower bound was inverted.** Consecutive lines of one paragraph always have slightly
   overlapping boxes, because a line box covers ascender to descender while the leading is
   smaller than that — measured at −0.94 pt on this corpus, against a box 13.34 pt tall. So
   the guard that was supposed to catch the reading order stepping backwards *out of a column*
   fired at almost every line instead. On `pdf_b_two_column` page 0 the left column's 47 lines
   produced **33 breaks**, every one of them at a gap of exactly −0.94 pt. The case the guard
   exists for is a gap of about −700 pt, three orders of magnitude away.

2. **The upper bound could never fire.** `1.55 × 13.34 = 20.7 pt` is larger than every
   paragraph gap measured anywhere in the corpus. Combined with (1), the net effect was that
   step 1 split at almost every line, step 2's `_should_merge` re-joined the fragments, and the
   result was coarse blocks.

So the code produced roughly the right *number* of paragraphs for the wrong reasons, and a
reader could not pair a paragraph with its translation piece by piece. Only **11 of 76**
corpus text blocks were addressable on their own.

## Why the existing measures did not see it

Coverage, adjacent order, word-LCS and line order all measure whether text is present and in
sequence. Blocks that are too *coarse* preserve both. The page-and-region item did see it —
it sat at 0.8952 and was reported FAIL — but this repository had explained that figure away as
a matcher artefact (limitations 3). That explanation was wrong: it was the real defect, and
with no change to the matcher the item went to 1.0000 once the parameter was fixed.

## The calibration, and why not one page

`scripts/diagnose_merging.py` gave the mechanism. The replacement value cannot come from that
page: an earlier attempt at eager cutting, tuned on one page, collapsed a 101-line page from
33 paragraphs to 8. So `scripts/calibrate_paragraph_step.py` measured the two populations the
threshold has to separate across all 8 text PDFs and 17 pages, using the corpus's recorded
block boundaries as the answer key, and **restricted to same-column pairs**: a column jump is
already rejected by the horizontal-overlap test, so counting those pairs would flatter any
threshold by attributing to it a case it never sees.

| | within a block | between blocks (same column) |
| --- | --- | --- |
| gap | +2.40 pt, constant | p05 +4.16, median +6.90 |
| count | 509 | 268 |

Only **3 of 268** between-block gaps are ≤ 2.40 pt. The populations separate, and any
threshold in the valley works; the best single value is 2.40 pt with 3 of 777 pairs misjudged.
The default factor becomes **0.25**, i.e. 3.34 pt against this type size, sitting in the
valley rather than on its edge.

`scripts/sweep_paragraph_params.py` was then used to check the choice rather than to make it.
Its absolute rate could not be trusted — the denominator it reports is inconsistent with the
standalone measure, because the matcher can return more than one match per block — so the
value was confirmed against the independent measures in the table below instead. The sweep is
kept because its *ordering* of the configurations was informative: tighter was consistently
better, which is the direction the calibration predicts.

## Options compared

**A. Use the step (top-to-top) rather than the gap.** Rejected. The step separates worse here:
within-block is a constant 12.40 pt and the 5th percentile between blocks is 13.16 pt, a margin
of 0.76 pt, whereas the gap margins 2.40 against 4.16. This is because between-block gaps
involve two different type sizes, and the gap is the quantity that already accounts for that.

**B. Fix only the inverted lower bound.** Rejected as incomplete, and it would have looked
plausible in review. With the upper bound still at 20.7 pt, almost every line pair merges, so
the result is one paragraph per column — no better than what the two defects produced together,
and it would have left a provably dead condition in the code.

**C. Place the threshold in the measured valley — chosen.** Both bounds corrected. Effects,
on the same corpus with nothing else changed:

| measure | before | after |
| --- | --- | --- |
| corpus blocks addressable on their own | 11/76 (0.1447) | **87/175 (0.4971)** |
| page+region accuracy | 0.8952 FAIL | **1.0000 PASS** (333/333) |
| adjacent-block order | 0.9725 | **0.9969** |
| block-type accuracy | 13/13 (thin) | **87/87** |
| line-level order | 1.0000 | 1.0000 |
| body character coverage | 0.9985 | 0.9985 |

**D. Detect paragraph breaks by first-line indentation instead.** Rejected as insufficient on
its own for this corpus and worse in general: the corpus's paragraphs are separated by space
and not every one is indented, and a rule that splits on indent alone would split a hanging
indent inside one paragraph.

## What this does not settle

The corpus has a **constant** 12.40 pt leading and a 10.00 pt body box, so its
within-paragraph gap is a single point rather than a distribution. The valley is wider here
than it will be on papers with mixed leading, two-column figures and footnote blocks. The
factor 0.25 is calibrated against this corpus and must be re-calibrated against real
published articles before it is called settled. This is recorded in limitations 15.

## The lesson

The same lesson as 0013, from the other direction. There, a metric was too lenient and hid a
real fault. Here a metric reported a real fault correctly and this repository talked itself
out of it with a mechanism that *could* have explained the failures but did not. Plausibility
is not evidence: before a metric is declared untrustworthy, the proposed artefact has to be
shown to account for the failures, not merely to be capable of them.
