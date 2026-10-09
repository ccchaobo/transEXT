"""Are the unmatched reference blocks a placement failure or a merge failure?

`region_accuracy` divides by the blocks that were *matched*, so a block that the
pipeline merged into a larger paragraph is reported as a coverage miss, not a region
miss.  Those are different defects with different fixes, so this counts them separately
and shows what the unmatched blocks look like.
"""

from __future__ import annotations

import json
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import BODY_TYPES, match_blocks_to_paragraphs
from measure_layout import load_truth_blocks

CORPUS = Path("benchmarks/corpus")


def main() -> int:
    extractor = PdfTextExtractor()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        e["name"]
        for e in sorted(manifest["entries"], key=lambda e: e["name"])
        if e["kind"] == "text_pdf"
    ]
    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        result = analyse(extraction.lines_by_page)
        blocks = load_truth_blocks(truth, extraction.geometry_by_page)
        body = [
            b
            for b in blocks
            if b.block_type in BODY_TYPES
            and b.has_text
            and not any(
                i.index == b.page_index and i.likely_scanned
                for i in extraction.page_infos
            )
        ]
        paragraphs = [
            (p.text, tuple(sorted(p.page_indices or [0])), tuple(p.bbox.as_list()))
            for p in result.paragraphs
        ]
        report = match_blocks_to_paragraphs(body, paragraphs)
        matched = [m for m in report.matches if m.paragraph_index is not None]
        print(f"=== {name} ===")
        print(
            f"  reference body blocks={len(body)}  matched={len(matched)}  "
            f"unmatched={len(report.unmatched_blocks)}  paragraphs={len(paragraphs)}"
        )
        # Does the unmatched block's text appear *inside* some paragraph?  If so the
        # pipeline merged it and the block is not lost.
        merged = 0
        for b in report.unmatched_blocks[:40]:
            needle = b.text.strip()[:40]
            if not needle:
                continue
            hit = next((p for p in paragraphs if needle in p[0]), None)
            if hit is not None:
                merged += 1
        print(f"  of the unmatched, text is contained in a paragraph: {merged}")
        for b in report.unmatched_blocks[:5]:
            print(f"    unmatched {b.block_type:9} p{b.page_index} "
                  f"n_lines={b.n_lines} {b.text[:56]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
