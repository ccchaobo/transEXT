"""Test whether recognising at the raster's native resolution removes the loss.

The storage round trip costs about 11% even with no degradation applied, and the
vector page at the same 200 dpi scores 0.0000.  The difference is that the stored
scan was rasterised at 300 dpi and is then *downsampled* to 200 dpi for
recognition.  If that is the cause, recognising at 300 dpi -- the resolution the
raster actually has -- should recover most of the loss.
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

try:
    from scripts.spike_ocr import _normalise, edit_distance, truth_text
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise, edit_distance, truth_text


def reference(document: str, page_index: int) -> str:
    truth = json.loads((CORPUS / "truth" / f"{document}.json").read_text(encoding="utf-8"))
    return _normalise(truth_text(truth, page_index))


def native_dpi(path: Path, page_index: int) -> float:
    """The dpi at which the page's image is stored, from its pixel dimensions."""
    doc = pymupdf.open(str(path))
    page = doc.load_page(page_index)
    rect = page.rect
    infos = page.get_image_info()
    doc.close()
    if not infos:
        return 0.0
    info = max(infos, key=lambda i: i["bbox"][2] - i["bbox"][0])
    image_width = info["width"]
    box_width_points = info["bbox"][2] - info["bbox"][0]
    return image_width * 72.0 / box_width_points if box_width_points else 0.0


def measure(engine: RapidOCR, image: Image.Image, reference_text: str) -> tuple[int, float]:
    result, _elapsed = engine(np.asarray(image))
    if not result:
        return 0, 1.0
    lines = [text for _box, text, _score in result]
    flat = _normalise("\n".join(lines))
    return len(lines), edit_distance(reference_text, flat) / max(1, len(reference_text))


def main() -> int:
    engine = RapidOCR()
    documents = [
        "scan_a_clean_300dpi",
        "scan_b_two_column_noisy",
        "scan_c_skewed",
        "scan_d_low_dpi",
        "scan_e_grayscale",
    ]
    print(f"{'document':24} {'page':>4} {'native':>7} {'dpi':>5} {'lines':>6} {'CER':>8}")
    print("-" * 60)
    summary: dict[str, list[float]] = {}
    for document in documents:
        path = CORPUS / "files" / f"{document}.pdf"
        truth = json.loads((CORPUS / "truth" / f"{document}.json").read_text(encoding="utf-8"))
        for page_index in range(min(2, len(truth["pages"]))):
            reference_text = reference(document, page_index)
            native = native_dpi(path, page_index)
            best: tuple[float, int, float] | None = None
            for dpi in (150, 200, 250, 300, 400):
                doc = pymupdf.open(str(path))
                pixmap = doc.load_page(page_index).get_pixmap(dpi=dpi, alpha=False)
                doc.close()
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                lines, cer = measure(engine, image, reference_text)
                summary.setdefault(document, []).append(cer)
                flag = ""
                if abs(dpi - native) <= 25:
                    flag = "  <- native"
                if best is None or cer < best[1]:
                    best = (dpi, cer, lines)
                print(
                    f"{document:24} {page_index:>4} {native:>7.0f} {dpi:>5} {lines:>6} {cer:>8.4f}{flag}"
                )
            if native:
                print(
                    f"{'':24} {'':>4} {'':>7} {'best':>5} {best[2]:>6} {best[1]:>8.4f}  at {best[0]} dpi"
                )
    print()
    print("best-achievable per document (min CER across dpi):")
    for document, values in summary.items():
        print(f"  {document:24} min={min(values):.4f}  max={max(values):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
