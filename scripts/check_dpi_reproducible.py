"""Is the error spread across render resolutions reproducible?

The calibration run showed the same page scoring 0.0548 at 150 dpi, 0.1394 at 200
dpi and 0.0000 at 400 dpi.  If those differences are run-to-run variation then
render resolution is not a usable tuning knob and any policy based on it would be
fitting noise.  If they reproduce, the detector's internal resizing genuinely
interacts with the input scale and the resolution has to be chosen deliberately.

Each resolution is recognised several times and the spread across repeats is
reported next to the spread across resolutions.  Reproducibility is the thing
being tested, so a single run cannot answer it.
"""

from __future__ import annotations

import json
import statistics
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

DOCUMENT = "scan_a_clean_300dpi"
PAGE_INDEX = 0
RESOLUTIONS = (150, 200, 250, 300, 400)
REPEATS = 3


def main() -> int:
    truth = json.loads((CORPUS / "truth" / f"{DOCUMENT}.json").read_text(encoding="utf-8"))
    reference = _normalise(truth_text(truth, PAGE_INDEX))

    engine = RapidOCR()
    print(f"{DOCUMENT} page {PAGE_INDEX}, {REPEATS} repeats per resolution")
    print(f"{'dpi':>5} {'render':>12} {'runs':>34} {'median':>8} {'spread':>8}")
    print("-" * 74)

    by_resolution: dict[int, list[float]] = {}
    for dpi in RESOLUTIONS:
        doc = pymupdf.open(str(CORPUS / "files" / f"{DOCUMENT}.pdf"))
        pixmap = doc.load_page(PAGE_INDEX).get_pixmap(dpi=dpi, alpha=False)
        doc.close()
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        array = np.asarray(image)

        scores: list[float] = []
        for _ in range(REPEATS):
            result, _elapsed = engine(array)
            text = "\n".join(t for _b, t, _s in (result or []))
            scores.append(multiset_character_error_rate(reference, _normalise(text)))
        by_resolution[dpi] = scores
        median = statistics.median(scores)
        spread = max(scores) - min(scores)
        runs = " ".join(f"{s:.4f}" for s in scores)
        print(
            f"{dpi:>5} {f'{pixmap.width}x{pixmap.height}':>12} {runs:>34} "
            f"{median:>8.4f} {spread:>8.4f}"
        )

    within = max(max(v) - min(v) for v in by_resolution.values())
    medians = [statistics.median(v) for v in by_resolution.values()]
    across = max(medians) - min(medians)
    print()
    print(f"largest spread within one resolution : {within:.4f}")
    print(f"spread across resolutions (medians)  : {across:.4f}")
    print()
    if across > 4 * max(within, 1e-9):
        print("VERDICT: the differences across resolutions are reproducible; render")
        print("         resolution is a real factor and must be chosen deliberately.")
    else:
        print("VERDICT: the differences are within run-to-run variation; render")
        print("         resolution is NOT a usable tuning knob on this evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
