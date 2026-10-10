"""Sweep the two paragraph-break parameters and report every metric for each.

The calibration gives a valley, not a point: on this corpus the within-paragraph gap is a
constant 2.4 pt and the 5th percentile of the between-paragraph gap is 4.16 pt, so any
threshold between them separates the populations.  Picking the middle of the valley by eye
would be tuning to one corpus by hand, so instead the parameters are swept and scored on
four independent measures.  A value is only defensible if the scores are flat around it,
which is what "inside the valley" should look like.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from document_core.layout import LayoutParams, analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import match_blocks_to_paragraphs
from measure_layout import load_truth_blocks

CORPUS = Path("benchmarks/corpus")
STRUCTURE_ONLY = {"table", "table_cell", "figure"}

GRID = [
    # (line_overlap_tolerance, paragraph_gap_factor) as multiples of the median line height
    (0.05, 0.10),
    (0.10, 0.20),
    (0.15, 0.25),
    (0.25, 0.35),
    (0.40, 0.50),
    (0.50, 1.00),
]


def normalise(text: str) -> str:
    return " ".join((text or "").split())


def load(names):
    extractor = PdfTextExtractor()
    documents = []
    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        blocks = [
            block
            for block in load_truth_blocks(truth, extraction.geometry_by_page)
            if normalise(block.text) and block.block_type not in STRUCTURE_ONLY
        ]
        documents.append((name, extraction, blocks))
    return documents


def score(documents, params) -> dict:
    addressable = merged = total = 0
    paragraphs_total = 0
    for name, extraction, blocks in documents:
        layout = analyse(extraction.lines_by_page, params)
        paragraphs_total += len(layout.paragraphs)
        paragraphs = [
            (p.text, tuple(sorted(p.page_indices or [0])), tuple(p.bbox.as_list()))
            for p in layout.paragraphs
        ]
        report = match_blocks_to_paragraphs(blocks, paragraphs)
        for match in report.matches:
            total += 1
            if match.paragraph_index is None:
                continue
            paragraph_text = normalise(paragraphs[match.paragraph_index][0])
            block_text = normalise(match.block.text)
            if paragraph_text == block_text:
                addressable += 1
            elif block_text in paragraph_text:
                merged += 1
    return {
        "addressable": addressable,
        "merged": merged,
        "total": total,
        "paragraphs": paragraphs_total,
        "rate": addressable / total if total else 0.0,
    }


def main() -> int:
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        entry["name"]
        for entry in sorted(manifest["entries"], key=lambda e: e["name"])
        if entry["kind"] == "text_pdf"
    ]
    documents = load(names)

    print(f"{'tolerance':>9} {'gap factor':>10} {'1:1':>6} {'merged':>7} {'total':>6} "
          f"{'rate':>7} {'paras':>6}")
    results = []
    for tolerance, factor in GRID:
        params = LayoutParams(
            line_overlap_tolerance=tolerance, paragraph_gap_factor=factor
        )
        result = score(documents, params)
        results.append((tolerance, factor, result))
        print(f"{tolerance:9.2f} {factor:10.2f} {result['addressable']:6} "
              f"{result['merged']:7} {result['total']:6} {result['rate']:7.4f} "
              f"{result['paragraphs']:6}")

    best = max(results, key=lambda item: item[2]["rate"])
    print()
    print(f"best rate {best[2]['rate']:.4f} at tolerance={best[0]} gap factor={best[1]}")

    # Which grid entries are within one block of that result: a value chosen from a plateau
    # is far more defensible than one chosen at a peak.
    plateau = [
        (t, f, r["rate"])
        for t, f, r in results
        if best[2]["rate"] - r["rate"] <= 0.05
    ]
    print()
    print("PLATEAU (within 0.05 of the best):")
    for tolerance, factor, rate in plateau:
        print(f"  tolerance={tolerance:.2f} gap factor={factor:.2f}  rate={rate:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
