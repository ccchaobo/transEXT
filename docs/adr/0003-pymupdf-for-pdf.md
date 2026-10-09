# ADR 0003: PyMuPDF as the PDF engine, with its licence obligation recorded

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation

## Context

The product needs one PDF library to do all of the following on a local machine:
extract text with per-span boxes and font metadata, report page geometry
(media/crop box, rotation), render pages to raster images for preview and for
OCR, inspect embedded images, and detect encryption.

The candidate libraries available for this Python version are PyMuPDF, pypdf,
pdfplumber (which uses pdfminer.six), and pdf2image (a wrapper around the Poppler
binary).

## Decision

Use **PyMuPDF** as the PDF engine.

## Alternatives considered

**A. pypdf alone.** Already a dependency and pure Python, and it is used for the
document-level operations where it is a good fit (encryption detection, metadata,
page counting). Rejected as the *primary* engine because it does not render pages
and does not provide per-span coordinate boxes with font metadata. Rendering is
required for previews and is a prerequisite for the OCR path, so a second library
would be needed anyway.

**B. pdfplumber / pdfminer.six.** Excellent text extraction with character-level
boxes, which would suit the extraction task well. It still does not render pages,
so the OCR path and previews would need Poppler or another renderer. Adding a
second extraction engine also means two different notions of a "line" and a
"span", and reconciling them is work that buys nothing for this product.

**C. pdf2image + Poppler.** Rendering only; Poppler's binaries are not installed
here and would add a non-Python installation step to a tool whose value
proposition is that it runs locally without ceremony. Poppler is also not present
as a Python package, so it cannot be verified by the project's own test suite.

**D. PyMuPDF.** Chosen. One library covers text+boxes, geometry, rendering,
embedded image inspection and encryption. It is the only candidate that covers
rendering at all without an external binary, and rendering is not optional here.

## Consequences

### Licence

PyMuPDF's metadata states:

```
License: Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License
```

This is recorded in the dependency inventory as a **distribution obligation, not
a development obstacle**. AGPL-3.0 is a copyleft licence: distributing this
application, or operating a modified version as a network service, carries source
disclosure obligations under AGPL terms. Internal use, modification for personal
use, and evaluation do not.

The practical instruction is that a commercial or network-service distribution
requires either accepting AGPL-3.0 or buying the Artifex commercial licence. This
must be re-checked before any distribution. It is written down here, and in the
dependency inventory, so that it cannot be discovered late.

The obligation is contained: PyMuPDF is used behind the `DocumentParser` adapter
interface, so it can be replaced without touching the API, storage, contract or
frontend layers. The replacement candidates above all have permissive licences.

### Other consequences

* Extraction quality is upstream-dependent. The parser treats PyMuPDF output as
  evidence and does its own reading-order, paragraph and furniture decisions in
  `document_core`, so a library upgrade changes inputs rather than policy.
* PyMuPDF is not thread-safe per document handle, which constrains the worker
  design: one document is handled by one task at a time, and handles are not
  shared across threads.
* The library reports its own coordinate boxes in PDF user space; those are
  converted in exactly one place (`adapters/pdf_extract.py`) so the coordinate
  contract (ADR 0008) has a single point of enforcement.

## Revisit if

* The product is to be distributed commercially or operated as a network
  service, which makes the licence choice concrete.
* A permissively licensed engine gains page rendering, which would remove the
  licence obligation at no functional cost.
* Extraction quality on real-world PDFs proves insufficient, measured with the
  coverage metric on a corpus that includes third-party documents.
