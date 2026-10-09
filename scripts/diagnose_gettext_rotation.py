"""Does ``get_text`` report boxes in the rotated frame or the unrotated crop frame?

The adapter's correctness depends on this and the documentation is not available
here, so it is measured.  A word is drawn at a known place in the top-left frame of
an unrotated page; the page is then rotated and the same word extracted again.  If
the reported box moves, ``get_text`` works in the rotated display frame (which is
what the frozen normalised contract wants).  If it does not move, the adapter has to
apply the rotation itself.
"""

from __future__ import annotations

import pymupdf

W, H = 400.0, 600.0
#: Drawn near the top-left of the unrotated page, in page units (y down).
X, Y = 50.0, 40.0


def main() -> int:
    for rotation in (0, 90, 180, 270):
        doc = pymupdf.open()
        page = doc.new_page(width=W, height=H)
        page.insert_text((X, Y), "MARKER", fontsize=12)
        page.set_rotation(rotation)
        page = doc.load_page(0)

        words = page.get_text("words")
        span_boxes = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if "MARKER" in span.get("text", ""):
                        span_boxes.append(span["bbox"])

        print(f"=== rotation {rotation} ===")
        print(f"  page.rect   : {page.rect}")
        print(f"  page.rotation: {page.rotation}")
        for word in words:
            if "MARKER" in word[4]:
                print(
                    f"  words bbox  : ({word[0]:.1f},{word[1]:.1f},"
                    f"{word[2]:.1f},{word[3]:.1f})"
                )
        for box in span_boxes:
            print(
                f"  span bbox   : ({box[0]:.1f},{box[1]:.1f},{box[2]:.1f},{box[3]:.1f})"
            )
        w = page.rect.width
        h = page.rect.height
        for box in span_boxes:
            print(
                f"  normalised by page.rect: "
                f"({box[0] / w:.3f},{box[1] / h:.3f},{box[2] / w:.3f},{box[3] / h:.3f})"
            )
        doc.close()
        print()

    print(
        "Interpretation: 'MARKER' was drawn at page-units (50, 40), i.e. near the\n"
        "top-left of the unrotated page.  If the normalised position changes with\n"
        "rotation, get_text already reports in the rotated display frame, and\n"
        "normalising by page.rect is sufficient."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
