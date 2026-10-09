# Architecture decision records

Each record states the decision, the alternatives that were actually compared,
the measured or observed basis for the choice, and what would force a revisit.
Records are immutable once accepted: a changed decision is a new record that
supersedes the old one, because the point is to keep the reasoning auditable.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-frontend-without-build-step.md) | Serve the web frontend without an npm build step | accepted |
| [0002](0002-pdf-js-vendored.md) | Vendor PDF.js instead of installing from npm | accepted |
| [0003](0003-pymupdf-for-pdf.md) | PyMuPDF as the PDF engine, with an AGPL obligation recorded | accepted |
| [0004](0004-rapidocr-for-ocr.md) | OCR engine selection | pending measurement |
| [0005](0005-docx-conversion-path.md) | Built-in DOCX renderer as the degraded conversion path | accepted |
| [0006](0006-json-schema-wire-contract.md) | Pydantic models as the single wire contract | accepted |
| [0007](0007-single-worker-with-lease.md) | One worker process with leased, fenced jobs | accepted |
| [0008](0008-normalised-coordinates.md) | Normalised coordinates with an explicit transform | accepted |
| [0009](0009-rotation-transform-measured.md) | The rotation transform is measured against the renderer | accepted |
| [0010](0010-recognition-and-order-measured-separately.md) | Recognition accuracy and reading order are measured separately | accepted |
| [0011](0011-pymupdf-text-frame.md) | PyMuPDF text boxes are in the unrotated media frame, not raw PDF space | accepted |

## Not decided yet

These are deliberately left open because the spec defers them or because the
evidence is not in yet. Listing them here is what keeps them from being decided
by accident:

* **Translation provider.** The spec (16) fixes an adapter interface, not a
  vendor. No credentials exist in this environment, so the real-call acceptance
  item is PENDING and no mock is used to claim completion.
* **Real-world PDF corpus.** Every document in the corpus is authored by this
  repository (see ADR 0004). That bounds what reading-order and coverage numbers
  can claim: they describe this corpus, not published papers.
* **Deployment and packaging.** Out of P0 scope.
* **Annotation conflict resolution across parse revisions.** The migration
  machinery exists and marks unreliable matches; the interactive policy is a
  product decision that has not been made.
