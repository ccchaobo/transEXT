"""What exactly fails the region test?

Prints each matched block whose paragraph does not cover its region, with the reference
box, the paragraph box, and the overlap between them, so the failure mode is visible.
"""

from __future__ import annotations

import json
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import (
    BODY_TYPES,
    _overlap_fraction,
    match_blocks_to_paragraphs,
    region_accuracy,
)
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
        region = region_accuracy(report.matches)
        print(f"=== {name}: region={region['accuracy']:.4f} "
              f"({region['correct']}/{region['considered']}) ===")
        failures = []
        for match in report.matches:
            if match.paragraph_index is None or match.bbox is None:
                continue
            overlap = _overlap_fraction(match.bbox, match.block.bbox)
            if overlap < 0.5:
                failures.append((overlap, match))
        failures.sort(key=lambda pair: pair[0])
        for overlap, match in failures[:6]:
            b = match.block
            print(f"  overlap={overlap:.3f}  {b.block_type:9} p{b.page_index} "
                  f"{b.text[:40]!r}")
            print(f"      ref  box=({b.bbox[0]:.3f},{b.bbox[1]:.3f},"
                  f"{b.bbox[2]:.3f},{b.bbox[3]:.3f})")
            print(f"      para box=({match.bbox[0]:.3f},{match.bbox[1]:.3f},"
                  f"{match.bbox[2]:.3f},{match.bbox[3]:.3f}) "
                  f"{match.paragraph_text[:40]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
