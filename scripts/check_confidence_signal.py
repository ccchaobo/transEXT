"""Can recognition confidence choose the best render resolution?

The error rate varies reproducibly with render resolution, but the variation is
non-monotonic: a page scored 0.0548 at 150 dpi, 0.1394 at 200 dpi and 0.0000 at
400 dpi.  So "render as high as possible" is wrong and a fixed resolution is a
lottery on some pages.

If mean line confidence correlates with accuracy, a multi-resolution policy is
available: recognise at two resolutions and keep the higher-confidence result.
If it does not, that policy would be a guess and is not worth the doubled cost.

This measures the relationship on the corpus, where the true error rate is known.
"""

from __future__ import annotations

import json
import sys
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


def main() -> int:
    engine = RapidOCR()
    rows: list[tuple[str, int, int, float, float, int]] = []

    for path in sorted((CORPUS / "files").glob("scan_*.pdf")):
        document = path.stem
        truth_path = CORPUS / "truth" / f"{document}.json"
        if not truth_path.exists():
            continue
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        for page_index in range(min(2, len(truth["pages"]))):
            reference = _normalise(truth_text(truth, page_index))
            for dpi in (150, 200, 250, 300, 400):
                doc = pymupdf.open(str(path))
                pixmap = doc.load_page(page_index).get_pixmap(dpi=dpi, alpha=False)
                doc.close()
                image = Image.frombytes(
                    "RGB", (pixmap.width, pixmap.height), pixmap.samples
                )
                result, _elapsed = engine(np.asarray(image))
                lines = result or []
                text = "\n".join(t for _b, t, _s in lines)
                cer = multiset_character_error_rate(reference, _normalise(text))
                # A word-count weighted mean, because an unweighted mean lets one
                # short low-confidence line dominate a page of long ones.  The
                # score is coerced to float: the engine documents it as a number
                # but a text path through the JSON-ish output can yield a string,
                # and a silent str would poison the whole sum.
                if lines:
                    weights = [max(1, len(t)) for _b, t, _s in lines]
                    scores = [float(s) for _b, _t, s in lines]
                    confidence = float(
                        sum(s * w for s, w in zip(scores, weights)) / sum(weights)
                    )
                else:
                    confidence = 0.0
                rows.append((document, page_index, dpi, cer, confidence, len(lines)))

    print(f"{'document':24} {'pg':>3} {'dpi':>5} {'CER':>8} {'conf':>7} {'lines':>6}")
    print("-" * 60)
    for document, page_index, dpi, cer, confidence, count in rows:
        print(
            f"{document:24} {page_index:>3} {dpi:>5} {cer:>8.4f} "
            f"{confidence:>7.4f} {count:>6}"
        )

    cers = np.array([r[3] for r in rows])
    confs = np.array([r[4] for r in rows])
    print()
    if len(set(cers)) > 1 and len(set(confs)) > 1:
        correlation = float(np.corrcoef(cers, confs)[0, 1])
        print(f"Pearson correlation between CER and confidence: {correlation:+.4f}")
        print("(a useful selector needs a clear negative correlation)")
    else:
        correlation = float("nan")
        print("correlation undefined: one of the series is constant")

    # How often does the higher-confidence result also have the lower error?
    print()
    print("pairwise check: choosing by confidence against choosing by error")
    correct = 0
    total = 0
    for document in sorted({r[0] for r in rows}):
        for page_index in sorted({r[1] for r in rows if r[0] == document}):
            group = [r for r in rows if r[0] == document and r[1] == page_index]
            for i, first in enumerate(group):
                for second in group[i + 1 :]:
                    total += 1
                    by_conf = first if first[4] > second[4] else second
                    by_error = first if first[3] < second[3] else second
                    if by_conf[2] == by_error[2]:
                        correct += 1
    if total:
        print(
            f"confidence picked the lower-error resolution in {correct}/{total} "
            f"pairs ({correct / total:.1%})"
        )
        print("(50% would mean the signal is worthless)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
