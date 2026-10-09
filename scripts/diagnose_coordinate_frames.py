"""Which coordinate frame does PyMuPDF's span bbox use, and does the adapter match truth?

The layout metrics reported every region as overlapping nothing, with reference and
paragraph y coordinates summing to 1.0.  That means one of the two is vertically
mirrored.  This compares, for the same text, the span box PyMuPDF reports, the
adapter's normalised box, and the reference box, so the discrepancy is located
rather than argued about.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from rr_adapters.pdf_extract import PdfTextExtractor, page_geometry

CORPUS = Path("benchmarks/corpus")
NAME = "pdf_a_single_column"
PDF = CORPUS / "files" / f"{NAME}.pdf"


def main() -> int:
    truth = json.loads((CORPUS / "truth" / f"{NAME}.json").read_text(encoding="utf-8"))
    doc = pymupdf.open(str(PDF))
    page = doc.load_page(0)

    print("=== page frames ===")
    print(f"  page.rect    : {page.rect}")
    print(f"  page.mediabox: {page.mediabox}")
    print(f"  page.cropbox : {page.cropbox}")
    print(f"  page.rotation: {page.rotation}")

    geo = page_geometry(page)
    print(f"\n=== geometry the adapter builds ===")
    print(f"  display_width={geo.display_width} display_height={geo.display_height}")
    print(f"  rotation={geo.rotation} crop={geo.crop}")
    to_norm = geo.raw_to_norm()
    print(f"  raw_to_norm coefficients: {to_norm}")

    print("\n=== the truth's first page blocks (raw PDF space, from the generator) ===")
    for block in truth["pages"][0]["blocks"][:4]:
        print(
            f"  #{block['index']} {block['type']:9} bbox={[round(v, 1) for v in block['bbox']]} "
            f"{block['text'][:40]!r}"
        )

    print("\n=== PyMuPDF span boxes for the same text ===")
    data = page.get_text("dict", sort=False)
    shown = 0
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(s.get("text", "") for s in spans)
            if not text.strip():
                continue
            x0 = min(s["bbox"][0] for s in spans)
            y0 = min(s["bbox"][1] for s in spans)
            x1 = max(s["bbox"][2] for s in spans)
            y1 = max(s["bbox"][3] for s in spans)
            print(
                f"  span bbox=({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f}) "
                f"norm=({to_norm.apply(x0, y0)[0]:.3f},{to_norm.apply(x0, y0)[1]:.3f},"
                f"{to_norm.apply(x1, y1)[0]:.3f},{to_norm.apply(x1, y1)[1]:.3f}) "
                f"{text[:40]!r}"
            )
            shown += 1
            if shown >= 5:
                break
        if shown >= 5:
            break

    print("\n=== what the adapter itself returns ===")
    extraction = PdfTextExtractor().extract(PDF)
    for line in sorted(extraction.lines_by_page[0], key=lambda l: l.bbox.y0)[:5]:
        print(
            f"  adapter bbox=({line.bbox.x0:.3f},{line.bbox.y0:.3f},"
            f"{line.bbox.x1:.3f},{line.bbox.y1:.3f}) {line.text[:40]!r}"
        )

    print("\n  DIAGNOSIS")
    title_truth = truth["pages"][0]["blocks"][0]
    adapter_top = min(
        extraction.lines_by_page[0], key=lambda l: l.bbox.y0
    )
    print(f"  truth title bbox (raw)      : {[round(v, 1) for v in title_truth['bbox']]}")
    print(f"  truth title text            : {title_truth['text'][:50]!r}")
    print(f"  adapter topmost line (norm) : "
          f"({adapter_top.bbox.x0:.3f},{adapter_top.bbox.y0:.3f})")
    print(f"  adapter topmost line text   : {adapter_top.text[:50]!r}")
    print(
        "\n  The title is drawn at the TOP of the page.  In a top-left-origin "
        "normalised\n  space its y must be near 0.  If the adapter reports it near 1, "
        "the adapter (or\n  this consumer of it) is using a bottom-left origin."
    )
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
