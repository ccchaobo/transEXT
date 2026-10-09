# ADR 0004: OCR engine selection

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Refines: spec §5.3, §12.2

## Context

Scanned pages and page images have no usable text layer, so recognition is the
only way to get text. The spec requires a measured character error rate on clear
scans (a provisional 5%) and a page routed to OCR whenever the text layer fails
either test.

The constraint that shapes the choice is offline operation: the product is
local-first and must work with no network. That rules out any engine that calls a
cloud OCR API at recognition time.

## Decision

Use **RapidOCR** (`rapidocr_onnxruntime`, 1.2.3) with `onnxruntime` 1.20.1.

The decisive property is that the detection and recognition models ship inside
the wheel. The engine therefore works with no network access and no separate
model download step, which is exactly the local-first requirement.

## Alternatives considered

**A. Tesseract via `pytesseract`.** The most widely deployed open-source engine
and genuinely good on clean printed text, with mature language packs. Rejected as
the *default* here on evidence: neither `pytesseract` nor the `tesseract` binary
is installed in this environment, so it cannot be measured or verified. It
remains the first candidate to re-evaluate if a system binary is available,
because it is more mature and its per-word output is easier to map onto the
coordinate contract.

**B. A cloud OCR service.** Best accuracy on hard scans, especially handwriting.
Rejected: it breaks the local-first requirement and sends the user's unpublished
manuscripts to a third party. This is a product decision, not an accuracy
tradeoff.

**C. PaddleOCR directly.** The same model family as RapidOCR with a larger
support surface. Rejected as redundant: RapidOCR already packages the same
inference path with bundled models, and adding PaddlePaddle would add a heavy
dependency for no accuracy gain on printed text.

**D. RapidOCR with bundled models.** Chosen.

## Measured behaviour

### The engine itself

Measured on this machine against per-page ground truth recorded at draw time. The
figures below are *recognition* only; reading order is a separate measurement and is
reported in its own section, because earlier versions of this table quoted a
reading-order score that was vacuously 1.0 (defect described below).

| Source | Render dpi | Recognition CER | Notes |
| --- | --- | --- | --- |
| Vector PDF, no degradation | 200 | 0.0000 | exact recovery |
| Clean scan, best resolution | 400 | 0.0000 | exact recovery of a *degraded* page |
| Clean scan, page 0 | 200 | 0.1394 | the same page, badly served |
| Clean scan, page 0 | 250 | 0.0593 | the calibrated resolution |
| Clean scan, page 0 | 400 | 0.0000 | exact |

The engine is deterministic: five repeated runs on one image produced
byte-identical text, and across three repeats at each of five resolutions the
recognition error was identical to four decimal places every time. **Any
variation reported below is therefore a real property of the input, not noise.**

Two findings matter more than the headline numbers:

* **The engine is not the limiting factor.** On a clean render it recovers the
  page exactly, and at 400 dpi it recovered a *JPEG-compressed, blurred, noisy,
  speckled* scan exactly. The error budget is spent on the scan and on the
  resolution the page is rendered at, not on the recogniser.
* **Resolution is not monotonic, and not predictable.** One page scored 0.0548 at
  150 dpi, 0.1394 at 200 dpi, 0.0593 at 250 dpi, 0.0622 at 300 dpi and 0.0000 at
  400 dpi. The worst resolution was neither the lowest nor the highest. "Render
  as high as possible" is the wrong policy.

### Recognition and reading order are separate

An earlier figure of 0.77 for two-column scans was **not** an engine failure. The
metric was at fault: ground truth concatenated a page's blocks in *semantic*
order, so comparing it against 96 recognised lines in visual order measured the
misalignment of two different orderings, not recognition accuracy. The two are now
scored separately — an order-insensitive character error rate for recognition and a
reading-order score for sequence — and the two-column documents score 0.034 and
0.016 recognition.

A second, worse defect followed from the same confusion: the reading-order metric
returned a perfect `1.0` whenever fewer than two lines could be matched, and the
ground truth recorded whole paragraphs as single lines, so every page reported a
perfect order score while some had measured nothing at all. Both are fixed; the
methodology is recorded in [ADR 0010](0010-recognition-and-order-measured-separately.md).

The corrected reading-order figures for the recogniser's own output are **0.78 to
1.0** per page, lower on two-column pages because the recogniser scans visually and
crosses between columns. That is the expected behaviour of a recogniser and is not a
defect: reordering belongs to `document_core.layout`. It does mean the reading-order
score reported here describes the *input* to layout, not the order the reader sees.

### Render resolution policy

Recognising at the page's native resolution rather than a fixed one is the
intuitive policy. It was measured, and it does not hold up:

| Policy | Mean recognition CER over 5 scanned documents |
| --- | --- |
| Fixed 200 dpi | 0.045 |
| Native resolution per page | 0.037 |
| Best achievable per page (oracle) | 0.012 |

Native resolution is *worse* than a fixed resolution, because a page stored at
200 dpi recognises better when upscaled than at its own scale. The oracle column
is overfitted to these ten pages and is reported only as a ceiling.

A better policy would select the resolution by a signal available at runtime, so
the obvious candidate — mean line confidence — was tested. It does not work:
confidence correlates **positively** with error (+0.17, where a useful selector
needs a clear negative correlation) and picking the higher-confidence result
chooses the lower-error resolution in **43%** of pairs, below chance. Selecting by
confidence would be worse than not selecting at all.

Resolution therefore has to be a fixed, calibrated parameter. Measuring accuracy
and cost together over five scanned documents (two pages each) gives:

| Render dpi | Mean recognition CER | Mean seconds per page |
| --- | --- | --- |
| 150 | 0.0342 | 4.28 |
| 200 | 0.0448 | 4.75 |
| 250 | **0.0247** | **5.76** |
| 300 | 0.0298 | 6.19 |
| 400 | 0.0206 | 7.45 |

**Decision: render for OCR at a fixed 250 dpi.** It halves the error of 200 dpi
for about one second per page. 300 dpi costs *more* than 250 dpi and recognises
slightly *worse*, which is the non-monotonicity again and the reason "round up to
a familiar number" is not a policy. 400 dpi is the most accurate setting measured
but costs 1.7 s per page more than 250 dpi for a further 0.004 of error.

An earlier version of this record chose 300 dpi on accuracy alone, before cost was
measured. That is what the table above corrects: an accuracy-only comparison
cannot distinguish 250 from 300 dpi, and the cost measurement is what shows one of
them to be strictly better. The value lives in code as
`rr_adapters.ocr.OCR_RENDER_DPI` so that changing it is a deliberate act rather
than an edit to a literal.

**Cost is the binding constraint on scanned documents.** At roughly 5.8 s per page
of recognition, plus about 0.15 s of rendering, OCR cannot meet a 3 s first-screen
target. The reader therefore shows the page image first and fills in text as
recognition completes, which is why the job model separates stages and why a
partially-completed job is a first-class state. This is a design consequence of a
measured number, not an excuse.

An early measurement reported a 14.7% error rate on a "clean" scan. That figure
was **wrong**, and the cause was a defect in the corpus ground truth rather than
in the engine: the ground truth recorded each block's whole semantic text on
every page it touched, and omitted the `Abstract-` prefix that was drawn. Against
the corrected per-page ground truth the same engine run scores 0.0000.

## Consequences

* OCR runs fully offline with no model download. This is verifiable: run the
  reader with the network disabled and the OCR path still works.
* The engine returns per-line boxes with a confidence score, which the parser
  uses to populate the coordinate contract and to flag low-confidence lines
  rather than silently accepting them. **Confidence must not be used to choose
  between competing recognition attempts**, on the evidence above; it is only a
  per-line review hint.
* OCR cost is significant: roughly 5.8 s per page of recognition at 250 dpi on
  this machine, so a scanned page cannot meet the 3 s first-screen target by OCR
  alone. The reader therefore shows the page image first and fills in text as
  recognition completes, rather than blocking the first screen. This is why the
  job model separates stages and why "partially succeeded" has to be a real state.
* `onnxruntime` is a compiled dependency, so the wheel is platform specific. The
  dependency inventory records the version and the platform it was resolved for.
* Recognition quality on *real* scans is not yet established. Every scanned
  document in the corpus is generated by this repository's own degradation
  pipeline, so the measured rates bound what the pipeline does under controlled
  conditions, not what a 1990s photocopy does. This is recorded as a limitation
  and as a PENDING item rather than presented as a validated accuracy claim.

## Revisit if

* A real scanned corpus becomes available, which is the only way to establish a
  credible accuracy figure for the product's actual inputs.
* `tesseract` becomes available on the target system, which makes a head-to-head
  comparison possible on identical input — the correct way to choose between
  them.
* The provisional 5% threshold proves to be the wrong target once real scans are
  measured. The threshold must then be recalibrated deliberately and the change
  recorded, not quietly relaxed.
