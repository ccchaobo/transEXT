"""Test determinism of the OCR engine and of the measurement.

Two very different character error rates were measured for the same page and the
same nominal conditions (0.0005 and 0.0410), which means either the engine is
non-deterministic or the harness compares different things between runs.

This runs the identical image repeatedly and reports the spread, then checks the
harness itself by recomputing the reference text two ways.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

CORPUS = Path("benchmarks/corpus")

try:
    from scripts.spike_ocr import _normalise, edit_distance, truth_text
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise, edit_distance, truth_text


def main() -> int:
    document = "pdf_a_single_column"
    page_index = 0
    truth = json.loads((CORPUS / "truth" / f"{document}.json").read_text(encoding="utf-8"))

    # --- 1. the harness: are the two reference paths the same text? --------
    from_truth_text = truth_text(truth, page_index)
    page = next(p for p in truth["pages"] if p["index"] == page_index)
    from_join = "\n".join(b["text"] for b in page["blocks"])
    print("reference construction check:")
    print(f"  len(truth_text)          = {len(from_truth_text)}")
    print(f"  len('\\n'.join(blocks))    = {len(from_join)}")
    print(f"  identical                = {from_truth_text == from_join}")
    print(f"  normalised length        = {len(_normalise(from_truth_text))}")
    print(f"  blocks on this page      = {len(page['blocks'])}")
    print(f"  page keys                = {sorted(page.keys())}")
    print()

    # --- 2. the engine: same image, repeated runs --------------------------
    doc = pymupdf.open(str(CORPUS / "files" / f"{document}.pdf"))
    pixmap = doc.load_page(page_index).get_pixmap(dpi=200, alpha=False)
    doc.close()
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    array = np.asarray(image)
    print(f"image: {pixmap.width}x{pixmap.height}, sha256 of pixels = "
          f"{hashlib.sha256(array.tobytes()).hexdigest()[:16]}")
    print()

    reference = _normalise(from_truth_text)
    engine = RapidOCR()
    print(f"{'run':>3} {'lines':>6} {'chars':>6} {'CER':>8} {'conf':>6} {'text hash':>18}")
    print("-" * 58)
    results = []
    for run in range(5):
        result, _elapsed = engine(array)
        lines = [text for _box, text, _score in result]
        flat = _normalise("\n".join(lines))
        distance = edit_distance(reference, flat)
        cer = distance / len(reference)
        confidence = sum(float(s) for _b, _t, s in result) / len(result)
        digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]
        results.append((len(lines), len(flat), cer, digest))
        print(f"{run:>3} {len(lines):>6} {len(flat):>6} {cer:>8.4f} {confidence:>6.3f} {digest:>18}")

    distinct = {r[3] for r in results}
    print()
    print(f"distinct outputs across 5 runs: {len(distinct)}")
    if len(distinct) > 1:
        print("ENGINE IS NON-DETERMINISTIC: the same input yields different text.")
    else:
        print("engine is deterministic for this input.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
