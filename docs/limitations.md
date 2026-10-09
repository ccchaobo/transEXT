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

## 3. DOCX conversion is a declared degraded path

See [ADR 0005](adr/0005-docx-conversion-path.md) for the full list. In summary,
these are **not** reproduced, and each is reported as a user-visible issue on the
document rather than silently dropped:

* text boxes, shapes and WordArt (text genuinely missing — severity `error`),
* floating and wrapped object placement,
* tracked changes, comments and revision marks,
* Word's pagination (the product uses its own rendering and says so),
* the document's fonts, numbering scheme, and equation layout.

## 4. Frontend verification is not automated

This environment has no browser automation and no headless browser. There is no
end-to-end UI test.

**What is verified:** DOM-contract unit tests against the frontend modules, the
wire contract on the server, and documented manual steps for the interactive
behaviour.

**What is not verified:** that the two panes scroll in step in a real browser,
that highlighting lands on the intended rectangle at every zoom level, and that
the layout is usable at small window sizes. These require a browser and a human,
and they are listed as manual steps rather than claimed as covered.

## 5. Real scanning conditions are not represented

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

## 6. Coordinate accuracy is bounded by the parser, not asserted

The coordinate contract is tested against page geometry that the PDF file itself
declares (media box, crop box, rotation), which is an independent check. Content
positions are checked against draw-time ground truth.

**What is not verified:** that a user clicking a translated sentence lands on the
correct *line* within a paragraph. Block-level anchoring is what is implemented
and tested. Sub-block anchoring would need a finer contract and is not claimed.

## 7. Page-level location is never presented as paragraph-level

Following the spec, a page-level result is always described as page-level. The
reader's location indicators name a page and a region, not a sentence. Where a
block's extraction is uncertain, the uncertainty is surfaced as a quality issue
rather than presented as a precise anchor.

## 8. Encrypted documents are refused, not cracked

An encrypted PDF is detected and reported. No password prompt, no dictionary
attack, and no attempt to extract partial content is made. This is a product
boundary, not a technical gap.

## 9. Deletion scope

Deleting a document removes its database rows, its original file, its derived
resources and its caches. **Backups and any copies the user made outside the
application are explicitly not covered**, and the confirmation text says so.
A backup policy is a separate concern that is not implemented.

## 10. PyMuPDF licence obligation

PyMuPDF is dual licensed AGPL-3.0 / Artifex commercial. This is an obligation on
*distribution*, not on development or internal use. It is recorded in
[ADR 0003](adr/0003-pymupdf-for-pdf.md) and in the dependency inventory, and it
must be re-checked before any commercial or network-service distribution. The
library is confined behind the `DocumentParser` interface so it can be replaced.

## 11. Performance figures are for this machine only

Timings describe the machine and versions recorded in the environment inventory.
They are not portable, and a slower machine or a cold page cache will differ. The
spec's thresholds are treated as targets to calibrate and freeze, not as
guarantees.

## 12. OCR is too slow for an interactive first screen on scanned documents

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

## 13. Peak memory during a corpus run was 1.7 GiB

The M0 performance harness peaked at 1743 MiB of working set while walking the
whole corpus. That is a batch figure, not a single-document figure: it includes
holding rendered page buffers for many documents in one process. Per-document
memory has not been measured separately, so a claim about the product's footprint
on one open document cannot be made from this number.
