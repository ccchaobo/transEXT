# ADR 0008: Normalised coordinates with an explicit transform

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Refines: spec §8

## Context

Every feature in the product depends on one question: given a rectangle on a
page, which text is in it, and given a piece of text, where is it on the page?
The rectangle has to be stored somewhere and compared across sessions, across
page renders, and across re-parses.

A PDF page is not a stable coordinate system. Its user space has its origin at
the bottom-left with `y` increasing upwards. Its visible area is the intersection
of the media box and the crop box. It may carry a `/Rotate` of 0, 90, 180 or 270.
The rendered image may be produced at any resolution, and the browser may report
pointer positions scaled by the device pixel ratio. A scanned page may
additionally be skewed, so the *content's* coordinate frame is rotated relative
to the page's.

Storing pixel coordinates would mean every consumer re-deriving the same
transform, and any disagreement produces anchors that drift by a few pixels — the
class of bug that is invisible in a screenshot and obvious to a user who clicks a
sentence and lands on the paragraph above it.

## Decision

Store normalised coordinates: the origin is the **top-left of the displayed
page** and values run 0–1 along each axis. A box is
`(x0, y0, x1, y1)` with `x0 <= x1` and `y0 <= y1`, clamped to `[0, 1]`.

The conversion is not left implicit. Everything that maps between spaces is an
`AffineTransform` (row-vector convention, `p * M + t`), and the transforms
applied to a page — rotation, crop offset, deskew, render scale, device pixel
ratio — are composed explicitly via `.then()` and inverted via `.invert()` when a
click has to travel back to raw space.

The unrotated raw PDF box is retained alongside the normalised box whenever it is
known, so a change to the normalisation itself is recoverable rather than
lossy.

Page indexing is fixed as part of the same contract: the internal page index is
**0-based**, and the number shown to the user is **1-based**. The wire model
exposes `page_number` and never a bare `page_index`, so the two cannot be
confused at a call site.

## Alternatives considered

**A. Raw PDF user-space points.** No lossy conversion and no clamping. Rejected:
it makes the crop box and `/Rotate` every consumer's problem, and it does not
survive the transition to a raster rendering, which is where the reader actually
displays things.

**B. Pixel coordinates at a fixed reference DPI.** Easy to reason about for one
render. Rejected: the reference DPI becomes an implicit global constant that
every stored anchor depends on; changing it (or the preview resolution)
invalidates every anchor silently.

**C. Normalised coordinates with an implicit, documented transform.** Less code.
Rejected: an implicit transform is exactly the failure mode this record exists to
prevent. "Documented" does not mean "enforced", and nothing would catch a
consumer that forgot the crop offset.

**D. Normalised coordinates with an explicit transform.** Chosen.

## Consequences

* Anchors are resolution-independent, so the preview DPI can change without
  invalidating stored annotations.
* A coordinate check can be written without reference to any particular render:
  comparing a normalised box against an independently computed one is a real
  test, not a tautology.
* Clamping means a box that genuinely extends outside the page loses that
  information. This is deliberate: the contract is about what is visible. The raw
  box is kept where the out-of-page extent matters.
* Rotation must be handled in exactly one place. The corpus includes a page
  rotated by 90° precisely so that a wrong implementation is caught by a test
  rather than by a user.
* Skew is expressed as a transform, not baked into the stored coordinates. A
  deskewed image and its stored anchors therefore stay consistent; the residual
  skew is reported as a quality issue rather than silently absorbed.

## Verification

The property that makes this testable is that page geometry is independently
computable from the file: the PDF itself states its media box, crop box and
rotation. A test can therefore compare an extracted box against a box computed
from those stated values, rather than comparing the implementation to itself.
Ground truth recorded at draw time is used for content checks; geometry checks
use the file's own declared page metrics.
