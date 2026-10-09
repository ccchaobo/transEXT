"""Test whether OCR misses lines because of resolution or page degradation.

RapidOCR detects far fewer lines than the page contains.  This isolates the
cause by running the same page through the engine from several sources:

* the *vector* PDF rendered at increasing dpi (no scan degradation),
* the scanned PDF rendered at increasing dpi (degradation included).

If the vector path is also incomplete, the fault is detection or resolution.  If
only the scanned path is incomplete, the fault is the degradation profile used to
build the corpus.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

CORPUS = Path("benchmarks/corpus")

try:
    from scripts.spike_ocr import _normalise, edit_distance
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise, edit_distance


def reference(name: str, page_index: int) -> str:
    truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
    page = next(p for p in truth["pages"] if p["index"] == page_index)
    return "\n".join(b["text"] for b in page["blocks"])


def run(engine: RapidOCR, image: Image.Image) -> tuple[list[str], float, float]:
    started = time.perf_counter()
    result, _elapsed = engine(np.asarray(image))
    seconds = time.perf_counter() - started
    if not result:
        return [], seconds, 0.0
    lines = [text for _box, text, _score in result]
    mean_conf = sum(float(score) for _box, _text, score in result) / len(result)
    return lines, seconds, mean_conf


def score(reference_text: str, lines: list[str]) -> tuple[int, float]:
    flat_ref = _normalise(reference_text)
    flat_ocr = _normalise("\n".join(lines))
    distance = edit_distance(flat_ref, flat_ocr)
    return distance, distance / max(1, len(flat_ref))


def main() -> int:
    page_index = 0
    truth_text = reference("scan_a_clean_300dpi", page_index)
    ref_chars = len(_normalise(truth_text))
    print(f"reference normalised characters: {ref_chars}")
    print(f"{'source':22} {'dpi':>5} {'lines':>6} {'chars':>6} {'ratio':>6} {'CER':>7} {'conf':>6} {'sec':>6}")
    print("-" * 78)

    engine = RapidOCR()

    for label, path in (
        ("vector pdf_a", CORPUS / "files" / "pdf_a_single_column.pdf"),
        ("scan clean 300dpi", CORPUS / "files" / "scan_a_clean_300dpi.pdf"),
    ):
        for dpi in (150, 200, 300, 400):
            doc = pymupdf.open(str(path))
            pixmap = doc.load_page(page_index).get_pixmap(dpi=dpi, alpha=False)
            doc.close()
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            lines, seconds, conf = run(engine, image)
            _distance, cer = score(truth_text, lines)
            ocr_chars = len(_normalise("\n".join(lines)))
            print(
                f"{label:22} {dpi:>5} {len(lines):>6} {ocr_chars:>6} "
                f"{ocr_chars / ref_chars:>6.2f} {cer:>7.4f} {conf:>6.3f} {seconds:>6.1f}"
            )

    # Grayscale and upscaling effects on the 300 dpi scan.
    print()
    doc = pymupdf.open(str(CORPUS / "files" / "scan_a_clean_300dpi.pdf"))
    pixmap = doc.load_page(page_index).get_pixmap(dpi=300, alpha=False)
    doc.close()
    base = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    variants = {
        "300dpi as-is": base,
        "300dpi grayscale": base.convert("L").convert("RGB"),
        "300dpi 2x upscaled": base.resize((base.width * 2, base.height * 2), Image.LANCZOS),
        "300dpi autocontrast": _autocontrast(base),
    }
    for label, image in variants.items():
        lines, seconds, conf = run(engine, image)
        _distance, cer = score(truth_text, lines)
        ocr_chars = len(_normalise("\n".join(lines)))
        print(
            f"{label:22} {'-':>5} {len(lines):>6} {ocr_chars:>6} "
            f"{ocr_chars / ref_chars:>6.2f} {cer:>7.4f} {conf:>6.3f} {seconds:>6.1f}"
        )
    return 0


def _autocontrast(image: Image.Image) -> Image.Image:
    from PIL import ImageOps

    return ImageOps.autocontrast(image.convert("L"), cutoff=1).convert("RGB")


if __name__ == "__main__":
    raise SystemExit(main())
