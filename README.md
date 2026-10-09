# Research Reader

A local-first bilingual reader for research literature. It imports a paper, parses
its structure, translates it paragraph by paragraph, and shows the source and the
translation side by side with the correspondence between them preserved in both
directions.

The design and implementation plan is the authoritative specification; this
repository implements it in milestones, and each milestone is closed with measured
evidence rather than a status claim.

## Current milestone: M0 (foundation) — complete, with two PENDING items

M0 is the part that everything else depends on: the coordinate contract, the text
cleaning rules, the corpus with recorded ground truth, the storage layer, the wire
contract, and the measurements that fix the calibrated parameters.

**What is implemented.** The table below is the short version; each row names the
test or script that demonstrates it, and every one of them was run to produce the
results in this document.

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

**What is PENDING, and why.** Two items cannot be closed in this environment, and
both are recorded rather than worked around:

* **Real-call translation acceptance.** No provider credential exists here. The
  adapter interface and everything credential-independent is done; no mock
  translation is used to claim completion, and no translation-quality figure is
  reported. See [limitations §2](docs/limitations.md).
* **A real-world document corpus.** Every sample is authored by this repository's
  generator, so the measured layout and OCR figures describe this corpus, not
  published papers. See [limitations §1](docs/limitations.md).

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

## Verify

Every command below is runnable from the repository root and prints its own result.
Nothing here depends on a service being started.

```powershell
# Unit tests (343 tests)
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

| Stage | n | P50 | P95 | Max | Threshold | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Open a PDF | 14 | 1.7 ms | 2.1 ms | 2.2 ms | — | — |
| Extract text, one page | 31 | 2.4 ms | 5.2 ms | 7.8 ms | — | — |
| First-screen preview render | 14 | 45.8 ms | 212.7 ms | 292.1 ms | ≤ 3 s | **PASS** |
| Page-turn preview render | 48 | 40.6 ms | 214.5 ms | 278.5 ms | ≤ 300 ms | **PASS** |
| Scan detection, one page | 31 | 1.2 ms | 4.1 ms | 4.3 ms | — | — |
| DOCX conversion | 6 | 69.7 ms | 154.1 ms | 154.1 ms | — | — |
| OCR engine load | 10 | 191.0 ms | 718.1 ms | 718.1 ms | — | — |
| OCR render, one page | 13 | 169.8 ms | 173.6 ms | 182.1 ms | — | — |
| **OCR recognition, one page** | 17 | **7.6 s** | **10.7 s** | **10.7 s** | — | **not interactive** |

Peak working set for the whole run: 1743 MiB, a batch figure across many documents
rather than a per-document footprint ([limitations §14](docs/limitations.md)).

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
| Body character coverage ≥ 98% | **PASS** | **0.9985** pooled over the 9 text-layer documents (84 of 56008 chars, normalisation only); `scripts/measure_layout.py` |
| Adjacent-block order ≥ 95% | **FAIL** | **0.8571** (210/245 pairs). Cause known and not fixed: full-width headers suppress column detection, so two-column bodies interleave |
| ≥ 200 sampled blocks ≥ 95% correct page and region | **FAIL** | **0.7835** (199/254 blocks) on the same cause; 254 blocks sampled, meeting the ≥200 requirement |
| OCR CER ≤ 5% on clear scans | **PASS** | recognition 0.0484 / 0.0092 / 0.0334 on the three clear scans, all ≤ 0.05 |
| OCR reading order | **PARTIAL, not claimed as pass** | 0.78–1.0 per page before layout reordering; see above |
| Protected placeholder integrity 100% | **PASS** | `tests/unit/test_cleaning.py`, including a percentage token that earlier went unprotected |
| ≥ 100 human-reviewed paragraphs ≥ 4/5 | **PENDING** | requires a real provider (limitations §2) |
| First screen ≤ 3 s | **PASS** | 5.2 ms P95 text extraction, 212.7 ms P95 preview render, 2.1 ms P95 open |
| Page turn ≤ 300 ms | **PASS** | 214.5 ms P95, 40.6 ms P50, worst case 278.5 ms |
| Anomaly handling | **PASS** | 11/11 handled as intended by `scripts/spike_extract.py` |
| Job recovery, retry, fencing | **PASS** | `scripts/smoke_storage.py` |

The layout items are measured over **text-layer pages only**. A scanned page has no
text layer, so the extractor returns nothing for it and OCR is required; averaging
such a page into a text-layer coverage figure reported 0.39 for `mixed_f_hybrid`, whose
first page is an image, while every text page scores 0.997 or better.
`scripts/measure_layout.py` names the pages it could not judge instead of hiding them.

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
packages/document_core/   domain logic: coordinates, cleaning, ids, layout, revision
packages/contracts/       Pydantic wire models and the generated OpenAPI document
adapters/rr_adapters/     PDF, DOCX and OCR engines - the licence-confined layer
services/api/             storage, services, worker (M1)
benchmarks/rr_m0/         corpus generator, OCR metrics, performance harness
scripts/                  verification, calibration and diagnostic tooling
tests/unit/               unit tests
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
