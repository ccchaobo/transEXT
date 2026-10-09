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

## 3. Page-and-region accuracy is measured against a paragraph's whole-page box

The acceptance item "≥ 200 sampled blocks, ≥ 95% attributed to the correct page and
region" reads **0.8952** (205/229 blocks) on the corpus, and that figure is **not
trusted**. Both dominant failure modes are artefacts of the comparison, not placement
errors:

* A reference block whose text was split across two corpus blocks is matched to the
  paragraph holding its *continuation* — a fragment with a small box — while the
  reference block's own box is the union of both parts. The block is on the right page
  in the right column; the boxes simply are not the same shape. Example overlap 0.133
  for `[5] H. Statistician and I. Reviewer, "Co…`.
* A heading or author line absorbed into the paragraph that follows it sits at 0.492
  against the 0.5 threshold, so a correct merge is recorded as a region failure.

The cause is structural: the matcher compares a block against a paragraph's union box
for the whole page, while the pipeline now splits that paragraph into column fragments.
The metric needs to compare against the paragraph's fragment on the block's own page.

**What is verified instead:** the text is present (coverage 0.9985, no line recorded as
drawn is absent) and it is in the right order (pooled reading order **0.9915** by a
matcher-independent word-LCS measure). Reading order and coverage are the two items that
speak to whether text is lost or misordered; this item speaks to whether a highlight
would land on the right rectangle, and it cannot be answered by the current comparison.

**Status:** the acceptance figure is reported as **FAIL** and not worked around. Either
the metric is corrected to compare per-page fragments, or the item stays FAIL. It is not
to be re-labelled.

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

## 15. Two-column front matter is over-merged and misordered on page one

Found by running a real file end to end (`scripts/evidence_parse.py`), not by any
of the M0 metrics. On `pdf_b_two_column` page 0 the corpus records **21 blocks** —
title, author, abstract, then the left column, then the right column — and the
pipeline emits **7 paragraphs**, in this order:

| pipeline position | what it actually is |
| --- | --- |
| 0 | the tail of the abstract |
| 1 | a left-column run, several source blocks merged |
| 2 | the title |
| 3 | the author line with the head of the abstract absorbed into it |
| 4-6 | the right column, in runs |

The correct order starts with the title. So the title is third, the abstract is
split in two and its halves are separated by a whole column, and distinct headings
and paragraphs have been absorbed into a few long runs.

**Neither M0 reading-order measure catches this**, and that is the more important
finding:

* The word-LCS measure (`scripts/measure_reading_order.py`) scores this document's
  two-column pages at 0.9733. LCS finds the longest common subsequence, so
  displacing a *short* block is nearly free: dropping the 8-word title and keeping
  the 200-word abstract costs about 1% of the score. A metric that barely notices
  the title is in the wrong place cannot be used to claim reading order is correct.
* The adjacent-pair measure (0.9591 pooled) counts pairs, and two misplaced blocks
  out of twenty-one is about 4%.

Block order is what the reader is built on: the source pane highlights a block and
the translation pane follows it. Over-merged runs mean one anchor covers text that
should be several anchors, and the abstract halves cannot be paired with their
translations. This is recorded as a defect, not a tuning target.

## 16. Tables are not extracted, so table cells are offered for translation

There is no table or figure extraction anywhere in the pipeline. A wide table
becomes a run of ordinary paragraphs, one per cell: in the same evidence run
`Docs`, `Coverage`, `Order acc.`, `8`, `99.1%` each became a translatable
paragraph. Sending a numeric cell to a translation service is wasted spend, and a
translated percentage is a wrong number in a research table.

This also swamps block-type measurement: of 207 matched corpus blocks, 131 are
cells of one wide table. They are excluded from the classifier's score and
reported separately, because no classifier can label a table cell when nothing
detects the table.

A figure caption *is* extracted (it is ordinary text), and captions are
translatable, so captions are not affected by this gap.

## 17. Block-type measurement covers only 13 of 207 matched blocks

The classifier's 13/13 result is real but thin, and the reason matters. Of 207
matched blocks: 131 are the wide table's cells (section 16), 33 have been merged
into a longer paragraph by section 15, and 30 are pairs where the block and the
paragraph are simply different content, which is a defect of the matcher used to
score it. Scoring any of those would attribute someone else's defect to the
classifier, so they are excluded and counted. The conclusion the number supports is
"no known misclassification on the blocks that can be scored", not "the classifier
is correct on real papers".

