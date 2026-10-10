# Research Reader

A local-first bilingual reader for research literature. It imports a paper, parses
its structure, translates it paragraph by paragraph, and shows the source and the
translation side by side with the correspondence between them preserved in both
directions.

The design and implementation plan is the authoritative specification; this
repository implements it in milestones, and each milestone is closed with measured
evidence rather than a status claim.

## Where this is: M1 (the reader) usable, with named gaps

M0 is the foundation: the coordinate contract, the text cleaning rules, the corpus with recorded
ground truth, the storage layer, the wire contract, and the measurements that fix the calibrated
parameters.

M1 adds the product around it — an HTTP API, a separate worker process, and the reader itself —
and it is usable today for text PDFs: import, read with the translation drawn over the source or
side by side, navigate by outline, look up an anchored block, annotate, and export bilingual
Markdown. **Translation works against a real service**, verified end to end: 53 blocks of a real
paper translated through `https://ollama.com/v1` with `deepseek-v4.1-flash`, all `status: ok`. Two
things are **not** done and are named here rather than left to be discovered: OCR and DOCX are
engines without a wire into the parse stage ([limitations §24](docs/limitations.md)), and
`BlockTextPatch` does not exist (§25). The quality of those 53 translations has not been reviewed
by a human, and no cost figure can be stated because token usage is not attributed to a job —
both in [§2](docs/limitations.md).

The table below is the short version of what is implemented; each row names the test or script
that demonstrates it, and every one of them was run to produce the results in this document.

| Area | Implementation | Demonstrated by |
| --- | --- | --- |
| Coordinates | `PageGeometry`, `AffineTransform`, `BBox`; normalised top-left origin contract | `tests/unit/test_coords.py`, `tests/unit/test_rotation_geometry.py` (measured against rendered pixels) |
| Text cleaning | NFKC, control and soft-hyphen handling, de-hyphenation, line joining, protected spans | `tests/unit/test_cleaning.py` |
| Identifiers | Deterministic block/span/page IDs and content hashes | `packages/document_core/document_core/ids.py` |
| Layout | Band segmentation, column detection, furniture classification, paragraph and section assembly | `packages/document_core/document_core/layout.py` |
| Revision migration | Block matching and anchor migration with reliability marking | `packages/document_core/document_core/revision.py` |
| PDF extraction | Text, geometry, rotation, scan detection, rendering, anomaly handling | `scripts/spike_extract.py` |
| OCR | RapidOCR adapter with a calibrated render resolution | `tests/unit/test_ocr_adapter.py`, `scripts/cost_ocr_resolution.py` |
| DOCX | Built-in converter with a declared capability list and a decompression guard | `scripts/spike_extract.py`, `tests/unit/test_contracts.py` |
| Storage | SQLite schema, WAL, leased and fenced job queue, retry accounting | `scripts/smoke_storage.py` |
| Wire contract | Pydantic models, error taxonomy, generated OpenAPI | `tests/unit/test_contracts.py`, `tests/unit/test_openapi_artifact.py` |
| Corpus | 35 labelled samples with draw-time ground truth | `benchmarks/rr_m0/corpus_generator.py`, `benchmarks/corpus/manifest.json` |
| HTTP API | 22 routes over the frozen contract; loopback-only, same-origin guard, refusals with reasons | `tests/integration/test_api.py`, `scripts/verify_live_acceptance.py` |
| Worker | Separate process; leased, fenced, cancellable jobs; per-stage and per-unit status | `tests/unit/test_worker.py`, `scripts/smoke_storage.py` |
| Reader | Server-rendered page images with normalised-bbox overlays; five view modes; a toolbar that no view mode can hide; linked scrolling; per-block re-translation; outline; annotations; export | `scripts/verify_overlay_alignment.py`, `scripts/verify_frontend_contract.py`, `scripts/verify_checks_can_fail.py` |

**What is PENDING, and why.** These cannot be closed in this environment, and are recorded rather
than worked around:

* **Human review of translation quality.** The pipeline is no longer the blocker: a real provider
  was configured and 53 blocks of a real paper were translated through `https://ollama.com/v1`
  with `deepseek-v4.1-flash`, every row `status: ok`. Nothing has been read against the source, so
  the spec's ≥ 100 reviewed paragraphs is outstanding and no quality figure is reported. See
  [limitations §2](docs/limitations.md).
* **Cost attribution.** The service reports tokens per call; the job records `usage: {}`, so the
  cost of a document cannot be stated. The pre-flight count of blocks to send is now correct.
* **The new browser features are unexecuted.** There is no browser automation here, so the overlay
  view and the linked scrolling have never been run on a page. Everything about them that can be
  checked without a browser is asserted in `verify_frontend_contract.py` §[5], and each of those
  assertions has been reverted and confirmed to fail. What cannot be checked is whether they are
  *usable* — and the first round of that came back from the person using it, as a bug report. See
  [limitations §34](docs/limitations.md), [§36](docs/limitations.md) and [§37](docs/limitations.md).
* **A real-world document corpus.** Every sample is authored by this repository's generator, so
  the measured layout and OCR figures describe this corpus, not published papers. See
  [limitations §1](docs/limitations.md).
* **The three §12.2 thresholds** are still provisional. They are targets to calibrate and freeze;
  none was lowered to make a measurement pass. See [limitations §16](docs/limitations.md).

## Requirements

* Python 3.11 or newer (developed on 3.13.5, Anaconda)
* Windows, Linux or macOS; developed and measured on Windows 11
* No network access is required at run time. OCR models ship inside the wheel.

## Install

```powershell
# From the repository root
python -m pip install -e .            # core dependencies
python -m pip install -e ".[ocr,dev]" # OCR engine and the test runner
```

The OCR extra pulls `rapidocr-onnxruntime` and `onnxruntime`, which are
platform-specific compiled wheels. Without them the product still parses text PDFs
and DOCX; a scanned document is then reported as unreadable rather than as empty
(see `rr_adapters.ocr.available_engine`).

> **Licence note.** PyMuPDF is dual licensed AGPL-3.0 / Artifex Commercial. This is
> an obligation on distribution, not on development or internal use. It is
> recorded in [ADR 0003](docs/adr/0003-pymupdf-for-pdf.md) and in the dependency
> inventory, and it must be re-checked before any commercial or network-service
> distribution.

## Run it

Two processes, both from the repository root. The API serves the reader and never
runs a job; the worker runs jobs and serves nothing. They are separate so that a long
parse can be cancelled, can outlive an HTTP request, and cannot take the reader down
with it.

```powershell
# Terminal 1 - the reader itself, on http://127.0.0.1:8765
python -m rr_api

# Terminal 2 - the worker that executes parse and translation jobs
python -m rr_worker
```

Then open **<http://127.0.0.1:8765>** and import a PDF. Both commands are also
installed as `rr-api` and `rr-worker` after `pip install -e .`.

Useful worker flags: `--once` processes one job and exits (it also exits when the
queue is empty, so it is safe to call from a script); `--drain` stops once the queue
empties instead of polling; `--quiet` silences the start-up line.

Set `RR_PORT` to move the port and `RR_DATA_DIR` to move the database, uploaded
originals and derived page images. The server binds `127.0.0.1` only — there is no
option to bind a public interface, and state-changing requests are refused unless
they come from the reader's own pages.

**Data locations.** `RR_DATA_DIR` (default `var/`, git-ignored) holds
`reader.sqlite3`, `originals/<document>/<sha256>.pdf` and
`derived/<document>/<revision>/pages/NNNN.png`. Originals are written once and never
modified; every derived artifact is addressed by revision.

### Translation setup

Translation is refused until a provider is configured, and **no placeholder text is
ever produced in its place**.

Open the reader and click **翻译设置** in the top bar. Fill in the service root (the
base URL, including its API version segment), the model id, and the API key, then press
**测试连接**. That button sends one real request to whatever you configured and reports
what came back — the HTTP status, the error code, the latency and a sample of the reply —
which is the only way to find out whether a base URL, a model id and a key work together.
Nothing is stored or cached by it.

Saved settings live in `reader.sqlite3` and are read by **both** processes on the next
job, so **no restart is needed**. The API key travels one way: it is sent from the page to
this machine's own backend, stored, and never returned by any endpoint — every response
reports only whether a key is present. See
[limitations §31](docs/limitations.md) for what that costs.

`.env` still works and is still the right tool for pinning a value for one command.
Copy the template if you prefer it:

```powershell
Copy-Item .env.example .env
# then set RR_TRANSLATION_API_KEY, and RR_TRANSLATION_BASE_URL / RR_TRANSLATION_MODEL
# if the defaults are not what you are using
```

An environment variable **wins** over a stored setting. When that happens the panel names
the fields being overridden rather than leaving you to wonder why a saved value did nothing.

> The default base URL, `https://ollama.com/v1`, is **verified**: a real request reached it and
> returned HTTP 200 with a real completion (`.env.example` and earlier revisions of this file
> called it unverified, because `ollama.com` is not reachable from this machine's other tooling —
> the server reaches it fine). The model id is yours to pick and is not verified for you; that is
> what **测试连接** is for. Translation *quality* remains unreviewed. See
> [limitations §2](docs/limitations.md).

### What works today

| Input | Reader | Translation |
| --- | --- | --- |
| Text PDF | reads, outline, anchored lookup, annotations, export | works once a provider is configured |
| Scanned PDF / PNG / JPEG | page image renders; each page says **"no text layer; OCR is required"** | refused — nothing to translate |
| DOCX | refused with `unsupported_format` | refused |

The OCR and DOCX engines exist and are measured, but neither is wired into the parse
stage yet, so the server declines those formats instead of pretending. A refusal is
reported on the document (`parse_status = failed`), on the job (`error_code`), and in
the reader — not as a server error and never as an empty success.

### Reading a page: five view modes, and the panes follow each other

The reader shows one page at a time and offers **译文覆盖** (the translation drawn where the
original is), **左右对照**, **上下对照**, **仅原文** and **仅译文**.

In the two side-by-side modes the two panes follow each other: scrolling either one moves the
other to the matching paragraph, and the **联动** checkbox turns that off. Off is a reasonable
choice — the link snaps from paragraph to paragraph, and if you are reading one long paragraph
you may prefer the other side to stay where you put it.

The alignment is **block by block and nothing finer**, and that is not a shortcut being excused.
The source pane is one page image whose height follows the page's aspect ratio; the target pane is
a stack of cards whose total height has nothing to do with it. Equal fractions of the two scroll
ranges therefore point at different paragraphs, so nothing that interpolates between positions can
be right. Snapping to the paragraph nearest the top of the viewport is the only mapping that
survives that.

**译文覆盖** removes the correspondence problem instead of managing it. The translated text is
drawn into the area the original occupied, so there is only one place to look. It is deliberately
plain: no border, no scrollbar, no badge — just the translation where the original was. Four things
it does not pretend:

* It is a **reading view, not an export**, and it is not a translated file. Filling an English
  paragraph with Chinese is a layout problem this does not attempt, and the bilingual Markdown
  export does not come from here.
* Formulas, figures and tables are **never covered** — the classifier marks them and they stay
  verbatim.
* Where the translation is longer than the space the original used, the panel **grows downward and
  may cover the paragraph below**. The type is never shrunk to fit, because text shrunk until it
  fits is text nobody can read, which is the failure this view exists to avoid — and it is not put
  in a scroll box, because a scroll box around every paragraph stops the page reading as a page.
  In this direction (English into Chinese) the translation is almost always shorter, so this is
  rare. The panel's tooltip says so when it happens.
* The panel's area is the union of the block's line boxes, which is the paragraph's own area in
  almost every case but not by construction. Hovering a panel drops its background to 40% so the
  original shows through, and you can always check whether it is covering something that is not
  its own.

Every control — 上一页 / 下一页, the page label, the view selector, 联动, 重新翻译本块 and 翻译全文 —
sits in a toolbar **above both panes**, not inside either one. That is not cosmetic: those controls
used to live in the translation pane's header, and choosing 译文覆盖 or 仅原文 hides that pane, so
the control that gets you back out was hidden by the mode it had just switched to. See
[limitations §36](docs/limitations.md).

**重新翻译本块** sends one paragraph back to the service and replaces what is stored for it, using
`scope: "blocks"` with `force`. It acts on whatever you last clicked, so it works in the overlay and
single-pane modes too, where there is no card to put a button on. A machine translation that is
wrong is not a reason to re-translate a page.

Both of those fields were accepted by the API and **ignored by the worker** until recently: asking
for one block translated the whole document, and `force` sent nothing at all. They are spend
controls, and they are now pinned by tests that fail when either is reverted. See
[limitations §38](docs/limitations.md).

A block with no translation is left showing its original, marked so you can tell "not translated"
from "translated to something empty".

## Verify

Every command below is runnable from the repository root and prints its own result.
Nothing here depends on a service being started.

```powershell
# Unit tests
python -m pytest tests/unit -q

# Coordinate contract: check all 16 corner mappings plus round-trip properties,
# and re-measure the table from rendered pixels rather than trusting it
python scripts/check_rotation.py --verify

# Anomaly handling and the extraction and conversion paths
python scripts/spike_extract.py

# Job queue: leasing, retry, fencing, partial completion
python scripts/smoke_storage.py

# OCR accuracy against the corpus
python scripts/spike_ocr.py

# Performance (P50/P95 per stage, peak memory)
python -m benchmarks.rr_m0.perf_harness

# Regenerate and validate the wire-contract artifact
python scripts/gen_openapi.py
python scripts/check_openapi.py

# Regenerate the corpus and the environment inventory
python -m benchmarks.rr_m0.corpus_generator --clean --root benchmarks/corpus
python scripts/env_inventory.py --out benchmarks/out/env_inventory.json
```

### Verifying the reader end to end

These four are about the running product rather than a library. They need no
credentials and no service of their own; the first two start their own.

```powershell
# Every field the frontend reads exists in the real payload, checked against a real
# import and a real worker run rather than against the model definitions
python scripts/verify_frontend_contract.py

# That each overlay box really sits on the text it claims, measured on rendered
# pixels: banded ink correlation against the source image, and a mirrored control
# that must fail
python scripts/verify_overlay_alignment.py

# Live HTTP acceptance: start the API and the worker, import, read, refuse, export
python scripts/verify_live_acceptance.py

# The whole suite, unit and integration
python -m pytest tests -q
```

### All of it at once

```powershell
python scripts/verify_all.py            # every entry point, one table, ~15 minutes
python scripts/verify_all.py --list     # what runs, and why each one counts as verification
python scripts/verify_all.py --group product
```

`verify_all.py` runs 27 entry points, each in its own process with its own data
directory, and reports the exit code, the wall time and the verdict line for each.
The list is written out by hand and never globbed: the repository also holds about
fifty `diagnose_*` and `probe_*` scripts, which print what they found while chasing
one defect and assert nothing. Running those and reporting their exit codes as
verification would be the same mistake as a metric that cannot fail on the bug it
was written for — which is why `verify_order_by_eye.py` is deliberately excluded,
and why `verify_rotation.py` asserts handedness as well as position and requires its
own mirror test to fail.

Artifacts are written to `benchmarks/out/`. The corpus (about 31 MiB of generated
files and ground truth) is committed so that measurements can be reproduced
without regenerating it.

## Measured results

All figures are from this machine, recorded in
[`benchmarks/out/env_inventory.json`](benchmarks/out/env_inventory.json). The
specification's thresholds are targets to calibrate and freeze, not guarantees.

### Performance (P50 / P95 per stage)

Measured by `python -m benchmarks.rr_m0.perf_harness` over the 24 normal samples,
at the calibrated 250 dpi. Full data in
[`benchmarks/out/perf.json`](benchmarks/out/perf.json).

| Stage | n | Typical (P50 → P95) | Threshold | Verdict |
| --- | --- | --- | --- | --- |
| Open a PDF | 14 | ~1.7 → ~2 ms | — | — |
| Extract text, one page | 31 | ~2.4 → ~6 ms | — | — |
| First-screen preview render | 14 | ~46 → ~215 ms | ≤ 3 s | **PASS**, ~14× margin |
| Page-turn preview render | 48 | ~40 → ~210 ms | ≤ 300 ms | **PASS**, ~1.4× margin |
| Scan detection, one page | 31 | ~1.2 → ~4 ms | — | — |
| DOCX conversion | 6 | ~67 → ~155 ms | — | — |
| OCR engine load | 10 | ~185 → ~665 ms | — | — |
| OCR render, one page | 13 | ~170 → ~180 ms | — | — |
| **OCR recognition, one page** | 17 | **~7.4 s → ~10.4 s** | — | **not interactive** |

**These are rounded on purpose.** The exact P50 / P95 / max for every stage is in
[`benchmarks/out/perf.json`](benchmarks/out/perf.json), which the harness rewrites on every run;
the figures above are the shape of it, not a single run's output. The reason is that the numbers
move more than they look like they should: across runs of identical code the first-screen P95 was
observed at 211.2, 212.6, 212.7, 213.3, 218.8, 226.9, 230.9 and 235.0 ms, and its maxima between
274.1 and 322.3 ms. A table reported to the millisecond went stale every time the harness ran, and
chasing it was both futile and a temptation to re-measure until the numbers looked good. Read the
artifact when the third digit matters.

The **verdicts** do not move. Every run passed every threshold, and the margins are large: the
first screen has a factor of ~14 in hand against its 3 s budget, and the page turn ~1.4× against
its 300 ms one. No threshold has been adjusted to make a measurement pass.

Peak working set for the whole run: **~1740 MiB**, a batch figure across many documents rather than
a per-document footprint ([limitations §14](docs/limitations.md)). This one is stable — 1739.9,
1740, 1741.1 and 1742.4 MiB across the runs — except when the harness was measuring at the
rejected 300 dpi, where it read 2055 MiB.

**The harness must be run at the calibrated OCR resolution**, and this is a correction rather
than a description. `--ocr-dpi` used to default to a literal 300, which is the value the OCR
adapter's own calibration rejected: at 300 dpi recognition is slower *and* slightly worse than at
250. Running the harness with no arguments therefore rewrote this table — and the peak-memory
figure, 1743 MiB becoming 2055 MiB — with numbers from a configuration that does not ship. It now
defaults to `rr_adapters.ocr.OCR_RENDER_DPI`, so the calibration has one definition and the
measurement cannot drift from it.

OCR recognition is three orders of magnitude outside the interactive targets, and
no tuning changes that. The reader therefore shows the page image first and fills
in text as recognition completes. This is recorded as
[limitations §13](docs/limitations.md) rather than presented as a pass.

### OCR accuracy

Measured by `python scripts/spike_ocr.py` against per-page ground truth recorded
when the corpus was drawn. Recognition and reading order are scored **separately**,
and the two are reported per page because a single number would hide which one
failed.

At the calibrated 250 dpi:

| Corpus document | Recognition CER | Clear-scan verdict | Reading-order score (per page) |
| --- | --- | --- | --- |
| `scan_a_clean_300dpi` | **0.0484** | PASS (≤ 0.05) | 0.860, 0.848 |
| `scan_b_two_column_noisy` | 0.0338 | degraded scan, not counted | 0.884, 0.961 |
| `scan_c_skewed` | **0.0092** | PASS | 0.784, 1.000 |
| `scan_d_low_dpi` | 0.0061 | low-resolution, not counted | 1.000, 0.933 |
| `scan_e_grayscale` | **0.0334** | PASS | 0.884, 0.948 |

All three clear scans pass the provisional 5% recognition threshold. The two exempt
rows are reported separately because a noisy or low-resolution scan is a different
input class; they are not omitted, and both also score 0.03 or better.

**Reading order is a separate, weaker result and is not claimed as a pass.** The
scores above describe the *recogniser's own* line sequence. A score near 0.88 on a
two-column page means roughly one adjacent line pair in eight is out of sequence,
which is expected: the recogniser scans visually and reads across both columns
rather than finishing the left column first. Reordering is deliberately not the
recogniser's job — it belongs to `document_core.layout` (spec §8) — so this figure
measures the input to layout, not the reading order the reader will see. **The
layout module's reading order has not been measured yet** and is listed as PENDING
below.

An earlier version of this table reported a reading-order score of 1.0 on every
page. That was wrong: the metric returned a perfect score whenever fewer than two
lines could be matched, and the ground truth recorded each paragraph as a single
flattened line, so on a two-column page a 96-line recognition was compared against
a one-line reference and scored as perfect while measuring nothing. Both the metric
and the ground truth have been fixed, and both fixes are pinned by tests
(`tests/unit/test_ocr_metrics.py`, `tests/unit/test_corpus_truth.py`).

The engine itself is deterministic — repeated runs at one resolution produce
byte-identical text — and at the right resolution it recovers a clean page
**exactly** (0.0000). Accuracy varies reproducibly with render resolution and is
**not monotonic**: one page scored worse at 200 dpi than at either 150 or 250 dpi.

The calibrated render resolution is **250 dpi**
(`rr_adapters.ocr.OCR_RENDER_DPI`), chosen on measured cost *and* accuracy:

| Render dpi | Mean CER | Mean seconds per page |
| --- | --- | --- |
| 150 | 0.0342 | 4.28 |
| 200 | 0.0448 | 4.75 |
| **250** | **0.0247** | **5.76** |
| 300 | 0.0298 | 6.19 |
| 400 | 0.0206 | 7.45 |

250 dpi halves the error of 200 dpi; 300 dpi costs more *and* recognises slightly
worse. Selecting the resolution per page by mean confidence was tested and
rejected: confidence correlates *positively* with error (+0.17) and picks the
better result 43% of the time, below chance. See
[ADR 0004](docs/adr/0004-rapidocr-for-ocr.md).

### Acceptance status

| Spec §12.2 item | Status | Evidence |
| --- | --- | --- |
| Coordinate contract, all rotations | **PASS** | `scripts/check_rotation.py --verify`; markers within 2 px of the transform's prediction for 0/90/180/270, re-measured from pixels |
| PDF text box frame | **PASS** (was a defect) | `scripts/diagnose_adapter_vs_render.py`, `scripts/verify_coordinate_fix.py`; every line box was vertically mirrored. See [ADR 0011](docs/adr/0011-pymupdf-text-frame.md) |
| Body character coverage ≥ 98% | **PASS** | **0.9985** pooled over the 9 text-layer documents (84 of 56008 chars, typographic quotation marks); `scripts/measure_layout.py` |
| Adjacent-block order ≥ 95% | **PASS** | **0.9969** (323/324 pairs); `scripts/measure_layout.py` |
| Reading order (matcher-independent) ≥ 95% | **PASS** | **0.9983** pooled word-LCS over 17 pages; every page ≥ 0.95; `scripts/measure_reading_order.py`. Two-column pages were **0.53–0.54** before the XY-cut fix |
| Reading order (line-level, merge-independent) ≥ 95% | **PASS** | **1.0000** — 0 inverted pairs of 32964 over 928 matched lines, every document at 1.0000; `scripts/measure_line_order.py`. See [ADR 0013](docs/adr/0013-placing-the-crossing-group.md) |
| ≥ 200 sampled blocks ≥ 95% correct page and region | **PASS** | **1.0000** (409/409 blocks). Was 0.8952 FAIL; that figure was the paragraph over-merging of [limitations §15](docs/limitations.md), not the matcher artefact this README previously claimed. See [ADR 0014](docs/adr/0014-calibrating-the-paragraph-break.md) and [ADR 0015](docs/adr/0015-a-type-size-change-starts-a-new-block.md) |
| Corpus blocks addressable on their own | **PASS, not a spec item** | **235/246 = 0.9553**, from 11/76. Reported because anchoring is what the reader is built on; `scripts/measure_segmentation.py` |
| OCR CER ≤ 5% on clear scans | **PASS** | recognition 0.0484 / 0.0092 / 0.0334 on the three clear scans, all ≤ 0.05 |
| OCR reading order | **PARTIAL, not claimed as pass** | 0.78–1.0 per page before layout reordering; see above |
| Protected placeholder integrity 100% | **PASS** | `tests/unit/test_cleaning.py`, including a percentage token that earlier went unprotected |
| ≥ 100 human-reviewed paragraphs ≥ 4/5 | **PENDING** | the pipeline runs against a real service (53 blocks, all `status: ok`) but no human has reviewed the output; only the review is outstanding (limitations §2) |
| First screen ≤ 3 s | **PASS** | 5.2 ms P95 text extraction, 212.7 ms P95 preview render, 2.1 ms P95 open |
| Page turn ≤ 300 ms | **PASS** | 214.5 ms P95, 40.6 ms P50, worst case 278.5 ms |
| Anomaly handling | **PASS** | 11/11 handled as intended by `scripts/spike_extract.py` |
| Job recovery, retry, fencing | **PASS** | `scripts/smoke_storage.py` |

The layout items are measured over **text-layer pages only**. A scanned page has no
text layer, so the extractor returns nothing for it and OCR is required; averaging
such a page into a text-layer coverage figure reported 0.39 for `mixed_f_hybrid`, whose
first page is an image, while every text page scores 0.997 or better.
`scripts/measure_layout.py` names the pages it could not judge instead of hiding them.

Two measurement defects made a broken layout read as PASS, so reading order is now
reported three ways. `adjacent_order_accuracy` counts only pairs where both blocks were
matched, which means a block the pipeline moved out of order and the matcher then failed
to pair is excluded from the score; that is how a pipeline still emitting left, right,
left, right on two-column pages showed 0.9858. `scripts/measure_reading_order.py` compares
the reference word sequence against the pipeline's with no matcher in the loop.

Neither of those saw the next defect, and the direction was reversed: here the *old*
measure was too **lenient** and was hiding a real fault. Word-LCS scores a page 0.9733
while the page title is emitted third, because dropping an 8-word title to keep a 200-word
abstract costs about 1% of the score. `scripts/measure_line_order.py` measures the order of
**lines** instead — matched by bounding-box IoU, so merging cannot disturb it, and a short
block counts as much as a long one. It found that page 0 of every two-column document was
out of order and every other page was perfect, which pointed straight at the cause. See
[ADR 0012](docs/adr/0012-xy-cut-reading-order.md) and
[ADR 0013](docs/adr/0013-placing-the-crossing-group.md).

The page-and-region row is the counter-example, and it is kept on display for that reason.
It reported **0.8952 and FAIL**, and this README explained the failure away as an artefact of
the matcher — a mechanism that really does exist. It was wrong. The metric was correctly
detecting the same over-merging defect from a different angle, and it went to **1.0000** once
the paragraph break was calibrated, with no change to the matcher at all. A plausible
mechanism that *could* explain a failure is not evidence that it does; it has to be shown to
account for the failures. See [limitations §3](docs/limitations.md) and
[ADR 0014](docs/adr/0014-calibrating-the-paragraph-break.md).

The PENDING rows are not failures. They are items that require a real translation
provider, a real-world corpus, or a measurement that has not been built yet, and
each is named so it cannot be mistaken for done.

## Sample provenance

**Every document in the corpus is authored by this repository.** There is no
third-party or copyrighted material, so the corpus is freely redistributable — and
equally, no measurement taken on it generalises to real published papers. The
corpus contains 35 samples (8 text PDFs, 5 scanned PDFs, 1 mixed, 4 images, 6
DOCX, 11 malformed anomaly files); 24 are normal samples, meeting the spec's
minimum of 24.

Ground truth is recorded at draw time, so text, layout class, reading order and
geometry are exact by construction. Coordinate checks additionally compare against
page metrics the PDF file itself declares, which is an independent source.

## Repository layout

```
apps/web/                 the reader: HTML, CSS and ES modules, served by the API
packages/document_core/   domain logic: coordinates, cleaning, ids, layout, revision
packages/contracts/       Pydantic wire models and the generated OpenAPI document
adapters/rr_adapters/     PDF, DOCX and OCR engines - the licence-confined layer
services/api/rr_api/      HTTP layer: routes, reads, exports, and the frontend mount
services/api/rr_services/ application services: import, parse, translate, runtime assembly
services/api/rr_storage/  SQLite schema, workspace, leased job queue
services/api/rr_worker/   the worker process and its command line
benchmarks/rr_m0/         corpus generator, OCR metrics, performance harness
scripts/                  verification, calibration and diagnostic tooling
tests/unit/               unit tests
tests/integration/        API, worker and failure-visibility tests
docs/adr/                 architecture decision records
docs/limitations.md       what is not done and what is not verified
```

## Architecture decisions

Decisions are recorded with the alternatives that were actually compared and the
measurement that settled the choice. See [`docs/adr/`](docs/adr/README.md). The
records most worth reading first:

* [0004](docs/adr/0004-rapidocr-for-ocr.md) — OCR engine and render resolution,
  including two figures that were measured wrong and why
* [0008](docs/adr/0008-normalised-coordinates.md) — the coordinate contract
* [0009](docs/adr/0009-rotation-transform-measured.md) — a correctness bug in
  rotation handling, and why the fix is measured rather than derived
* [0005](docs/adr/0005-docx-conversion-path.md) — what DOCX conversion does not
  reproduce

## Known limitations

Read [`docs/limitations.md`](docs/limitations.md) before relying on any figure
here. It states, for each item, the bound and how it was measured — including the
synthetic corpus, the unverified translation quality, the degraded DOCX path, the
absence of browser automation, and the OCR cost.

## Licence status

Undecided. The `pyproject.toml` records `Proprietary-until-decided` deliberately:
the dependency inventory shows one dependency (PyMuPDF) whose licence constrains
distribution, so a licence cannot be chosen for this project without first deciding
what to do about that. See [ADR 0003](docs/adr/0003-pymupdf-for-pdf.md).
