"""Measure the layout pipeline against the corpus ground truth.

Produces the three spec 12.2 figures that belong to the layout module:

* body character coverage (spec item: >= 98%)
* adjacent-block order accuracy (spec item: >= 95%)
* page and region accuracy over at least 200 sampled blocks (spec item: >= 95%)

Recognition is measured elsewhere (``spike_ocr.py``); this script deliberately says
nothing about recognition, because a text PDF's characters are not at issue here —
what is at issue is whether the pipeline keeps them, orders them and places them.

The reference regions come from the corpus ground truth, recorded in raw PDF space
at draw time and converted here with the *same* transform the extractor uses, so a
disagreement is a pipeline failure and not a coordinate disagreement.

Usage::

    python scripts/measure_layout.py
    python scripts/measure_layout.py --json benchmarks/out/layout_metrics.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from document_core.coords import BBox
from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor, page_geometry
from rr_m0.layout_metrics import (
    BODY_TYPES,
    TruthBlock,
    adjacent_order_accuracy,
    body_coverage,
    match_blocks_to_paragraphs,
    region_accuracy,
)

CORPUS = Path("benchmarks/corpus")
TEXT_PDF_KIND = "text_pdf"
MIXED_KIND = "mixed_pdf"


def load_truth_blocks(
    truth: dict, geometry_by_page: dict, *, include_structure_only: bool = True
) -> list[TruthBlock]:
    """Reference blocks with regions in normalised display coordinates.

    Region conversion uses ``PageGeometry.raw_to_norm`` from the page itself, which
    is the frozen coordinate contract (ADR 0008) and the same transform the text
    extractor applies to every box it emits.

    **A block that spans pages is split into one entry per page, holding only the
    lines drawn on that page.**  This matters more than it looks.  A block's ``text``
    is its *semantic* text: for a paragraph that runs across a page break it is the
    whole paragraph, and the generator records that whole paragraph on every page the
    block reaches.  Using it made the reference contain those paragraphs once per page
    while the PDF contains them once in total, which reported 9% of the body text as
    missing when in fact every recorded line was present.  Its ``bbox`` has the same
    problem: it is the union of the block's boxes across pages, which for a cross-page
    paragraph is most of the page and therefore matches no paragraph's region.

    Splitting by page and using the per-line text and boxes fixes both, and it is what
    a per-page measurement actually means.
    """
    blocks: list[TruthBlock] = []
    for page in truth["pages"]:
        page_index = page["index"]
        geometry = geometry_by_page.get(page_index)
        if geometry is None:
            continue
        transform = geometry.raw_to_norm()
        for block in page["blocks"]:
            block_type = block.get("type") or "paragraph"
            line_texts = list(block.get("line_texts") or [])
            line_boxes = list(block.get("line_bboxes") or [])
            line_pages = list(block.get("line_page_indices") or [])

            on_this_page = [
                i
                for i, _text in enumerate(line_texts)
                if not line_pages or line_pages[i] == page_index
            ]
            if on_this_page:
                text = " ".join(line_texts[i] for i in on_this_page)
                boxes = [line_boxes[i] for i in on_this_page if i < len(line_boxes)]
            else:
                # A block with no line record (a figure, or an unfilled table): fall
                # back to its own text and box, which for these is page-local.
                text = block.get("text") or ""
                boxes = [block.get("bbox") or [0.0, 0.0, 0.0, 0.0]]

            if not text.strip():
                if not include_structure_only:
                    continue

            x0s: list[float] = []
            y0s: list[float] = []
            x1s: list[float] = []
            y1s: list[float] = []
            for raw in boxes:
                if not raw or len(raw) < 4:
                    continue
                corner_a = transform.apply(raw[0], raw[1])
                corner_b = transform.apply(raw[2], raw[3])
                x0s.append(min(corner_a[0], corner_b[0]))
                x1s.append(max(corner_a[0], corner_b[0]))
                y0s.append(min(corner_a[1], corner_b[1]))
                y1s.append(max(corner_a[1], corner_b[1]))
            if not x0s:
                # Nothing drawable on this page; a structure-only block keeps its
                # recorded page so it can still be counted for region accuracy.
                bbox = (0.0, 0.0, 0.0, 0.0)
            else:
                bbox = (min(x0s), min(y0s), max(x1s), max(y1s))

            blocks.append(
                TruthBlock(
                    index=int(block.get("index", len(blocks))),
                    block_type=block_type,
                    text=text,
                    page_index=page_index,
                    reading_order=int(block.get("reading_order", len(blocks))),
                    bbox=bbox,
                    n_lines=len(on_this_page) or int(block.get("n_lines", 0)),
                )
            )
    return blocks


def measure_document(extractor: PdfTextExtractor, name: str) -> dict:
    pdf = CORPUS / "files" / f"{name}.pdf"
    truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))

    extraction = extractor.extract(pdf)
    result = analyse(extraction.lines_by_page)

    blocks = load_truth_blocks(truth, extraction.geometry_by_page)

    paragraphs = [
        (para.text, _para_page(para), tuple(para.bbox.as_list()))
        for para in result.paragraphs
    ]

    # --- which pages can a text-layer measurement judge? -------------------
    # A scanned page has no text layer, so the extractor returns nothing for it and
    # OCR is required.  Averaging such a page into a text-layer coverage figure
    # reported 0.39 for `mixed_f_hybrid`, whose first page is an image, while every
    # text page in the corpus scores 0.997 or better.  Those pages are named and
    # excluded instead of being allowed to look like pipeline failures.
    scanned_pages = {
        info.index for info in extraction.page_infos if info.likely_scanned
    }
    text_pages = [
        page["index"]
        for page in truth["pages"]
        if page["index"] not in scanned_pages
    ]

    # --- coverage ---------------------------------------------------------
    # Compared as pooled text per page, not per block, so that the pipeline's own
    # choice of where one paragraph ends and the next begins cannot affect the
    # figure.  That is the whole point of a coverage measure; ordering is judged
    # separately.
    #
    # The per-page *drawn* text is used rather than each block's semantic text.  A
    # block's `text` is the whole paragraph for a paragraph that crosses a page
    # break, and the generator records it on every page the block reaches, so using
    # it counted those paragraphs once per page while the PDF holds them once.  That
    # alone reported 9% of the body text missing when in fact no recorded line was
    # absent.
    body_reference_parts: list[str] = []
    all_reference_parts: list[str] = []
    hypothesis_parts: list[str] = []
    per_page: list[dict] = []
    for page in truth["pages"]:
        page_index = page["index"]
        drawn = page.get("visible_text", "")
        page_blocks = [b for b in blocks if b.page_index == page_index]
        body_ref = " ".join(
            b.text for b in page_blocks if b.block_type in BODY_TYPES and b.has_text
        )
        all_ref = " ".join(b.text for b in page_blocks if b.has_text)
        produced = " ".join(
            para.text for para in result.paragraphs if _para_page(para) == page_index
        )
        comparable = page_index in text_pages and bool(body_ref.strip())
        page_coverage = None
        if comparable:
            body_reference_parts.append(body_ref)
            all_reference_parts.append(all_ref)
            hypothesis_parts.append(produced)
            page_coverage, _missing, _length = body_coverage(body_ref, produced)
        per_page.append(
            {
                "page": page_index,
                "comparable": comparable,
                "scanned": page_index in scanned_pages,
                "recorded_chars": len(" ".join(drawn.split())),
                "produced_chars": len(" ".join(produced.split())),
                "coverage": None if page_coverage is None else round(page_coverage, 4),
            }
        )

    body_reference = " ".join(body_reference_parts)
    all_reference = " ".join(all_reference_parts)
    body_hypothesis = " ".join(hypothesis_parts)
    body_only, body_missing, body_length = body_coverage(body_reference, body_hypothesis)
    all_only, _all_missing, _all_length = body_coverage(all_reference, body_hypothesis)

    # --- order and region -------------------------------------------------
    # Restricted to pages a text-layer measurement can judge.  A scanned page has no
    # extracted lines, so every one of its blocks would match nothing and be counted
    # as an ordering and placement failure by the layout module, which never had a
    # chance to see the page.
    judged_blocks = [
        b
        for b in blocks
        if b.block_type in BODY_TYPES
        and b.page_index in text_pages
    ]
    judged_paragraphs = [p for p in paragraphs if p[1] in text_pages]
    report = match_blocks_to_paragraphs(judged_blocks, judged_paragraphs)
    order_accuracy, order_correct, order_total = adjacent_order_accuracy(
        [m.block for m in report.matches], report.matches
    )
    region = region_accuracy(report.matches)

    return {
        "document": name,
        "layout": truth.get("layout"),
        "pages": len(truth["pages"]),
        "text_pages": text_pages,
        "scanned_pages": sorted(scanned_pages),
        "per_page": per_page,
        "paragraphs": len(result.paragraphs),
        "furniture": len(result.furniture),
        "warnings": result.warnings,
        "page_columns": {str(k): v for k, v in result.page_columns.items()},
        "blocks_total": sum(1 for b in blocks if b.page_index in text_pages),
        "blocks_body": sum(
            1
            for b in judged_blocks
            if b.has_text
        ),
        "coverage_body": round(body_only, 4),
        "coverage_body_missing_chars": body_missing,
        "coverage_body_reference_chars": body_length,
        "coverage_all": round(all_only, 4),
        "matched_blocks": len(report.matches),
        "unmatched_blocks": len(report.unmatched_blocks),
        "order_accuracy": None if order_accuracy is None else round(order_accuracy, 4),
        "order_correct": order_correct,
        "order_total": order_total,
        "region": region,
        "unmatched_detail": [
            {"index": b.index, "type": b.block_type, "text": b.text[:60]}
            for b in report.unmatched_blocks[:8]
        ],
    }


def _para_page(para) -> int:
    """The page a paragraph is attributed to; the first, if it spans pages."""
    pages = para.page_indices or [0]
    return min(pages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure layout against truth")
    parser.add_argument("--json", default=None, help="write the full report here")
    parser.add_argument("--only", default=None, help="measure one document")
    args = parser.parse_args(argv)

    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        entry["name"]
        for entry in manifest["entries"]
        if entry.get("kind") in (TEXT_PDF_KIND, MIXED_KIND)
    ]
    if args.only:
        names = [n for n in names if n == args.only]
    names.sort()

    extractor = PdfTextExtractor()
    results: list[dict] = []
    for name in names:
        result = measure_document(extractor, name)
        results.append(result)
        print(
            f"\n=== {result['document']} ({result['layout']}, {result['pages']} pages) ==="
        )
        print(
            f"  blocks={result['blocks_total']} body={result['blocks_body']} "
            f"paragraphs={result['paragraphs']} furniture={result['furniture']}"
        )
        print(f"  page_columns detected: {result['page_columns']}")
        if result["scanned_pages"]:
            print(
                f"  pages judged: {result['text_pages']}   "
                f"NOT JUDGED (scanned, OCR required): {result['scanned_pages']}"
            )
        else:
            print(f"  pages judged: {result['text_pages']} (all pages have a text layer)")
        print(
            f"  coverage body={result['coverage_body']:.4f} "
            f"(missing {result['coverage_body_missing_chars']} of "
            f"{result['coverage_body_reference_chars']} chars)  "
            f"all-types={result['coverage_all']:.4f}"
        )
        for page in result["per_page"]:
            if page["comparable"]:
                continue
            reason = "scanned page, no text layer" if page["scanned"] else "no reference text"
            print(
                f"    page {page['page']}: not judged ({reason}); "
                f"reference has {page['recorded_chars']} chars"
            )
        print(
            f"  matched={result['matched_blocks']} "
            f"unmatched={result['unmatched_blocks']}"
        )
        if result["order_accuracy"] is None:
            print("  order: NOT MEASURED (no adjacent pairs could be judged)")
        else:
            print(
                f"  order={result['order_accuracy']:.4f} "
                f"({result['order_correct']}/{result['order_total']} adjacent pairs)"
            )
        region = result["region"]
        if region["accuracy"] is None:
            print("  region: NOT MEASURED")
        else:
            print(
                f"  region={region['accuracy']:.4f} "
                f"({region['correct']}/{region['considered']} blocks) "
                f"wrong_page={len(region['wrong_page'])} "
                f"wrong_region={len(region['wrong_region'])}"
            )
        if result["unmatched_detail"]:
            print("  unmatched sample:")
            for item in result["unmatched_detail"][:4]:
                print(f"    #{item['index']} {item['type']:10} {item['text']!r}")
        for warning in result["warnings"][:3]:
            print(f"  WARN {warning}")

    # --- aggregate --------------------------------------------------------
    print("\n" + "=" * 74)
    print("AGGREGATE (per-document figures are above; nothing is averaged away)")
    total_ref = sum(r["coverage_body_reference_chars"] for r in results)
    total_missing = sum(r["coverage_body_missing_chars"] for r in results)
    pooled_coverage = 1.0 - (total_missing / total_ref) if total_ref else None
    order_correct = sum(r["order_correct"] for r in results)
    order_total = sum(r["order_total"] for r in results)
    pooled_order = (order_correct / order_total) if order_total else None
    region_correct = sum(r["region"]["correct"] for r in results)
    region_considered = sum(r["region"]["considered"] for r in results)
    pooled_region = (region_correct / region_considered) if region_considered else None

    print(f"  coverage : {pooled_coverage:.4f}  "
          f"({total_missing} missing of {total_ref} chars)   target >= 0.98")
    print(f"  order    : {pooled_order:.4f}  "
          f"({order_correct}/{order_total} pairs)             target >= 0.95")
    print(f"  region   : {pooled_region:.4f}  "
          f"({region_correct}/{region_considered} blocks)          target >= 0.95")
    print(f"  sampled blocks: {region_considered}   spec requires >= 200")

    verdicts = {
        "coverage": pooled_coverage,
        "order": pooled_order,
        "region": pooled_region,
        "sampled_blocks": region_considered,
    }

    print("\nSPEC 12.2 LAYOUT ITEMS")
    def verdict(value, target, *, higher_is_better=True):
        if value is None:
            return "NOT MEASURED"
        ok = value >= target if higher_is_better else value <= target
        return "PASS" if ok else "FAIL"

    print(f"  body character coverage >= 98%      : "
          f"{verdict(pooled_coverage, 0.98)}  ({pooled_coverage:.4f})"
          if pooled_coverage is not None else "  body character coverage: NOT MEASURED")
    print(f"  adjacent-block order >= 95%         : "
          f"{verdict(pooled_order, 0.95)}  ({pooled_order:.4f})"
          if pooled_order is not None else "  adjacent-block order: NOT MEASURED")
    print(f"  page+region accuracy >= 95%         : "
          f"{verdict(pooled_region, 0.95)}  ({pooled_region:.4f})"
          if pooled_region is not None else "  page+region accuracy: NOT MEASURED")
    print(f"  >= 200 sampled blocks               : "
          f"{'PASS' if region_considered >= 200 else 'FAIL'}  ({region_considered})")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"documents": results, "aggregate": verdicts},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
