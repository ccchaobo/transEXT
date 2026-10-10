# 0015. A change of type size starts a new block

Status: accepted
Date: 2026-10-10
Follows: 0014 (which fixed the paragraph break by gap)

## The defect

After 0014 the paragraph break was right for body text, and one boundary was still wrong: a
section heading was merged into the paragraph beneath it. On `pdf_b_two_column` page 0,
`"1. Introduction Reading order and evidence tracking are pr…"` was a single block, and so were
`"2. Related Work …"`, `"3. Method …"` and `"4. Results …"`.

Two things followed, and both were already recorded as separate limitations:

* the **outline was useless** — `section_path` was `["<paper title>"]` for every block, because
  the real headings never reached the section stack (limitations 20), which also meant the
  context sent to the translation provider named nothing;
* the merged block **cited two papers**, so a numbered lead plus one citation marker typed whole
  sections of prose as bibliographies — which removes them from translation. That was found by
  running a real file, not by a metric, and is fixed separately.

Neither gap test could see the boundary, and this is the important part: a heading is set taller
than a body line, so `gap = below.y0 - above.y1` comes out **negative**, and the overlap
tolerance that 0014 introduced to stop over-splitting is exactly what accepts it. Tightening
that tolerance enough to reject the heading also rejects the normal overlap of two body lines.

## What was measured

`scripts/calibrate_heading_split.py` measured the two populations the rule has to separate,
over all 8 text PDFs and 17 pages, using the corpus's recorded block boundaries as the answer
key, and restricted to **same-column pairs** for the same reason as 0014 — the horizontal-overlap
test already handles a column jump.

| pair | ratio `below.font_size / above.font_size` |
| --- | --- |
| within a block (501 pairs) | constant **1.000**, min = max |
| heading → next block (86 pairs) | 0.618 – **0.926**, median 0.800 |

The populations are disjoint and the valley is `(0.926, 1.000)`. Every heading-to-next-block
pair is below it and **not one** within-block pair is. The corpus's own heading sizes explain
the 0.926: a 10.8 pt subheading over a 10 pt body, the narrowest change present.

## Options compared

**A. Tighten `line_overlap_tolerance` until the heading is rejected.** Rejected, and it is the
obvious thing to reach for. Rejecting the heading's overlap means rejecting the ordinary overlap
of two body lines, so it does not separate the cases: the measured body overlap is −0.94 pt and
the heading's is −1.6 pt, and no single tolerance in between is defensible on one page's
arithmetic.

**B. Scale the tolerance by the taller of the two boxes instead of the median.** Rejected by
arithmetic rather than by taste, which is why it is recorded: the heading gap is 1.6 pt and
0.15 × the taller box (14 pt) is 2.1 pt, so it still merges. It would have looked like a fix.

**C. Break when the type size changes by more than 3% — chosen.** In the middle of the measured
valley. Symmetric on purpose, so a heading absorbed into the paragraph *above* it is caught as
well; the corpus's between-block ratio reaches 1.562, so that case exists. A line with no font
information falls through to the gap tests rather than guessing a size.

Measured effect, same corpus, nothing else changed:

| measure | before | after |
| --- | --- | --- |
| corpus blocks addressable on their own | 87/175 (0.4971) | **235/246 (0.9553)** |
| block-type accuracy | 87/87 | **235/235** |
| page+region accuracy | 1.0000 (333 blocks) | **1.0000 (409 blocks)** |
| adjacent-block order | 0.9969 | **0.9975** |
| line-level order | 1.0000 | 1.0000 |
| body character coverage | 0.9985 | 0.9985 |

## A second defect the fix exposed, and the claim it refuted

Once headings were split off, `"1. Introduction"` was typed as the **title** in all 8 documents,
taking the heading score from 78/78 to 70/78. A numbered heading is short, is set larger than
the body, and — for the first section — sits near the top of page one, so `position` cannot
separate it from the title. `looks_like_title` now refuses a numbered section heading via the
existing `_SECTION_NUMBER_RE`, which restored 235/235.

This also refuted a claim this repository had written down: limitations 15 said the remaining
merged blocks were "finer than any threshold can recover" and needed "structure the layout stage
does not currently produce". The heading was recoverable from the type size, which the layout
stage was already carrying. That was an assumption stated as a finding.

## What this does not settle

The corpus has a constant 12.40 pt leading, a 10.00 pt body box and two or three type sizes. A
tolerance of 3% is calibrated against that. On real papers a paragraph can contain an inline
element reported at a different size, which this rule would split; the corpus has no such case
and cannot show whether that happens. Re-calibration against published articles is required
before either this tolerance or 0014's 0.25 is called settled.
