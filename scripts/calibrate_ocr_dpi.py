"""Should OCR render at the page's native resolution instead of a fixed dpi?

The pipeline currently renders every page at a fixed 200 dpi.  A page whose scan
was stored at 300 dpi is therefore resampled *down* before recognition, which
discards detail the recogniser could have used.  This measures recognition at a
range of render resolutions for each scanned document and reports which is best,
so the render-resolution policy can be decided on evidence rather than on the
intuition that "higher is better".

Recognition is scored with the order-insensitive character error rate, because
reading order is the layout module's responsibility and every document here
already scores 1.0 on reading order.
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

DOCUMENTS = [
    "scan_a_clean_300dpi",
    "scan_b_two_column_noisy",
    "scan_c_skewed",
    "scan_d_low_dpi",
    "scan_e_grayscale",
]
DPI_CHOICES = (150, 200, 250, 300, 350, 400)


def native_dpi(path: Path, page_index: int) -> float:
    """The dpi at which the page's largest image is stored."""
    doc = pymupdf.open(str(path))
    page = doc.load_page(page_index)
    infos = page.get_image_info()
    doc.close()
    if not infos:
        return 0.0
    info = max(infos, key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))
    box_width = info["bbox"][2] - info["bbox"][0]
    return info["width"] * 72.0 / box_width if box_width else 0.0


def main() -> int:
    engine = RapidOCR()
    summary: dict[str, dict[int, float]] = {}
    print(f"{'document':24} {'page':>4} {'native':>7} " + " ".join(f"{d:>7}" for d in DPI_CHOICES))
    print("-" * (40 + 8 * len(DPI_CHOICES)))
    for document in DOCUMENTS:
        path = CORPUS / "files" / f"{document}.pdf"
        truth = json.loads((CORPUS / "truth" / f"{document}.json").read_text(encoding="utf-8"))
        page_count = len(truth["pages"])
        for page_index in range(min(2, page_count)):
            reference = _normalise(truth_text(truth, page_index))
            native = native_dpi(path, page_index)
            scores: dict[int, float] = {}
            for dpi in DPI_CHOICES:
                doc = pymupdf.open(str(path))
                pixmap = doc.load_page(page_index).get_pixmap(dpi=dpi, alpha=False)
                doc.close()
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                result, _elapsed = engine(np.asarray(image))
                text = "\n".join(t for _b, t, _s in (result or []))
                scores[dpi] = multiset_character_error_rate(reference, _normalise(text))
            summary[f"{document}#{page_index}"] = scores
            best = min(scores, key=lambda d: scores[d])
            cells = " ".join(
                f"{scores[d]:>6.4f}{'*' if d == best else ' '}" for d in DPI_CHOICES
            )
            print(f"{document:24} {page_index:>4} {native:>7.0f} {cells}")

    print()
    print("best render resolution per page (* above):")
    for key, scores in summary.items():
        best = min(scores, key=lambda d: scores[d])
        at_fixed_200 = scores[200]
        print(
            f"  {key:34} best={best:>3} dpi ({scores[best]:.4f})  "
            f"at 200 dpi: {at_fixed_200:.4f}"
        )

    # What the acceptance figure would be under each policy, per document.
    print()
    print("document-level recognition CER under each policy:")
    print(f"  {'document':24} {'fixed 200':>10} {'native':>10} {'best of range':>14}")
    for document in DOCUMENTS:
        keys = [k for k in summary if k.startswith(f"{document}#")]
        fixed = float(np.mean([summary[k][200] for k in keys]))
        try:
            path = CORPUS / "files" / f"{document}.pdf"
            native_values = []
            for k in keys:
                page_index = int(k.split("#")[1])
                native = native_dpi(path, page_index)
                nearest = min(DPI_CHOICES, key=lambda d: abs(d - native))
                native_values.append(summary[k][nearest])
            native_score = float(np.mean(native_values))
        except Exception as exc:  # pragma: no cover - diagnostic only
            native_score = float("nan")
            print(f"    (native lookup failed: {exc})")
        best = float(np.mean([min(summary[k].values()) for k in keys]))
        print(f"  {document:24} {fixed:>10.4f} {native_score:>10.4f} {best:>14.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
