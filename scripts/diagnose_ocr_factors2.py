"""Measure each scan-simulation factor against the corrected ground truth.

An earlier sweep used a defective ground truth and its conclusions were
therefore unreliable: every factor appeared to cost the same, because the metric
was dominated by a constant ground-truth error.  This repeats the sweep against
the corrected per-page truth, so the numbers actually describe image quality.

The production pipeline renders a stored scan at 200 dpi before recognition, so
each factor is measured after that same downsampling step rather than on a
full-resolution render.
"""

from __future__ import annotations

import io
import json
import random
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter
from rapidocr_onnxruntime import RapidOCR

CORPUS = Path("benchmarks/corpus")

try:
    from scripts.spike_ocr import _normalise, edit_distance, truth_text
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise, edit_distance, truth_text

DOCUMENT = "pdf_a_single_column"
PAGE_INDEX = 0
TRUTH = json.loads((CORPUS / "truth" / f"{DOCUMENT}.json").read_text(encoding="utf-8"))
REFERENCE = _normalise(truth_text(TRUTH, PAGE_INDEX))

#: The dpi the pipeline actually recognises at.
OCR_DPI = 200
#: dpi the corpus generator rasterises at.
SCAN_DPI = 300


def vector_page(dpi: int) -> Image.Image:
    doc = pymupdf.open(str(CORPUS / "files" / f"{DOCUMENT}.pdf"))
    pixmap = doc.load_page(PAGE_INDEX).get_pixmap(dpi=dpi, alpha=False)
    doc.close()
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def jpeg_roundtrip(image: Image.Image, quality: int) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def add_noise(image: Image.Image, sigma: float, speckles: int, seed: int = 7) -> Image.Image:
    """Vectorised noise; equivalent to the generator's loop but not O(pixels) in Python."""
    array = np.asarray(image).astype("float32")
    if sigma > 0:
        generator = np.random.default_rng(seed)
        array = array + generator.normal(0.0, sigma, array.shape)
    array = np.clip(array, 0, 255).astype("uint8")
    out = Image.fromarray(array, "RGB")
    if speckles:
        rng = random.Random(seed)
        draw = ImageDraw.Draw(out)
        width, height = out.size
        for _ in range(speckles):
            x, y = rng.randrange(width), rng.randrange(height)
            value = rng.randrange(30, 120)
            draw.point((x, y), fill=(value, value, value))
    return out


def embed_and_render(image: Image.Image, jpeg_quality: int, dpi: int) -> Image.Image:
    """Simulate what the pipeline sees: the image is stored, then re-rendered."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=jpeg_quality)
    doc = pymupdf.open()
    rect = pymupdf.Rect(0, 0, image.width * 72.0 / SCAN_DPI, image.height * 72.0 / SCAN_DPI)
    page = doc.new_page(width=rect.width, height=rect.height)
    page.insert_image(rect, stream=buffer.getvalue())
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    doc.close()
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def measure(engine: RapidOCR, image: Image.Image) -> tuple[int, float, float]:
    result, _elapsed = engine(np.asarray(image))
    if not result:
        return 0, 1.0, 0.0
    lines = [text for _box, text, _score in result]
    flat = _normalise("\n".join(lines))
    distance = edit_distance(REFERENCE, flat)
    confidence = sum(float(s) for _b, _t, s in result) / len(result)
    return len(lines), distance / max(1, len(REFERENCE)), confidence


def main() -> int:
    engine = RapidOCR()
    print(f"reference (corrected) characters: {len(REFERENCE)}")
    print(f"recognition dpi: {OCR_DPI}, corpus raster dpi: {SCAN_DPI}")
    print(f"{'variant':44} {'lines':>5} {'CER':>7} {'conf':>6}")
    print("-" * 66)

    base300 = vector_page(SCAN_DPI)

    # Reference point: the ideal render, no storage round trip.
    lines, cer, conf = measure(engine, vector_page(OCR_DPI))
    print(f"{'IDEAL vector, no storage round trip':44} {lines:>5} {cer:>7.4f} {conf:>6.3f}")

    # Storage round trip alone, at the generator's JPEG quality.
    lines, cer, conf = measure(engine, embed_and_render(base300, 92, OCR_DPI))
    print(f"{'storage round trip, JPEG q92':44} {lines:>5} {cer:>7.4f} {conf:>6.3f}")
    lines, cer, conf = measure(engine, embed_and_render(base300, 70, OCR_DPI))
    print(f"{'storage round trip, JPEG q70 (generator)':44} {lines:>5} {cer:>7.4f} {conf:>6.3f}")

    print()
    # Each degradation alone, then stored and rendered the way the pipeline does.
    factors: list[tuple[str, Image.Image, int]] = []
    for quality in (78, 60, 45):
        factors.append(
            (f"JPEG q{quality} only", jpeg_roundtrip(base300, quality), 92)
        )
    for radius in (0.3, 0.6, 1.0):
        factors.append(
            (f"blur r{radius} only", base300.filter(ImageFilter.GaussianBlur(radius)), 92)
        )
    for sigma in (2.0, 4.0, 9.0, 16.0):
        factors.append((f"noise sigma{sigma} only", add_noise(base300, sigma, 0), 92))
    for count in (150, 500, 1500):
        factors.append((f"speckles {count} only", add_noise(base300, 0.0, count), 92))
    factors.append(("vignette", _vignette(base300), 92))

    for label, image, storage_quality in factors:
        rendered = embed_and_render(image, storage_quality, OCR_DPI)
        lines, cer, conf = measure(engine, rendered)
        print(f"{label:44} {lines:>5} {cer:>7.4f} {conf:>6.3f}")

    print()
    # The generator's current "clean" profile, reproduced exactly.
    clean = base300.filter(ImageFilter.GaussianBlur(0.3))
    clean = add_noise(clean, 4.0, 0)
    clean = jpeg_roundtrip(clean, 78)
    lines, cer, conf = measure(engine, embed_and_render(clean, 70, OCR_DPI))
    print(f"{'CURRENT clean profile (blur.3+noise4+q78)':44} {lines:>5} {cer:>7.4f} {conf:>6.3f}")

    # Candidate replacements.
    for label, image, quality in (
        ("CANDIDATE no degradation, q92", base300, 92),
        ("CANDIDATE mild noise1.5, q92", add_noise(base300, 1.5, 0), 92),
        ("CANDIDATE blur0.4 only, q92", base300.filter(ImageFilter.GaussianBlur(0.4)), 92),
    ):
        lines, cer, conf = measure(engine, embed_and_render(image, quality, OCR_DPI))
        print(f"{label:44} {lines:>5} {cer:>7.4f} {conf:>6.3f}")

    print()
    # And the actual stored scan, for comparison with the model above.
    scan = CORPUS / "files" / "scan_a_clean_300dpi.pdf"
    doc = pymupdf.open(str(scan))
    pixmap = doc.load_page(PAGE_INDEX).get_pixmap(dpi=OCR_DPI, alpha=False)
    doc.close()
    stored = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    lines, cer, conf = measure(engine, stored)
    print(f"{'ACTUAL stored scan_a @200dpi':44} {lines:>5} {cer:>7.4f} {conf:>6.3f}")
    return 0


def _vignette(image: Image.Image) -> Image.Image:
    array = np.asarray(image).astype("float32")
    height, width, _ = array.shape
    ys = np.linspace(-1.0, 1.0, height)[:, None]
    xs = np.linspace(-1.0, 1.0, width)[None, :]
    radius = np.sqrt(xs**2 + ys**2) / (2.0**0.5)
    mask = np.clip(1.0 - 0.22 * radius**2, 0.0, 1.0)
    array = array * mask[:, :, None]
    return Image.fromarray(np.clip(array, 0, 255).astype("uint8"), "RGB")


if __name__ == "__main__":
    raise SystemExit(main())
