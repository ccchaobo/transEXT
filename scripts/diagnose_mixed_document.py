"""Why is coverage 0.39 on mixed_f_hybrid?

Every other document is at 0.998 or better, so this one file is dragging the
aggregate down.  A mixed document deliberately contains scanned pages, and a scanned
page has no text layer, so the extractor legitimately returns nothing for it.  If
that is the cause, the figure is not a pipeline failure and the metric must say which
pages it could not compare instead of averaging them into one number.
"""

from __future__ import annotations

import json
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor, page_geometry
from rr_m0.layout_metrics import BODY_TYPES, body_coverage

CORPUS = Path("benchmarks/corpus")
NAME = "mixed_f_hybrid"


def main() -> int:
    truth = json.loads((CORPUS / "truth" / f"{NAME}.json").read_text(encoding="utf-8"))
    extraction = PdfTextExtractor().extract(CORPUS / "files" / f"{NAME}.pdf")

    print(f"=== {NAME} ===")
    print(f"  layout={truth['layout']} pages={truth['page_count']}")

    print("\n  --- page infos from the extractor ---")
    for info in extraction.page_infos:
        print(
            f"    page {info.index}: text_chars={info.text_chars:5} "
            f"likely_scanned={info.likely_scanned} image_count={info.image_count} "
            f"image_coverage={info.image_coverage:.2f}"
        )
        for reason in info.reasons:
            print(f"        reason: {reason}")
    for warning in extraction.warnings:
        print(f"    WARNING {warning}")

    result = analyse(extraction.lines_by_page)
    para_by_page: dict[int, list[str]] = {}
    for para in result.paragraphs:
        para_by_page.setdefault(min(para.page_indices or [0]), []).append(para.text)

    print("\n  --- per-page coverage, with the scan status alongside ---")
    for page in truth["pages"]:
        page_index = page["index"]
        ref = " ".join(
            b["text"] for b in page["blocks"]
            if (b.get("type") or "paragraph") in BODY_TYPES and b["text"].strip()
        )
        extracted = " ".join(
            ln.text for ln in extraction.lines_by_page.get(page_index, [])
        )
        produced = " ".join(para_by_page.get(page_index, []))
        info = next(
            (i for i in extraction.page_infos if i.index == page_index), None
        )
        cov_extract, miss_e, length = body_coverage(ref, extracted)
        cov_pipe, miss_p, _ = body_coverage(ref, produced)
        print(
            f"    page {page_index}: source_kind={page.get('source_kind')!r} "
            f"truth_chars={length:5} "
            f"extractor_lines={len(extraction.lines_by_page.get(page_index, [])):3} "
            f"scanned={getattr(info, 'likely_scanned', None)} "
            f"coverage extract={cov_extract:.4f} pipeline={cov_pipe:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
