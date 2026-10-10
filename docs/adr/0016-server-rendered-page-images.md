# 0016. The source pane renders server-produced page images, not PDF.js

Status: accepted
Date: 2026-10-10
Supersedes part of: ADR 0002 (vendored PDF.js in the browser)

## Context

ADR 0001 and 0002 chose vanilla ES modules with a vendored PDF.js so the browser could render
the page and the text layer itself. The reason PDF.js was wanted is sound: it gives selectable
text in the source pane and zoom without re-fetching a raster.

Two things changed what is actually achievable and actually needed:

* **PDF.js cannot be obtained here, and therefore cannot be verified.** `registry.npmmirror.com`
  serves the package *metadata* for `pdfjs-dist` 4.6.82, but the tarball itself fails at the TLS
  handshake (`schannel: failed to receive handshake`) from this machine, and `cdn.jsdelivr.net`,
  `unpkg.com` and `registry.npmjs.org` are all unreachable. Vendoring means shipping a ~10 MB
  binary artifact that nothing here can checksum against its published hash. Adding a dependency
  that cannot be verified is worse than not adding it.
* **The server already produces exactly the raster the pane needs, and it is measured.** The
  parse stage renders a page preview and a thumbnail; the acceptance record has preview render at
  **212.7 ms P95** and the images are already served from
  `GET /documents/{id}/pages/{n}/preview.png`. The page's spans are already published in
  **display-normalised coordinates** — origin top-left of the displayed page, values 0–1 — which
  is the frozen contract from the spec.

## Decision

The source pane shows the server-rendered PNG for the page, with the block overlays positioned
directly from each span's normalised `bbox`. No PDF.js, no vendored binary, no npm build step.

This required a change on the server, which is why the ADR exists rather than just a preference:
the parse stage was writing `preview_path=None` with the comment *"PDF.js renders pages in the
browser, so no preview is stored."* That was true under ADR 0002 and became false the moment the
browser stopped rendering. Rendering is now a named stage of the parse — `render`, after `persist`
— so its cost is visible in the job's units, and a page whose image cannot be produced keeps its
text, gets its own warning and its own failed unit, and reports the parse as
`partially_succeeded` rather than losing the page.

The overlay is positioned with percentages of the image box:

```css
left: x0 * 100%; top: y0 * 100%;
width: (x1 - x0) * 100%; height: (y1 - y0) * 100%;
```

That is not a coincidence or a convenience — it is the coordinate contract being useful. Because
spans arrive normalised against the *displayed* page, and the preview is the displayed page, the
overlay is correct for a rotated or cropped page without the browser knowing the rotation exists.

## What this gives up, and what it does not

**Given up.** Text selection and copy inside the source pane, and vector-sharp zoom. The source
pane is a raster at a fixed resolution; zooming past it is interpolation. Selection and copying
still work in the translation pane, and block text is available from the API, but a reader who
wants to select a sentence in the original and copy it cannot do so from the image.

**Not given up.** Anchoring, which is the feature the product is for. Bidirectional
source↔translation highlighting is driven by block ids and span boxes, both of which are present,
so hovering either pane highlights the other exactly. The anchoring does not depend on PDF.js in
any way.

**Not decided.** If PDF.js becomes obtainable and its integrity can be checked against a
published hash, this ADR should be revisited: the win would be text selection in the source pane
and the loss would be a 10 MB vendored artifact plus a dependency to track. That is a real
tradeoff and it is not settled here.

## How it was verified without a browser

There is no browser automation in this environment, so "the overlay lines up with the page" was
not asserted by eye or by screenshot. It is measured by `scripts/verify_overlay_alignment.py`,
which renders the preview PNG and compares the page's ink against the boxes.

**Result: PASS.** Over 31 rendered pages of 14 corpus PDFs and 1024 spans:

| measurement | value |
| --- | --- |
| page image aspect ratio vs declared display box | 31/31 agree within 2% |
| span boxes holding any ink | 1024/1024 |
| blank margin strips are pure white | 31/31 pages |
| box coverage vs page ink, horizontal bands | **r = +0.972** (its mirror: +0.334) |
| box coverage vs page ink, vertical bands | **r = +0.995** (its mirror: +0.356) |
| pages passing both correlations at r ≥ 0.6 | 18/18 text pages |

The mirror column is the point. If the coordinates were reflected about either axis, the coverage
profile would be reversed against the ink profile and those two numbers would trade places. They
are not close: +0.972 against +0.334 is a discriminating pair, not a threshold being cleared.

### The first two versions of that measurement were wrong, and how

This is recorded because both failures looked like failures of the overlay, and one of them was
the exact shape of a real past defect.

**Attempt 1 — ink inside the box vs ink on the whole page, requiring 3x.** It reported 37% of
spans passing. The corpus pages are densely set two-column text where 5-7% of *every* pixel is
ink, so a line's box holds only ~1.5-2x the page average even when it is exactly on the line.
The page average is not blank paper. Worse, this metric **cannot fail on the bug it exists for**:
the project's original coordinate defect was a vertical mirror, and on a page of continuous text
the mirrored position is also on text. A metric that passes under a known bug is not evidence.

**Attempt 2 — each span against its own vertical mirror, row-by-row profiles.** The per-span form
was noisy (3.5% of spans "failed" while the overlay was correct), and the row profile failed
badly: row correlation +0.439 against column correlation +0.929 on the same pages. The reason was
the metric, not the overlay — `ink[y]` oscillates with the line pitch (a text row is inked, the
leading under it is blank) while `cover[y]` is a constant step across a whole block, leading
included. That disagreement was most of the variance. It was measuring the rasterisation of
leading. Averaging into 24 bands per axis removed it and the row correlation went to +0.972.

Both attempts are kept in the script's docstring so the next person does not rebuild them.

