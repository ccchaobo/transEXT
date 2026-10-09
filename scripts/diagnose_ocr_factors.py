"""Isolate which degradation factor costs OCR accuracy.

The scanned corpus scores far worse than the vector source it was made from, so
one of the scan-simulation steps is destroying legibility.  Rather than guessing,
this sweeps each factor independently from an ideal page and reports the cost of
each.  The result decides how the *clean* scan profile should be parameterised:
a "clean scan" must be degraded only to the degree a real flatbed scan is.

Factors: JPEG quality, Gaussian blur radius, additive noise sigma, speckle count,
and the render dpi used at OCR time.
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter
from rapidocr_onnxruntime import RapidOCR

CORPUS = Path("benchmarks/corpus")

try:
    from scripts.spike_ocr import _normalise, edit_distance
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise, edit_distance

TRUTH = json.loads((CORPUS / "truth" / "scan_a_clean_300dpi.json").read_text(encoding="utf-8"))
PAGE = next(p for p in TRUTH["pages"] if p["index"] == 0)
REFERENCE = "\n".join(b["text"] for b in PAGE["blocks"])
REF_CHARS = len(_normalise(REFERENCE))


def base_image(dpi: int) -> Image.Image:
    doc = pymupdf.open(str(CORPUS / "files" / "pdf_a_single_column.pdf"))
    pixmap = doc.load_page(0).get_pixmap(dpi=dpi, alpha=False)
    doc.close()
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def jpeg_roundtrip(image: Image.Image, quality: int) -> Image.Image:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def add_noise(image: Image.Image, sigma: float, speckles: int, seed: int = 7) -> Image.Image:
    rng = random.Random(seed)
    array = np.asarray(image).astype("float32")
    if sigma > 0:
        generator = np.random.default_rng(seed)
        array = array + generator.normal(0.0, sigma, array.shape)
        # The per-pixel Python loop the corpus generator uses is far too slow at
        # full page size; a vectorised generator is equivalent for this purpose.
    array = np.clip(array, 0, 255).astype("uint8")
    out = Image.fromarray(array, "RGB")
    if speckles:
        draw = ImageDraw.Draw(out)
        width, height = out.size
        for _ in range(speckles):
            x, y = rng.randrange(width), rng.randrange(height)
            value = rng.randrange(30, 120)
            draw.point((x, y), fill=(value, value, value))
    return out


def measure(engine: RapidOCR, image: Image.Image) -> tuple[int, float, float, float]:
    started = time.perf_counter()
    result, _elapsed = engine(np.asarray(image))
    seconds = time.perf_counter() - started
    if not result:
        return 0, 1.0, 0.0, seconds
    lines = [text for _box, text, _score in result]
    flat = _normalise("\n".join(lines))
    distance = edit_distance(_normalise(REFERENCE), flat)
    confidence = sum(float(score) for _box, _text, score in result) / len(result)
    return len(lines), distance / max(1, REF_CHARS), confidence, seconds


def main() -> int:
    engine = RapidOCR()
    print(f"reference characters: {REF_CHARS}")
    print(f"{'variant':38} {'lines':>5} {'CER':>7} {'conf':>6} {'sec':>6}")
    print("-" * 70)

    # Baseline: the vector page, i.e. no degradation at all.
    for dpi in (200, 300):
        image = base_image(dpi)
        lines, cer, conf, seconds = measure(engine, image)
        print(f"{'ideal vector @%d dpi' % dpi:38} {lines:>5} {cer:>7.4f} {conf:>6.3f} {seconds:>6.1f}")

    print()
    # Each factor alone, applied to a 300 dpi base.
    base300 = base_image(300)
    sweeps: list[tuple[str, Image.Image]] = [("300dpi, no degradation", base300)]
    for quality in (95, 85, 78, 65, 50):
        sweeps.append((f"+ JPEG q{quality}", jpeg_roundtrip(base300, quality)))
    for radius in (0.3, 0.5, 0.8, 1.2):
        sweeps.append(
            (f"+ blur r{radius}", base300.filter(ImageFilter.GaussianBlur(radius)))
        )
    for sigma in (2.0, 4.0, 8.0, 13.0):
        sweeps.append((f"+ noise sigma{sigma}", add_noise(base300, sigma, 0)))
    for count in (100, 400, 1500):
        sweeps.append((f"+ speckles {count}", add_noise(base300, 0.0, count)))

    for label, image in sweeps:
        lines, cer, conf, seconds = measure(engine, image)
        print(f"{label:38} {lines:>5} {cer:>7.4f} {conf:>6.3f} {seconds:>6.1f}")

    print()
    # The combination the corpus currently uses for a "clean" scan.
    combined = base300
    combined = combined.filter(ImageFilter.GaussianBlur(0.3))
    combined = add_noise(combined, 4.0, 0)
    combined = jpeg_roundtrip(combined, 78)
    lines, cer, conf, seconds = measure(engine, combined)
    print(f"{'CURRENT clean profile (blur+noise+jpeg78)':38} {lines:>5} {cer:>7.4f} {conf:>6.3f} {seconds:>6.1f}")

    # A realistic mild flatbed scan.
    mild = base300
    mild = add_noise(mild, 1.5, 0)
    mild = jpeg_roundtrip(mild, 92)
    lines, cer, conf, seconds = measure(engine, mild)
    print(f"{'MILD profile (noise1.5 + jpeg92)':38} {lines:>5} {cer:>7.4f} {conf:>6.3f} {seconds:>6.1f}")

    # JPEG only at high quality: the most common real-world case.
    lines, cer, conf, seconds = measure(engine, jpeg_roundtrip(base300, 95))
    print(f"{'JPEG q95 only':38} {lines:>5} {cer:>7.4f} {conf:>6.3f} {seconds:>6.1f}")

    # And the mild profile rendered at 200 dpi from the *scan*, which is what the
    # pipeline actually sees when it re-renders an embedded image.
    import io

    buffer = io.BytesIO()
    mild.save(buffer, format="JPEG", quality=92)
    buffer.seek(0)
    doc = pymupdf.open()
    page = doc.new_page(width=595.276, height=841.89)
    page.insert_image(page.rect, stream=buffer.getvalue())
    for dpi in (150, 200, 300):
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        lines, cer, conf, seconds = measure(engine, image)
        print(f"{'MILD scan re-rendered @%d dpi' % dpi:38} {lines:>5} {cer:>7.4f} {conf:>6.3f} {seconds:>6.1f}")
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
