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

**The heading was then separated from the paragraph under it** (ADR 0015). That was the last
piece: a heading's box is taller than a body line, so the gap to the next line came out
negative and the overlap tolerance accepted it, and neither gap test could see the boundary.
A type-size change can. Measured over 8 documents and 17 pages, same-column pairs only: within
a block the ratio below/above is a **constant 1.000** over 501 pairs, and across all 86
heading-to-next-block pairs it is at most 0.926. The rule breaks when the size changes by more
than 3%, which sits in the middle of that valley and is symmetric, so a heading absorbed into
the paragraph *above* it is caught too.

| measure | after ADR 0014 | after ADR 0015 |
| --- | --- | --- |
| corpus blocks addressable on their own | 87/175 (0.4971) | **235/246 (0.9553)** |
| block-type accuracy | 87/87 | **235/235** |
| page+region accuracy | 1.0000 (333 blocks) | **1.0000 (409 blocks)** |
| adjacent-block order | 0.9969 | **0.9975** |
| line-level order | 1.0000 | 1.0000 |

Only **10 of 246** corpus blocks are still absorbed into a longer paragraph, and 1 is a
matcher error. The earlier note here that "the corpus's own blocks are finer than any threshold
can recover" was **wrong** — the heading was recoverable from the type size, and the claim that
it needed new structure was an assumption rather than a measurement.

**Calibration caveat.** This corpus has a *constant* 12.40 pt leading, a 10.00 pt line box and
two or three type sizes, so both valleys are wider here than they will be on real papers with
mixed leading and footnotes. Both 0.25 and 0.03 are calibrated against this corpus, not against
published articles. Re-calibration on real papers is required before either can be called
settled.

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

## 20. The section path is the document title — resolved

This section used to record that every block of `pdf_b_two_column` carried
`section_path == ["Reading Order Recovery in Two-Column Layouts"]`, the paper's title, because
the layout stage pushed the title onto the section stack — a title is short and set larger than
the body, which is its test for a heading — while the real section headings never reached the
stack, having been merged into the paragraph below them (section 15).

That is fixed by ADR 0015. Measured in `scripts/evidence_translation.py`, the context sent for
each block is now its real section:

```
heading   '1. Introduction'
paragraph 'Reading order and evidence tracking are prerequisites for '  ctx='1. Introduction'
heading   '2. Related Work'
paragraph 'Layout analysis for two-column articles has a long history'  ctx='2. Related Work'
```

So the outline the reader navigates by now has real entries at real levels, and the context
passed to the provider names the section a block is in. Neither was true before.

One further guard was needed and is recorded because it was found by measuring rather than by
reasoning: once the headings were split off, `"1. Introduction"` was typed as the **title** in
all 8 documents. A numbered heading is short, is set larger than the body, and — for the first
section — sits near the top of page one, so position alone cannot separate it from the title.
`looks_like_title` now refuses a numbered section heading, which took the heading score from
70/78 to 78/78.

## 21. The outline column is never written

`reads.outline` used to select blocks with `level IS NOT NULL`, and returned **nothing for the
whole corpus**. The layout stage never writes that column: it tracks sections in `section_path`,
which is what made the translation context correct, but leaves `Block.level` null.

An outline that silently came back empty is worse than a missing feature, because an empty
outline and a document with no sections look identical. Two things were changed:

* the outline is now built from the block **type** — `title` and `heading`, which the classifier
  produces and which is measured at 235/235 on the corpus — with `section_path` depth used for
  nesting when `level` is absent;
* `GET /documents/{id}/outline` returns an explicit `note` when fewer than two headings were
  recognised, so a one-entry outline is never presented as navigation.

`Block.level` remains unwritten. Nesting therefore comes from the length of the section path,
which is one level coarser than a real heading hierarchy: a document with `2` and `2.1` headings
gets depth from that path rather than from the numbering, so a sub-section is not necessarily
distinguished from its parent.

## 22. A redundant child DELETE on document deletion, left visible

Deleting a document through the API emits a SQLAlchemy warning:

```
SAWarning: DELETE statement on table 'source_spans' expected to delete 185 row(s);
0 were matched.
SAWarning: DELETE statement on table 'blocks' expected to delete 57 row(s); 0 were matched.
```

The rows are gone and the deletion is correct — an integration test asserts that blocks, pages,
parse revisions and translations all count zero afterwards, and that the stored original is
removed from disk. What the warning says is that the ORM asked the database to delete rows the
database had already removed through the `ON DELETE CASCADE` the schema declares.

The documented remedy is `passive_deletes=True` on the document's child relationships, telling
the ORM to let the database do the work. **That was tried and reverted.** It broke import: three
existing deletion tests began failing with `FOREIGN KEY constraint failed` while inserting a job
event, before any deletion had happened at all. Reverting restored them, so the change was the
cause; the mechanism was not established in the time spent.

The choice was therefore to silence a warning whose behaviour was not understood, or to leave it
visible. It is visible. The cost is a redundant DELETE per child table on an operation that runs
once per document, and the warning is a correct signal about duplicated work rather than a
defect in the result. Anyone revisiting it should first explain why a relationship-level flag on
`Document` changes the insert ordering of `job_events`, which is the part that does not follow
from the documentation.

## 23. Six defects that only appeared when the server was actually run

The 607 tests that passed at the end of M1 did not find any of the following. Each was found
within minutes of starting `python -m rr_api`, uploading a file through HTTP and running
`python -m rr_worker` as a separate process. They are recorded individually because the *reason*
they were invisible is different in each case, and that reason is the useful part.

**23.1 A failed parse returned HTTP 500 from the document's own detail endpoint.** The issues of
the active revision were collected by a list comprehension:

```python
for issue in (revision.issues or []) if revision
```

This reads as though `if revision` guards the loop. It does not: the comprehension's `if` is a
*filter*, applied to each value, and the iterable is evaluated first. When `revision` was `None`
the endpoint raised `AttributeError: 'NoneType' object has no attribute 'issues'` and FastAPI
answered 500. Every existing test imported a document that parsed successfully, so `revision` was
never `None` in any of them; the one path where the reader most needs an explanation was the one
path never exercised. Fixed by guarding before the loop.

**23.2 A parse failure was recorded as `translation_failed`.** The worker mapped every handler
exception through `rr_services.translation.error_code_for`, whose fallback is a translation code,
because it was written for the translation service. Importing a DOCX therefore produced a job
whose error code pointed at the API key. The mapping is now per job type, with a vocabulary that
already existed in `ErrorCode` (`parse_failed`, `ocr_failed`, `conversion_failed`), and an
unimplemented format reports `unsupported_format` — which is what it is.

**23.3 A document kept `parse_status = "pending"` forever after its parse had failed.** Only a
successful parse wrote the field. A reader looking at the library saw a document still waiting
for work that had already finished badly. The worker now writes the stage status for the document
as well as the job, including partial and cancelled, because the status a reader sees has to
reflect what happened.

**23.4 `warning_count` counted only revision-level warnings.** A three-page scan showed
**0 warnings** on its library card while every one of its pages carried "no text layer on this
page; OCR is required". Page warnings are now aggregated into the count, so the card says 9.
The page-level text is also rendered above the page in the reader.

**23.5 Translating a document that does not exist answered `translation_not_configured`.** The
provider check ran before the document lookup, so a typo'd or deleted id sent the reader off to
configure an API key for a document that was never there. The more specific fact is now reported
first: 404 `not_found` for a missing document, 409 only for a real document with no provider.

**23.6 `python -m rr_worker --once` hung forever on an empty queue.** `max_jobs=1` means "stop
after one job", so with nothing queued it polled indefinitely. This was a command-line design
fault, not a queue fault, and it was found by running the CLI rather than the library. `--once`
and a new `--drain` now exit when the queue empties, after a `--idle-grace` period so that a
producer enqueueing the next job is not left for the next process.

The common thread is that these are all *seams*: a document with no revision, a job type that is
not translation, a process boundary, a command line. Unit tests exercise functions; they do not
exercise a seam, because a seam is the place where one component's assumptions meet another's.
`scripts/verify_live_acceptance.py` now starts both processes and asserts 37 of these behaviours
over real sockets, and `tests/integration/test_failure_visibility.py` pins the six above.

## 24. What is not yet wired into the parse stage

The OCR engine (RapidOCR, offline, measured at CER 0.0484 / 0.0092 / 0.0334 on the clear scans)
and the DOCX converter both exist and are tested. Neither is called by the parse stage:
`SUPPORTED_FORMATS = {"pdf"}` in `rr_services/parsing.py`, and the code says so rather than
falling through to an empty result.

The consequence is visible and deliberate: a DOCX is refused with `unsupported_format`, and a
scanned PDF **succeeds as a page listing with no blocks**, with `translatable = false` and a
per-page warning. This is the honest description of what happens, but it is also a real gap
against the specification, which requires OCR and DOCX import in P0. It is listed here so it
cannot be mistaken for done.

## 25. `BlockTextPatch` does not exist

Specification 5.3 requires correcting OCR text on a block, bumping `ocr_revision` and
invalidating the translations derived from it. There is no endpoint for this, and no column.
Since OCR is not wired into the parse stage either (see §24), nothing currently produces OCR text
that would need correcting.

## 26. Two of the verification entry points were not verification

`scripts/verify_all.py` runs every script that decides something and reported **26 of 28**
passing on its first full run. Both failures were faults in the scripts, not in the product — but
in each case the script would have convinced a reader that the product was broken, so both are
recorded and both were fixed rather than dropped quietly.

**26.1 `verify_rotation.py` reported six failures for a correct transform.** It drew a marker with
`page.draw_rect`, which takes the media frame (top-left origin, y down), and then passed that
point to `PageGeometry.raw_to_norm` as if it were raw PDF space (y up). The two frames differ by
exactly the flip the script was meant to be checking, so every case failed with mirrored numbers.
Its printed column headings also did not match its own format string - the column labelled
`pixel` held the geometry result and the one labelled `geometry` held the pixel measurement - so
the output read as the opposite of what it was. Both faults are now fixed.

The rewrite does something the repository did not have. Position agreement alone cannot detect
the defect this project actually shipped twice: a vertical mirror still puts all four corners on
all four corners of the image, still distinct, still inside the unit square. What a mirror breaks
is **handedness** - the winding direction of top-left, top-right, bottom-right, bottom-left - and
no rotation can reverse it. The script now checks positions *and* handedness for all four
rotations, and then runs the handedness test against a deliberately mirrored prediction and
requires it to fail. A mirror-detector that has never been shown a mirror is not evidence.

**26.2 `verify_order_by_eye.py` had no verdict at all.** It returns 0 whatever it prints, which its
own docstring states; listing it as verification was a category error in the harness, and it is
now excluded there. It also read `LayoutResult.bands_by_page`, an attribute that has never existed
on the current `LayoutResult`, so it raised `AttributeError` for anyone who ran it. It now reads
the per-page band summary the layout actually records. The general lesson is the same one §23
ends on: a script that cannot fail will be believed, and a script that crashes on a stale
attribute reads as a broken product rather than as a stale script.

## 27. `page_columns` was always 1, and has been removed

`LayoutResult.page_columns` reported the maximum number of columns across the page's bands. But
`segment_bands` is an XY-cut, so it has already separated the columns *before* `detect_columns`
runs; every band therefore contains exactly one column, and the figure is 1. Measured on the
corpus, it is 1 for every page of every document — for the two-column papers and the
single-column ones alike.

Nothing consumed it except two diagnostic prints and the `measure_layout.py` JSON artifact, and
no test referenced it, so it was removed along with its three usages rather than left in place
looking like data. The per-page band summary (`bands_per_page`, `columns_per_band`) replaces it.
A number that cannot vary is not a measurement, and the failure mode it invites — reading a
constant as a finding, or explaining a real defect away as an artefact of the constant — is one
this project has already been through once (§3).

## 28. Two verification scripts only passed on a clean checkout

`verify_frontend_contract.py` and `verify_overlay_alignment.py` each built their application
against a fixed directory inside the repository — `var/_frontend_contract` and `var/_overlay_check`.
The first full run of `verify_all.py` passed both. The second run failed the first of them:

```
File "scripts/verify_frontend_contract.py", line 71, in main
    assert worker.serve(poll_seconds=0, max_jobs=1) == 1
AssertionError
```

The reason is worth stating because it is not a flake. On the second run the corpus PDF was
already imported, so the content-hash deduplication returned the existing document and **created no
new job**; the assertion that exactly one job ran was therefore false. The script was asserting a
property of an empty data directory while appearing to assert a property of the product.

Both scripts now create a fresh temporary data directory per run, which also stops them writing
into the `var/` that a running server uses. This is recorded rather than quietly fixed because the
signature is general: **a verification whose result depends on what the previous run left behind
will pass once and fail afterwards**, and the temptation is to read the second failure as a
regression in the product.

Two smaller things came out of the same session. `TemporaryDirectory` needs
`ignore_cleanup_errors=True` on Windows, because the SQLite engine still holds the database file
open when the directory is removed — the checks pass and then the cleanup raises. And the README
named the database `research_reader.sqlite3`; it is `reader.sqlite3`. Both are corrected.

## 29. The performance harness measured a configuration that does not ship

`benchmarks/rr_m0/perf_harness.py` declared `--ocr-dpi` with a default of **300**, while
`rr_adapters/ocr.py` fixes the calibrated resolution at **250** — and rejects 300 in as many words:

```
#: 250    0.0247    5.76
#: 300    0.0298    6.19
#: 250 dpi is chosen because it halves the error of 200 dpi for about one second
#: per page, whereas 300 dpi costs more than 250 *and* recognises slightly worse.
OCR_RENDER_DPI = 250
```

So `python -m benchmarks.rr_m0.perf_harness`, the command the README documents, measured a
resolution the project had explicitly decided against. The recorded artifact said `"ocr_dpi": 250`
only because it had originally been produced with an explicit `--ocr-dpi 250`; running the
documented command again **overwrote** it with 300-dpi numbers, and because recognition is the
dominant cost, the headline figure moved too — peak working set from 1743 MiB to 2055 MiB. Nothing
warned, and the new artifact looked exactly as authoritative as the old one.

Found by diffing the regenerated artifacts during a full verification run, not by reading either
file: both are internally consistent, and only the pair disagrees. The default is now
`OCR_RENDER_DPI` itself, so the calibration has a single definition and a measurement cannot drift
from it. The artifact has been regenerated at 250 dpi and the README's table updated to match it.

The related weakness is that the table's P95s come from 14 and 48 samples and move by up to 9%
between runs of identical code (212.7 → 230.9 ms observed on the first-screen row). The threshold
verdicts are unaffected because the margin is large, but the individual millisecond figures are
single-run observations and the README now says so rather than presenting them as fixed.

## 30. A configured API key did nothing, because the worker never built a provider

The API's own refusal told the reader exactly what to do:

```
no translation provider is configured.  Set RR_TRANSLATION_API_KEY (and
RR_TRANSLATION_BASE_URL if the default endpoint is wrong) and restart the server.
```

Doing that did not work. `rr_worker/__main__.py` constructed the worker as
`Worker(runtime.settings, runtime.workspace, runtime.queue, database=runtime.database)` — with no
`provider` argument and no way to supply one. `worker.provider` was therefore `None` for the whole
run, `TranslationService.plan()` reported `provider='unconfigured'`, and **every** translate job
ended in the same `TranslationNotConfigured` the reader had just resolved. Following the
instruction produced a second, identical failure with nothing to explain it.

`tests/unit/test_worker.py` could not catch this. It builds `Worker` itself and passes a provider
explicitly — correct for a unit test, and exactly why the defect survived: the only broken wiring
was the CLI's, and the CLI is the one thing those tests do not construct.
`scripts/evidence_translation.py` also passed `provider=provider_from_env()` by hand, so the
evidence that translation worked was evidence about a path the product did not use.

The fix gives `Worker` a `provider_factory`, resolved **once per job** rather than once per
process, and the CLI supplies one that reads the settings in force. A provider built at startup
would pin the process to whatever the environment said when it began, which is the behaviour that
made a file on disk the only way in. `tests/integration/test_translation_settings.py` now builds
the worker the way the CLI does and runs a whole translation through it, so the missing wire
cannot come back unnoticed.

Two smaller defects surfaced while building this, both in the same direction — an error the reader
could not act on:

* **`TranslationRequest.scope` defaulted to `"pages"`** and then rejected a request that omitted
  `page_indices` with `400 scope='pages' needs page_indices`. The most natural call there is,
  `{}`, failed with a validation error naming a field the caller had no reason to know about. It
  now defaults to `"document"`, which is what "translate this paper" means and what the reader's
  own button already sent.

* **A malformed body produced FastAPI's own 422**, whose `detail` is a *list* of error objects
  while every refusal this API raises puts an `{"error_code", "message"}` object there. The reader
  maps `error_code`; given a list it had nothing to map, so a request with a mistyped field name
  was the one refusal the UI could not explain. There is now a `RequestValidationError` handler
  answering 400 in the contract's shape, and the settings model uses `extra="forbid"` — Pydantic
  drops unknown keys silently by default, so `{"modle": "m1"}` was accepted, stored as nothing and
  reported as a success.

## 31. The settings panel changes specification 11, at the owner's direction

Specification 11 says the frontend must never receive **or send** a provider key. The reader asked
to be able to enter a base URL, a model id and an API key in the settings panel, and a key typed
into a panel is a key the browser sends. The two cannot both hold. The owner chose the panel.

This is recorded as a deliberate deviation rather than an oversight, and the test that guarded the
old rule was **replaced, not deleted**. It now asserts something narrower and stronger:

* a secret-named field is permitted **only** in a schema reachable from a `requestBody`;
* it must not appear in any schema a client can receive — not in a success response, not in the
  error envelope, and not in `components.responses`;
* it may not be `required`, because a required key would make "leave it unchanged" impossible to
  express and editing only the model would resend the key;
* and the write path must still exist, so the test cannot be satisfied by declaring nothing.

The previous version searched `components.schemas` for forbidden *names*, which cannot tell a
request field from a response field. The new one walks the document, resolves `$ref`s from both
directions, and names the exact `METHOD path status` that can read a credential back. It was
checked against a negative control — `api_key` added to `DocumentSummary` — and it failed with
`DocumentDetail.api_key reachable from ['GET /api/v1/documents/{document_id} 200']`. A rule that
cannot fail on the thing it forbids is not a rule.

What is given up: the key now exists in a browser form, so a cross-site scripting flaw anywhere in
this application could read what the reader typed into it. What is not given up: the key is never
returned by any endpoint, it is stored with an `is_secret` flag that redacts it at the single place
the table is read whole, and it is reported only as a boolean. For a single-user reader bound to
loopback that trade was accepted knowingly.

## 32. The check that exists to explain a failure reported a code and nothing else

`POST /settings/translation/test` was written because a wrong base URL, a wrong model id and a
wrong key all look identical from the outside. It is the one feature whose entire value is the
explanation it returns. Sending it at an endpoint that answers `HTTP 502` with an **empty body**
returned:

```
ok=False  http_status=502  error_code=external_unavailable  error_message=""
```

The code, and then nothing. The panel rendered `external_unavailable：` and stopped — strictly less
useful than the failure itself, because the reader now has a code they cannot acton.

The empty body is the diagnosis and was being thrown away. A chat-completions service that refuses
always sends a reason; a 502 with nothing in it comes from something that is not the service. The
message now says so, and names the responder from its own headers when there are any.

The case is real, not hypothetical, and was measured: on this machine a request to the discard port
`127.0.0.1:9` returns HTTP 502 with an empty body and no `Server` header. **No proxy variable is set
in this environment**, which is why the message names a proxy as one possibility rather than as the
cause — what answered has not been identified, only detected. The regression test reproduces the
*shape* with a local responder rather than reaching for port 9, so the test does not depend on this
machine's port 9 being answered by whatever answers it.

The same run found a second, smaller defect: `DELETE /settings/translation` answered with
`{removed, effective}` while a read answered `{effective, stored, env_overrides}`. Two shapes for
one resource is how a client renders a stale panel after a successful clear — the panel worked only
because it happened to re-read afterwards. Both were found by calling the endpoint and reading the
bytes, not by reading the code, and neither was visible to the 25 tests that already covered this
endpoint.

## 33. `link_scrolling` was stored, returned, tested — and read by nothing

The reading state carries a `link_scrolling` flag. It is in the contract, in the `reading_states`
table, in the OpenAPI document, it is written on every page turn and on every view-mode change, it
is returned by `GET` and echoed by `PUT`, and `tests/integration/test_api.py` asserts that it
survives a round trip. The frontend has never once read it. There was no scroll listener anywhere
in the application.

So the two panes never followed each other. The only thing that ever moved the other side was a
click, which centres the matching block. A reader scrolling through a paper had to find the
matching paragraph on the other side by eye, every time — which is the complaint that led here.

This is the same shape as [`page_columns`](#27-page_columns-was-always-1-and-has-been-removed): a
value that exists, is persisted, is asserted about, and has no effect. A round-trip test proves a
value is *stored*; it says nothing about whether anything *acts on* it. That distinction is worth
keeping in mind for every other round-trip assertion in the suite, which is most of them.

The fix reads the flag and adds a **联动** checkbox that writes it, since a stored switch with no
control is the other half of the same problem. The link snaps from block to block rather than
interpolating a scroll fraction — see the note in the README for why nothing finer can be correct
here — and it has to distinguish the reader's scrolling from its own writes, or the two panes hand
the scroll back and forth and neither settles. That guard is a recorded scroll position plus a
short suspension window while a smooth `scrollIntoView` is in flight.

## 34. The overlay view is unverified, and cannot be verified here

The 译文覆盖 view is new code in the browser, and **nothing in this repository has ever executed
it on a page**. There is no browser automation available in this environment — that is ADR 0016's
whole reason for server-rendering page images — so the panels have never been seen, the text has
never been laid out, and the linked scrolling has never been observed to settle.

What *is* checked, and what it is worth:

* the modules parse (`node --check`), and every field the new code reads exists in a real payload,
  because `verify_frontend_contract.py` extracts those names from `app.js` and requires them
  present in the response from a real import;
* every translatable, non-verbatim block on every page of a real document has at least one span
  with a bbox and a union box inside the page — 54 blocks over 2 pages, with 1 verbatim block left
  uncovered. This is a statement about the **data**. It is not a statement about the rendering,
  and it would be easy to mistake for one;
* `overlay` round-trips through `PUT /reading-state`, which found a real defect immediately:
  `view_mode` is a `Literal` in the contract and the new mode was not in it, so the reader's own
  choice of view would have been refused with a 400 — and only when they picked it, at which point
  it looks like a setting that silently did not stick.

The check in the previous bullet was written, run, and **failed** for that reason before it was
fixed, which is the only evidence that it can fail at all. Everything past "the data is
well-formed" is PENDING and belongs to the person who can open a page. The three things most
likely to be wrong, in order: whether the panels land where they belong; whether the linked
scrolling settles instead of oscillating; and whether the overflow badge appears on the paragraphs
that need it and not on the ones that do not.




