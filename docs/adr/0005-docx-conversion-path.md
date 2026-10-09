# ADR 0005: A built-in DOCX renderer as the degraded conversion path

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Refines: spec §5.4

## Context

A DOCX has no fixed pagination. It describes a flow of paragraphs, tables and
drawings with styles, and the page boxes a reader sees come from Word's layout
engine. The reading contract needs page boxes and coordinates (ADR 0008), and the
spec explicitly forbids presenting a page-level location as a precise paragraph
location.

So a DOCX has to yield two things: the document's real structure, which
`python-docx` reads faithfully, and *some* paginated rendering to anchor against.

## Decision

Use two representations, and use each for what it is good at:

* **Structure and text** come from `python-docx` reading the OOXML body:
  paragraph order, styles, heading levels, tables, images, equations (OMML) and
  footnote text.
* **Page boxes and coordinates** come from a PDF rendering produced by a built-in
  ReportLab renderer in this repository. Anchors refer to the rendering.

The conversion declares its fidelity explicitly, per capability, with
`reproduced` / `approximated` / `not_reproduced` / `derived` / `substituted`, and
those declarations become user-visible issues on the document rather than
internal notes. An external converter can be supplied through the same adapter
interface.

## Alternatives considered

**A. LibreOffice headless (`soffice --convert-to pdf`).** The best fidelity
short of Word itself, and the usual answer. Rejected *in this environment* on
evidence: no `soffice` binary is installed, and the spec requires the converter
to be verified on the target system before it is relied on. It also adds a
large external installation and a subprocess that must be sandboxed against
macros and remote content.

**B. Microsoft Word via COM automation.** Highest fidelity. Rejected: requires a
Word installation and a licensed desktop Office on the server, is Windows-only,
and drives a GUI application from a service. Not viable as a default.

**C. Read structure only, and display the DOCX without page anchoring.** Least
work, and honest about pagination. Rejected because it removes the product's
central feature for a whole input format: there would be no bidirectional
anchoring at all for DOCX, and the spec requires DOCX support with evidence
anchors.

**D. Structure from python-docx plus a built-in paginated rendering.** Chosen.
Pagination is explicitly *derived* and labelled as such, so the product never
claims Word's pagination; the structure is read from the real file, so headings,
tables and equations are not guesses.

## Consequences

### What is faithful

Paragraph text and order, headings and their levels, table cell text, embedded
images, and equation text content are read from the OOXML. These are the things
the reader, the search index and the translator actually consume, and they come
from the document rather than from a rendering.

### What is not, and is declared as not

* **Text boxes, shapes and WordArt** are not extracted. Declared as
  `not_reproduced` with severity `error`, because text is genuinely missing.
* **Floating and wrapped objects** are placed inline in document order. Declared
  as `not_reproduced`.
* **Tracked changes, comments and revision marks** are not displayed; the
  rendering shows the current text. Declared as `not_reproduced`, and detected by
  scanning for insertion/deletion elements so the warning only appears when it is
  true.
* **Pagination** is ours, not Word's. Declared as `derived`.
* **Fonts** are substituted with built-in Type1 faces (and a CJK face where the
  text needs one), so line breaks and metrics differ. Declared as `substituted`.
* **Lists** lose their numbering scheme and level. Declared as `approximated`.
* **Equations** keep their text but lose layout: fractions, matrices and
  multi-line structures are flattened. Declared as `approximated`.
* **Footnotes** are read from `word/footnotes.xml` and rendered at the end of the
  text, not at the foot of the page. Declared as `approximated`.

### Safety

The DOCX is treated as untrusted input. The archive is inspected before anything
is extracted: the compression ratio and the total uncompressed size are both
checked against `DecompressionBomb`, and a rejection names the measured ratio and
size. An absolute size limit and a ratio limit are both needed, because a large
legitimate document has a large uncompressed size while a zip bomb is defined by
the ratio.

### Testability

The rendering is deterministic, which makes it usable as a test fixture: the same
DOCX produces byte-identical layout, so a regression in the converter shows up as
a changed box rather than as a flaky rendering. This is a benefit of owning the
renderer, and it is the main reason the approach is acceptable despite its
fidelity limits.

## Revisit if

* A verified LibreOffice installation becomes available on the target system, at
  which point it should be preferred and this renderer kept as the fallback for
  machines without it.
* DOCX becomes a primary input format rather than a supported one, which would
  justify the fidelity work.
* Users report that missing text boxes or flattened equations cause real
  translation errors. The declared issues make this measurable rather than a
  matter of opinion.
