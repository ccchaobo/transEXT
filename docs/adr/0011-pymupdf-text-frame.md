# ADR 0011: PyMuPDF text boxes are in the unrotated media frame, not raw PDF space

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Fixes: every extracted text line's coordinates were vertically mirrored
* Related: ADR 0008 (the coordinate contract), ADR 0009 (the rotation transform)

## Context

The coordinate contract (ADR 0008) says every stored box is in normalised display
space: origin at the top-left of the **displayed** page, y increasing downwards, and
raw PDF space retained separately with its bottom-left origin and y increasing
upwards. `PageGeometry.raw_to_norm()` implements the conversion and ADR 0009 verified
it against rendered pixels for all four rotations.

The PDF adapter extracted lines with `page.get_text("dict")` and passed each span box
straight into `raw_to_norm()`. That treats PyMuPDF's box as a *raw* box, which it is
not.

Nothing caught this because each piece was tested in isolation. ADR 0009's pixel test
exercises `raw_to_norm` on values it constructs itself from the *raw* markers it draws;
it never asks the adapter what box a known piece of text comes back with. The unit
tests all passed with a mirrored adapter.

It was found by measuring the adapter's output for one known string against the pixels
the renderer produced for that string:

| | rows (of 1170) |
| --- | --- |
| title as rendered | 92–110 |
| title as the adapter placed it | 1054–1086 |

The title was drawn at the top of the page and reported at the bottom. The two
coordinates summed to 1.0, the signature of a vertical mirror.

## What was measured

Two properties of `get_text` were established by experiment, because the documentation
was not available in this environment and both had to be right for the fix:

1. **The frame is the unrotated media frame.** A word drawn at a fixed page position
   was extracted from the same page rotated by 0, 90, 180 and 270 degrees: `get_text`
   returned *identical* boxes every time, while the rendered pixels moved to the
   expected corners. So `get_text` does not apply the page's rotation, and the adapter
   must not apply it either — `raw_to_norm` already does. Applying the rotation in both
   places would be wrong in a second, less obvious way.
2. **The frame is relative to the media box, with a top-left origin and y downwards.**
   The flip therefore uses the media height, not the crop height.

## Decision

Convert each text box into raw PDF space before transforming it:

```python
raw_box = BBox(box.x0, geo.media_height - box.y1, box.x1, geo.media_height - box.y0)
```

The x axis needs no change, because both frames start at the left edge. Rotation is
left to `raw_to_norm`.

## Alternatives considered

**A. Flip the y coordinate after transforming, instead of before.** Equivalent for an
unrotated page, wrong for rotated ones: the output of `raw_to_norm` is in the *rotated*
display frame, where a flip is a horizontal mirror, not a vertical one. Rejected.

**B. Read `page.rotation` and apply the rotation to the output of `get_text` here.**
Rejected because it duplicates `raw_to_norm` and, per measurement 1, would rotate a
frame that is already unrotated at extraction time.

**C. Stop using `get_text("dict")` and reconstruct boxes from `get_text("words")`.**
Rejected: `"words"` has the same frame, so it solves nothing.

**D. Store boxes in PyMuPDF's frame and convert at render time.** Rejected: it breaks
the frozen contract, which exists precisely so that stored anchors stay valid
regardless of who renders them.

## Consequences

* Region accuracy rose from **0.0208 to 0.7835** on the layout acceptance run. Before
  the fix, essentially every block was attributed to the wrong part of its page, so
  every highlight and source anchor in the product would have pointed at the wrong
  place. This was the single most severe defect found in the project so far.
* The remaining region errors are a different, known problem (column interleaving when
  a full-width header sits above a two-column body), not a coordinate problem.
* A per-line reconciliation now exists: every line the corpus recorded as drawn is
  found in the extractor's output, which is what would have caught this immediately.
* The lesson recorded for the test suite: a transform verified only against values the
  test itself constructs proves the transform, not its *use*. Every adapter that uses
  `raw_to_norm` needs at least one check that goes through the real file and compares
  against independently obtained evidence about where the content is.

## Verification

* `python scripts/diagnose_adapter_vs_render.py` — compares the adapter's box for a
  known title against the rendered text rows.
* `python scripts/verify_coordinate_fix.py` — for all four rotations, puts a marker at
  a known text-frame position, predicts its normalised position, and locates it in the
  render. All 16 markers agree within 0.0013, and the output prints what the unfixed
  handling would have produced for comparison.
* `python scripts/diagnose_gettext_rotation.py` — the measurement that established
  property 1.
* `python -m pytest tests/unit` — 343 tests.
