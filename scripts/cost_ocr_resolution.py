"""OCR resolution: what accuracy does each choice actually buy, and at what cost?

ADR 0004 chose 300 dpi on accuracy alone, measured over ten pages.  The M0
performance harness then showed that 300 dpi costs roughly 10 s per page, against
about 8 s at 200 dpi, with a mean recognition error of 0.022 versus 0.031.

A 0.009 accuracy gain for a 25% cost increase is only worth taking if the errors
it removes are the kind that matter, and the decision should be made with both
figures in view rather than one at a time.  This measures accuracy *and* wall time
at each resolution on the same pages, and reports what each choice costs per page
and per document.

Timing is reported alongside accuracy because a policy that only optimises accuracy
can make the product unusable, and a policy that only optimises speed can make it
wrong.  Neither number decides alone.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

CORPUS = Path("benchmarks/corpus")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from benchmarks.rr_m0.ocr_metrics import multiset_character_error_rate  # noqa: E402

try:
    from scripts.spike_ocr import _normalise, truth_text
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise, truth_text

RESOLUTIONS = (150, 200, 250, 300, 400)


def main() -> int:
    engine = RapidOCR()
    rows: list[dict[str, float | str | int]] = []

    for path in sorted((CORPUS / "files").glob("scan_*.pdf")):
        document = path.stem
        truth_path = CORPUS / "truth" / f"{document}.json"
        if not truth_path.exists():
            continue
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        for page_index in range(min(2, len(truth["pages"]))):
            reference = _normalise(truth_text(truth, page_index))
            for dpi in RESOLUTIONS:
                doc = pymupdf.open(str(path))
                pixmap = doc.load_page(page_index).get_pixmap(dpi=dpi, alpha=False)
                doc.close()
                image = Image.frombytes(
                    "RGB", (pixmap.width, pixmap.height), pixmap.samples
                )
                array = np.asarray(image)

                # Warm-up is excluded from the timing: the first call on an image
                # size pays a one-off allocation cost that would otherwise be
                # charged to whichever resolution happened to run first.
                engine(array)
                started = time.perf_counter()
                result, _elapsed = engine(array)
                seconds = time.perf_counter() - started

                text = "\n".join(t for _b, t, _s in (result or []))
                rows.append(
                    {
                        "document": document,
                        "page": page_index,
                        "dpi": dpi,
                        "cer": multiset_character_error_rate(
                            reference, _normalise(text)
                        ),
                        "seconds": seconds,
                        "pixels": int(pixmap.width) * int(pixmap.height),
                        "lines": len(result or []),
                    }
                )

    print(f"{'dpi':>5} {'mean CER':>9} {'median s':>9} {'mean s':>8} {'CER/page-s':>11}")
    print("-" * 48)
    for dpi in RESOLUTIONS:
        group = [r for r in rows if r["dpi"] == dpi]
        mean_cer = statistics.fmean(r["cer"] for r in group)
        median_s = statistics.median(r["seconds"] for r in group)
        mean_s = statistics.fmean(r["seconds"] for r in group)
        # Accuracy bought per second spent.  Higher is better; this is the number
        # that makes the tradeoff comparable across resolutions.
        efficiency = (1.0 - mean_cer) / mean_s
        print(
            f"{dpi:>5} {mean_cer:>9.4f} {median_s:>9.2f} {mean_s:>8.2f} "
            f"{efficiency:>11.4f}"
        )

    print()
    print("per-document accuracy and cost (2 pages each):")
    print(f"  {'document':24} " + " ".join(f"{d:>15}" for d in RESOLUTIONS))
    for document in sorted({str(r['document']) for r in rows}):
        cells = []
        for dpi in RESOLUTIONS:
            group = [
                r for r in rows if r["document"] == document and r["dpi"] == dpi
            ]
            cer = statistics.fmean(r["cer"] for r in group)
            seconds = statistics.fmean(r["seconds"] for r in group)
            cells.append(f"{cer:.3f}/{seconds:4.1f}s")
        print(f"  {document:24} " + " ".join(f"{c:>15}" for c in cells))

    print()
    # The decision this evidence has to support, stated in its own terms.
    baseline = [r for r in rows if r["dpi"] == 200]
    candidate = [r for r in rows if r["dpi"] == 300]
    base_cer = statistics.fmean(r["cer"] for r in baseline)
    cand_cer = statistics.fmean(r["cer"] for r in candidate)
    base_s = statistics.fmean(r["seconds"] for r in baseline)
    cand_s = statistics.fmean(r["seconds"] for r in candidate)
    if cand_cer < base_cer:
        gain = base_cer - cand_cer
        cost = cand_s - base_s
        print(
            f"300 dpi removes {gain:.4f} of error for {cost:+.2f} s per page "
            f"({cost / base_s:+.0%} time)."
        )
        print(
            "A 20-page scanned document therefore costs about "
            f"{(cand_s - base_s) * 20:.0f} s extra to reduce error by {gain:.1%}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
