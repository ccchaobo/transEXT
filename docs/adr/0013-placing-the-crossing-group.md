# 0013. Placing the crossing group in the XY-cut

Status: accepted
Date: 2026-10-10
Supersedes part of: 0012

## The defect

`_split_regions` cuts a page recursively on whitespace. When a vertical cut finds a
corridor, it partitions the lines three ways — `left`, `right`, and `spanning` for lines
that cross the corridor — and emitted them in the fixed order `left + spanning + right`.

On a two-column paper's first page that order is wrong. The title, the author line and an
inset abstract cross the body's gutter, so they land in `spanning`; but they sit at the
*top* of the page, above both columns. Emitting `spanning` in the middle therefore placed
the page title after the whole left column.

Measured with `scripts/measure_line_order.py` (see below), page 0 of every two-column
corpus document was out of order and every other page was perfect:

| document | before | after |
| --- | --- | --- |
| `pdf_b_two_column` | 141 inverted pairs of 5050 (0.9721) | 0 |
| `pdf_f_two_column_wide_table` | 144 of 6216 (0.9768) | 0 |
| `pdf_h_no_references` | 138 of 6328 (0.9782) | 0 |
| all five other documents | 0 | 0 |
| pooled | 0.9872 | **1.0000** |

The inversion counts are exactly explained by the defect: `spanning` held 3 lines and the
left group 47, and 3 x 47 = 141.

Nothing else moved: body character coverage 0.9985 unchanged, block-type accuracy 13/13
unchanged, 494 unit tests unchanged.

## Why no existing measurement caught it

The word-LCS reading-order measure in the repo scored these pages at 0.9733 and passed.
LCS finds the longest common subsequence, so displacing a *short* block is nearly free:
dropping the 8-word title to keep the 200-word abstract costs about 1%. A metric that does
not notice the title is misplaced cannot be used to decide how a page should be cut.

`scripts/measure_line_order.py` was written for this. It measures the order of **lines**,
which are the atoms of the page: lines are matched between the truth and the pipeline by
bounding-box IoU, so a pipeline that merges blocks wrongly does not disturb the
measurement, and the reported figure is the fraction of line pairs in the same relative
order as the truth, which penalises displacing a short block exactly as much as a long one.
It calls `segment_bands` directly, so it measures the order the layout produced rather than
what later stages did with it.

## Options compared

**A. Try the horizontal cut first.** Implemented as `LayoutParams.cut_horizontal_first` and
measured both ways. Result: **identical** — 0.9872 either way. The horizontal cut does not
fire on these pages at all. On `pdf_b_two_column` page 0 the largest full-width gap is 4 of
240 rows (0.0140 normalised) against a required 1.9 x 0.01585 = 0.0301, so `_horizontal_cut`
returns None and the vertical cut runs in both orders. This refuted the hypothesis that the
cut *order* was at fault, and it is why the toggle did not fix anything on its own.

**B. Keep the fixed middle position** (the baseline). 0.9872 pooled, 141/144/138 inversions.

**C. Place the crossing group by its vertical centre — chosen.** Content whose vertical
centre lies above both columns reads first; content centred below both reads last; anything
else keeps the middle position. Pooled 1.0000, 0 inversions, no other metric moved.

The rule is positional rather than a special case for page one, so a full-width figure at
the top of a page reads first and one at the bottom reads last, without either being named.

## Rejected alternatives

**D. Compare the crossing group's top *edge* against the columns' top edges.** This was
implemented first and did fix `pdf_b` and `pdf_f` (141 and 144 inversions to 0), but left
`pdf_h_no_references` at 138. The reason is visible in `scripts/diagnose_cut.py`: a running
head is a single line that sits inside the left column's width and above everything else,
so it becomes the left group's topmost line at y=0.038, and the front matter at y=0.071 is
then not above the column. Comparing centres asks the question that matters — is this group
in the upper part of the page — and is insensitive to one stray line.

**E. Lower `band_gap_factor` so the running-head gap becomes cuttable.** Rejected. The gap
is 0.0215 against a required 0.0301, so lowering the factor to about 1.3 would let the
horizontal cut isolate the head first and option D would then work. But that is lowering a
threshold to make a particular case pass, which the project forbids, and it risks
over-splitting: an earlier attempt to cut more eagerly collapsed a 101-line page from 33
paragraphs to 8. Option C fixes the same case without touching a threshold.

**F. Exclude running heads from the layout before ordering.** They are only recognisable as
furniture by repetition across pages, which is a document-level decision made after the
layout runs, so this would reorder the pipeline for a cosmetic gain.

## A documentation defect fixed at the same time

`_split_regions`'s docstring claimed "a horizontal cut is tried first" while the code tried
the vertical cut first, and the comment above the vertical cut argued for that choice. The
code and the comment were the ones that were wrong about the consequence; the docstring's
reasoning turned out to be right, and `cut_horizontal_first` now defaults to `True` so the
code matches it. The measurement above is what settled which was right, not the prose.

## The lesson

This is the third time in this project that a metric defect and a product defect were
confused, and the first time the direction was reversed: here the *old* metric was too
lenient and was hiding a real defect, rather than a metric defect masquerading as a product
defect. Both directions were only distinguishable by measuring something the metric could
not see — line order rather than word order — before changing the product.

The defect was also found by running a real file end to end, not by any metric. Every
change from here should be re-checked the same way.
