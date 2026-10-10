# Known limitations

This document states what the software does not do, and what has not been
verified. It exists so that no claim in the evidence tables has to be read
generously. Each item names the bound and, where possible, how it was measured.

## 1. The corpus is synthetic

Every document in `benchmarks/corpus/` is authored by this repository's own
generator. No third-party papers are included.

**What this means.** The tests can make exact claims about text, layout class,
reading order and coordinates, because the ground truth was recorded at draw
time. They cannot make claims about *real-world* diversity: no publisher macros,
no multi-column journal templates with sidebars, no ligature-heavy legacy
typesetting, no equations from a real typesetting system, no handwriting, no
photocopied scans from the 1990s.

**Bounded consequence.** Reading-order, character-coverage and OCR numbers
measured here are properties of *this generator's output* under *this*
degradation pipeline. They are evidence that the implementation handles the
cases the corpus encodes, not evidence about the general population of research
PDFs.

**Status:** this is a limitation, not a defect. Obtaining a redistributable real
corpus is a PENDING item.

## 2. Translation quality is unverified

No translation provider credential exists in this environment. Per the spec,
credential-independent work was done and the real-call acceptance item is
**PENDING**. No mock translation is used to claim completion, and no
translation-quality figure is reported.

**What is verified:** the adapter interface, the cache key composition, the job
and retry state machine, partial-completion accounting, and the anchoring
contract that a translation must satisfy.

**What is not verified:** that a real provider's output is accurate, that cost
estimates match real billing, and that retry and rate-limit behaviour matches a
real service. The human review of at least 100 paragraphs required by the spec
cannot begin without one.

## 3. Page-and-region accuracy — resolved, and an earlier explanation of it was wrong

The acceptance item "≥ 200 sampled blocks, ≥ 95% attributed to the correct page and region"
now reads **1.0000** (333/333 blocks). It is recorded here because the earlier reading of it
was mistaken.

It previously read **0.8952** (205/229), and this document explained that figure away as an
artefact of the matcher — a block being compared against a paragraph's whole-page union box
while the pipeline emitted per-page fragments. That explanation was **wrong**. The metric
was not measuring an artefact; it was correctly detecting the paragraph over-merging
described in 15. Once paragraph breaks were calibrated (ADR 0014), the same metric on the
same corpus, with no change to the matcher, went to 1.0000 over *more* sampled blocks
(229 → 333).

The distinction matters because the artefact story was specific and plausible — a split
reference really is compared against the wrong fragment's box — and it was still not the
cause of the failure. A metric should not be declared untrustworthy on the strength of a
mechanism that *could* explain it; that requires showing the mechanism accounts for the
failures. This is the fourth time in this project that a metric and a product defect were
confused, and the first time the metric was simply right.

**Status:** PASS, and no longer reported as untrusted.

## 4. DOCX conversion is a declared degraded path

See [ADR 0005](adr/0005-docx-conversion-path.md) for the full list. In summary,
these are **not** reproduced, and each is reported as a user-visible issue on the
document rather than silently dropped:

* text boxes, shapes and WordArt (text genuinely missing — severity `error`),
* floating and wrapped object placement,
* tracked changes, comments and revision marks,
* Word's pagination (the product uses its own rendering and says so),
* the document's fonts, numbering scheme, and equation layout.

## 5. Frontend verification is not automated

This environment has no browser automation and no headless browser. There is no
end-to-end UI test.

**What is verified:** DOM-contract unit tests against the frontend modules, the
wire contract on the server, and documented manual steps for the interactive
behaviour.

**What is not verified:** that the two panes scroll in step in a real browser,
that highlighting lands on the intended rectangle at every zoom level, and that
the layout is usable at small window sizes. These require a browser and a human,
and they are listed as manual steps rather than claimed as covered.

## 6. Real scanning conditions are not represented

The scanned documents are produced by rasterising a born-digital PDF and applying
a modelled degradation (JPEG compression, Gaussian blur, additive noise,
speckle, skew, vignette). Real scans additionally exhibit:

* bleed-through from the reverse side,
* page curvature near the binding,
* halftone patterns from printing,
* inconsistent exposure across the page,
* staples, punch holes, handwritten annotations, and marginalia.

Only 2° and 1.8° skew is represented, which is mild; real skewed scans commonly
reach 3–5°.

## 7. Coordinate accuracy is bounded by the parser, not asserted

The coordinate contract is tested against page geometry that the PDF file itself
declares (media box, crop box, rotation), which is an independent check. Content
positions are checked against draw-time ground truth.

**What is not verified:** that a user clicking a translated sentence lands on the
correct *line* within a paragraph. Block-level anchoring is what is implemented
and tested. Sub-block anchoring would need a finer contract and is not claimed.

## 8. Page-level location is never presented as paragraph-level

Following the spec, a page-level result is always described as page-level. The
reader's location indicators name a page and a region, not a sentence. Where a
block's extraction is uncertain, the uncertainty is surfaced as a quality issue
rather than presented as a precise anchor.

## 9. Encrypted documents are refused, not cracked

An encrypted PDF is detected and reported. No password prompt, no dictionary
attack, and no attempt to extract partial content is made. This is a product
boundary, not a technical gap.

## 10. Deletion scope

Deleting a document removes its database rows, its original file, its derived
resources and its caches. **Backups and any copies the user made outside the
application are explicitly not covered**, and the confirmation text says so.
A backup policy is a separate concern that is not implemented.

## 11. PyMuPDF licence obligation

PyMuPDF is dual licensed AGPL-3.0 / Artifex commercial. This is an obligation on
*distribution*, not on development or internal use. It is recorded in
[ADR 0003](adr/0003-pymupdf-for-pdf.md) and in the dependency inventory, and it
must be re-checked before any commercial or network-service distribution. The
library is confined behind the `DocumentParser` interface so it can be replaced.

## 12. Performance figures are for this machine only

Timings describe the machine and versions recorded in the environment inventory.
They are not portable, and a slower machine or a cold page cache will differ. The
spec's thresholds are treated as targets to calibrate and freeze, not as
guarantees.

## 13. OCR is too slow for an interactive first screen on scanned documents

Measured on this machine at the calibrated 250 dpi (ADR 0004), over the 24 normal
corpus samples:

| Stage | P50 | P95 |
| --- | --- | --- |
| Open a PDF | 1.7 ms | 2.1 ms |
| Extract text (one page) | 2.4 ms | 5.2 ms |
| First-screen preview render | 45.8 ms | 212.7 ms |
| Page-turn preview render | 40.6 ms | 214.5 ms |
| OCR render (one page) | 169.8 ms | 173.6 ms |
| **OCR recognition (one page)** | **7.6 s** | **10.7 s** |

The interactive thresholds are met: text PDFs open and render well inside the 3 s
first-screen and 300 ms page-turn targets, including the worst single case
(278.5 ms). **OCR recognition is three orders of magnitude outside them**, and no
amount of tuning changes that.

**Consequence for the product, stated so it is not discovered later.** A scanned
document cannot show recognised text on the first screen. The reader shows the page
image immediately and fills in text as recognition completes, which makes
incremental progress a requirement rather than a nicety.

**Not attempted:** parallelism across pages, GPU inference, or a smaller model.
Each is a real option and none has been measured, so none is claimed.

## 14. Peak memory during a corpus run was 1.7 GiB

The M0 performance harness peaked at 1743 MiB of working set while walking the
whole corpus. That is a batch figure, not a single-document figure: it includes
holding rendered page buffers for many documents in one process. Per-document
memory has not been measured separately, so a claim about the product's footprint
on one open document cannot be made from this number.

## 15. Two-column front matter is over-merged, and was misordered on page one

Found by running a real file end to end (`scripts/evidence_parse.py`), not by any
of the M0 metrics.

**The ordering half is fixed** (ADR 0013). On `pdf_b_two_column` page 0, the page
title used to be emitted third — after the whole left column — because the title,
author line and inset abstract cross the body's gutter and the cut emitted the
crossing group in a fixed middle position. The crossing group is now placed by its
vertical centre. Page 0 went from 141 inverted line pairs to 0, and pooled
line-order accuracy from 0.9872 to 1.0000, with no other metric moving.

**The over-merging half is now fixed too** (ADR 0014), by calibrating the paragraph break
against the corpus instead of against one page. The mechanism was two defects that cancelled
into a third behaviour, not "paragraph assembly merges too eagerly":

1. `_same_column_flow` rejected every *negative* `_vertical_gap`, but consecutive lines of one
   paragraph routinely have slightly overlapping boxes, because a line box covers ascender to
   descender while the leading is smaller than that. On `pdf_b_two_column` page 0 the left
   column's 47 lines produced **33 breaks**, every one of them at a gap of exactly −0.94 pt.
   The guard was meant to catch the reading order stepping backwards out of a column, which is
   a gap of about −700 pt, three orders of magnitude away.
2. The other break condition was `gap > paragraph_gap_factor * typical_height`, i.e.
   1.55 × 13.34 = **20.7 pt**. Every paragraph boundary measured anywhere in the corpus has a
   gap well under that, so the test **could never fire** and only the inverted guard was ever
   breaking a paragraph. The value had also been computed against the wrong unit.

`scripts/calibrate_paragraph_step.py` measured the two populations the threshold has to
separate, using the corpus's own block boundaries as the answer key, and **same-column pairs
only** — a column jump is already rejected by the horizontal-overlap test, so counting those
would flatter any threshold:

| | within a block | between blocks |
| --- | --- | --- |
| gap, all pairs | +2.40 pt (constant) | p05 +4.16, median +6.90 |
| gap, same column | +2.40 pt (constant) | p05 +4.16, median +6.90 |

Only 3 of 268 between-block gaps are ≤ 2.40 pt, so the populations separate and the threshold
must sit in the valley between 2.40 and 4.16 pt. The default factor of 0.25 gives 3.34 pt.

**Measured effect**, on the same corpus, with nothing else changed:

| measure | before | after |
| --- | --- | --- |
| corpus blocks addressable on their own | 11/76 (0.1447) | **87/175 (0.4971)** |
| page+region accuracy | 0.8952 FAIL | **1.0000 PASS** (333/333) |
| adjacent-block order | 0.9725 | **0.9969** |
| block-type accuracy | 13/13 (thin sample) | **87/87** (section 17) |
| line-level order | 1.0000 | 1.0000 |
| body character coverage | 0.9985 | 0.9985 |

The denominator of the segmentation measure moved from 76 to 175 because with coherent
paragraphs *more* corpus blocks find a paragraph to match at all; 76 was the number that
matched while blocks were being absorbed, not the number that exists.

**What is still not fixed:** the remaining 0.497 are blocks whose text is a proper part of a
longer paragraph. The corpus's own blocks are finer than any threshold can recover — a
caption, a heading and the paragraph under it are one visual run of text — so closing this
needs structure the layout stage does not currently produce, not a better threshold. The
0.497 is also a lower bound on the current split quality: the measure's "merged" bucket
cannot distinguish "this block was absorbed into the paragraph that follows" from "this
block's paragraph is correct but the matcher paired it with a neighbour".

**Calibration caveat.** This corpus has a *constant* 12.40 pt leading and a 10.00 pt line box,
so its within-paragraph gap is a single point rather than a distribution. The valley is
therefore wider here than it will be on real papers with mixed leading, and 0.25 is calibrated
against this corpus, not against published articles. Re-calibration on real papers is
required before the number can be called settled.

**The old reading-order measure could not see the ordering defect.** It scored
these pages at 0.9733 and passed. Word-LCS finds the longest common subsequence, so
displacing a *short* block is nearly free: dropping the 8-word title to keep the
200-word abstract costs about 1%. `scripts/measure_line_order.py` now measures
line order instead, matched by bounding-box IoU, so merging cannot disturb it and a
short block counts as much as a long one. Both figures are reported.


## 16. Tables are not extracted, so table cells are offered for translation

There is no table or figure extraction anywhere in the pipeline. A wide table
becomes a run of ordinary paragraphs, one per cell: in the same evidence run
`Docs`, `Coverage`, `Order acc.`, `8`, `99.1%` each became a translatable
paragraph. Sending a numeric cell to a translation service is wasted spend, and a
translated percentage is a wrong number in a research table.

This also swamps block-type measurement: of the matched corpus blocks, 132 are
cells of one wide table. They are excluded from the classifier's score and
reported separately, because no classifier can label a table cell when nothing
detects the table.

A figure caption *is* extracted (it is ordinary text), and captions are
translatable, so captions are not affected by this gap.

## 17. Block-type measurement is over a corpus whose typography is uniform

This section previously reported the classifier at 13/13 and said the sample was thin
because 33 blocks were merged by section 15. Section 15 is now fixed, so the sample is
**87 blocks scored at 87/87**, up from 13. The thin-sample warning is resolved: the number
is now over every corpus block that has text, is not a table or figure, and matches a
paragraph one-to-one or is scored where it does.

| true type | scored |
| --- | --- |
| paragraph | 56/56 |
| title | 8/8 |
| author | 8/8 |
| abstract | 7/7 |
| caption | 4/4 |
| formula | 2/2 |
| footnote | 2/2 |

Three defects were only visible once the sample grew from 13 to 87, and all three were
fixed: the author line was typed as a title (7 cases — an author list is short and set
larger than the body, which is exactly the title test's own signal, so the title test
claimed it first, and all 7 corpus author lines were affected), a caption had **no rule at
all** (0 of 4 — the layout stage has no notion of a caption), and a single-author line
"O. Author" matched neither the multi-author pattern nor the title test.

**What the 87/87 still does not support.** Every document in the corpus has a constant
12.40 pt leading, a 10.00 pt body box, two or three type sizes and the same 13 block shapes.
A classifier that keys on relative type size and label shape will do well on that and is not
thereby shown to work on published papers, which vary leading, use colour and rules, place
captions above and below their figure, and number sections inconsistently. The claim the
number supports is "no known misclassification on any block of this corpus that can be
scored".

## 18. The caption clause guard is reasoned, not measured

`_CAPTION_CLAUSE_RE` exists to keep prose that merely *mentions* a table out of the caption
type — "Table 2 shows the results" must stay a paragraph, because a caption is not translated
as body prose and mistyping a real sentence as a caption would keep it out of the translation
entirely.

That guard has never fired on the corpus. The corpus contains no sentence of that shape, so
its three parametrized tests assert behaviour against **invented** examples, not against
measured ones. It is the only rule in the block classifier in that position: every other rule
was found by measuring, and several were wrong the first time. It is carried because the
error it prevents is a silent translation omission, which is the expensive direction, but it
would be the first thing to re-examine against real papers.

## 19. A re-parse invalidates the translation cache by design

`cache_key` includes the parse revision id, because the spec lists it among the required
inputs. The consequence is worth stating: **re-parsing a document whose text has not changed
invalidates every translation of it**, and the text is paid for again. The block content hash
alone would be enough for correctness — it is what actually determines the output — so the
revision only ever makes the key *stricter*.

This is not a bug and no cached row is ever reused incorrectly because of it. It is a cost,
and the spec's own wording is the reason it is not relaxed here. If re-parsing becomes common
(an OCR re-run, a parser upgrade), the honest options are to drop the revision from the key or
to make the key configurable, and either is a decision to take deliberately with the cost
measured rather than a silent change.

## 20. The section path is currently the document title

Measured in `scripts/evidence_translation.py`: every block of `pdf_b_two_column` carries
`section_path == ["Reading Order Recovery in Two-Column Layouts"]`, the paper's title. The
layout stage pushes the title onto the section stack because a title is short and set larger
than the body, which is its test for a heading; the real section headings never reach the
stack, because they are still merged into the paragraph below them (section 15).

Two consequences, both real:

* The outline the reader will navigate by has one level and one entry, so it is useless.
* The context sent to the translation provider is the title for every block, which resolves
  nothing. A context that already appears in the block's own text is now dropped, so the title
  block no longer sends itself as its own context, but the rest still receive a title that
  says nothing about their section.

Both are fixed by the same change — separating a heading from the paragraph under it — which
is therefore the next thing to fix, not a cosmetic one. Until then the outline is withheld
rather than shown with one entry, since a single-entry outline presented as navigation would
misrepresent what the reader knows about the document.



