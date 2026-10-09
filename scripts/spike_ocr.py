"""OCR engine spike: compare candidate engines on the scanned corpus.

The spec (5.3, 12.2) requires a measured OCR character error rate on clear scans
and an explicit, compared choice of engine.  This script runs every candidate
that is actually installed, reports timing and CER against the corpus ground
truth, and writes the comparison so the decision is reproducible rather than
asserted.

A candidate that is not installed is reported as ``not installed`` rather than
being skipped silently: the point of the table is to show what was compared.

Run::

    python scripts/spike_ocr.py [--limit 3] [--engine rapidocr]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

CORPUS = Path("benchmarks/corpus")

# Make ``benchmarks`` importable when this is run as a plain script rather than
# as a module, which is how the evidence commands invoke it.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.rr_m0.ocr_metrics import (  # noqa: E402
    align_lines,
    character_error_rate,
    edit_distance,
    multiset_character_error_rate,
    reading_order_score,
)


# ---------------------------------------------------------------------------
# metric
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Fold text for comparison, keeping only characters that carry meaning.

    Whitespace, punctuation width and case are all removed: OCR errors that
    matter are wrong characters, and including layout noise would flatter or
    punish an engine for reasons unrelated to recognition.
    """
    text = text.replace("\ufffd", "")
    text = re.sub(r"\s+", "", text)
    text = text.lower()
    # Normalise full-width forms before dropping punctuation, so that a
    # full-width comma does not count as a character insertion.
    import unicodedata

    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def character_error_rate_for(reference: str, hypothesis: str) -> float:
    """Strict CER on the folded text, for callers that want the hard measure."""
    return character_error_rate(_normalise(reference), _normalise(hypothesis))


# ---------------------------------------------------------------------------
# engines
# ---------------------------------------------------------------------------


@dataclass
class OcrResult:
    text: str
    lines: list[tuple[float, float, float, float, float, str]] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None


class Engine:
    name = "base"
    version = "?"

    @classmethod
    def available(cls) -> tuple[bool, str]:
        raise NotImplementedError

    def recognise(self, image_bytes: bytes) -> OcrResult:
        raise NotImplementedError


class RapidOcrEngine(Engine):
    name = "rapidocr_onnxruntime"

    def __init__(self) -> None:
        import numpy as np
        from PIL import Image

        self._np = np
        self._Image = Image
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()
        self.version = _module_version("rapidocr_onnxruntime")

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return True, _module_version("rapidocr_onnxruntime")

    def recognise(self, image_bytes: bytes) -> OcrResult:
        import io

        started = time.perf_counter()
        image = self._Image.open(io.BytesIO(image_bytes)).convert("RGB")
        array = self._np.asarray(image)
        result, _elapsed = self._engine(array)
        seconds = time.perf_counter() - started
        if not result:
            return OcrResult(text="", lines=[], seconds=seconds)
        lines: list[tuple[float, float, float, float, float, str]] = []
        texts: list[str] = []
        for box, text, score in result:
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            lines.append((min(xs), min(ys), max(xs), max(ys), float(score), text))
            texts.append(text)
        return OcrResult(text="\n".join(texts), lines=lines, seconds=seconds)


class TesseractEngine(Engine):
    name = "tesseract"

    def __init__(self) -> None:
        import pytesseract

        self._pytesseract = pytesseract
        self.version = str(pytesseract.get_tesseract_version())

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import pytesseract
        except Exception as exc:
            return False, f"pytesseract not importable: {exc}"
        try:
            version = pytesseract.get_tesseract_version()
        except Exception as exc:
            return False, f"tesseract binary not usable: {type(exc).__name__}"
        return True, str(version)

    def recognise(self, image_bytes: bytes) -> OcrResult:
        import io

        started = time.perf_counter()
        image = self._Image.open(io.BytesIO(image_bytes))
        data = self._pytesseract.image_to_data(
            image, output_type=self._pytesseract.Output.DICT
        )
        seconds = time.perf_counter() - started
        texts: list[str] = []
        lines: list[tuple[float, float, float, float, float, str]] = []
        for i, text in enumerate(data["text"]):
            if not text.strip():
                continue
            texts.append(text)
            lines.append(
                (
                    float(data["left"][i]),
                    float(data["top"][i]),
                    float(data["left"][i] + data["width"][i]),
                    float(data["top"][i] + data["height"][i]),
                    float(data["conf"][i]) / 100.0,
                    text,
                )
            )
        return OcrResult(text="\n".join(texts), lines=lines, seconds=seconds)

    @property
    def _Image(self):
        from PIL import Image

        return Image


def _module_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name.replace("_", "-"))
    except Exception:
        try:
            module = __import__(name)
            return str(getattr(module, "__version__", "?"))
        except Exception:
            return "?"


ENGINES: dict[str, type[Engine]] = {
    "rapidocr": RapidOcrEngine,
    "tesseract": TesseractEngine,
}


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def load_truth(name: str) -> dict:
    return json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))


def truth_text(truth: dict, page_index: int) -> str:
    """The text actually drawn on one page.

    Uses ``visible_text``, which is recorded from the lines that were really
    drawn.  The block-level ``text`` is the *semantic* text: for a paragraph that
    flows across a page boundary it is the whole paragraph, so using it would
    count text that is on the next page as missing from this one, and it omits
    drawn decoration such as an ``Abstract-`` prefix.
    """
    for page in truth["pages"]:
        if page["index"] != page_index:
            continue
        if "visible_text" in page:
            return page["visible_text"]
        return "\n".join(block["text"] for block in page["blocks"])
    return ""


def truth_lines(truth: dict, page_index: int) -> list[str]:
    """The visual lines drawn on one page, in reading order.

    Falls back to splitting the flattened text (which yields one line per
    paragraph) for truth files written before line-level recording existed, so an
    old corpus degrades to "order not measurable" rather than to a wrong score.
    """
    for page in truth["pages"]:
        if page["index"] != page_index:
            continue
        lines = page.get("visible_lines")
        if lines:
            return [line for line in lines if line.strip()]
        return [
            line
            for line in page.get("visible_text", "").split("\n")
            if line.strip()
        ]
    return []


def truth_blocks(truth: dict, page_index: int) -> list[dict]:
    """Per-page visible blocks, with a fallback for older truth files."""
    for page in truth["pages"]:
        if page["index"] != page_index:
            continue
        if "visible_blocks" in page:
            return page["visible_blocks"]
        return page["blocks"]
    return []


def render_pages(pdf: Path, page_indices: list[int], dpi: int) -> dict[int, bytes]:
    doc = pymupdf.open(str(pdf))
    try:
        out: dict[int, bytes] = {}
        for index in page_indices:
            pixmap = doc.load_page(index).get_pixmap(dpi=dpi, alpha=False)
            out[index] = pixmap.tobytes("png")
        return out
    finally:
        doc.close()


def evaluate_document(
    engine: Engine,
    name: str,
    pdf: Path,
    truth: dict,
    page_indices: list[int],
    dpi: int = 200,
) -> dict:
    images = render_pages(pdf, page_indices, dpi)
    per_page: list[dict] = []
    total_reference = 0
    total_recognition_errors = 0.0
    total_seconds = 0.0
    for index in page_indices:
        result = engine.recognise(images[index])
        reference = truth_text(truth, index)
        # The drawn lines, not the flattened paragraph text.  Order can only be
        # scored against line boundaries: a reference of one line per paragraph
        # cannot be aligned against the ~50-100 lines a page is recognised as, and
        # an earlier version of this script scored such a comparison as perfect.
        reference_lines = truth_lines(truth, index)
        recognised_lines = [line for line in result.text.split("\n") if line.strip()]
        folded_reference = _normalise(reference)
        folded_recognised = _normalise(result.text)

        # Recognition: order-insensitive, because line order is the layout
        # module's responsibility and a two-column page's natural render order
        # differs from its semantic block order.
        recognition_cer = multiset_character_error_rate(folded_reference, folded_recognised)
        # Order: measured separately so it cannot hide inside the above.
        matched_fraction, _pairs = align_lines(reference_lines, recognised_lines)
        order_score = reading_order_score(reference_lines, recognised_lines)

        ref_len = len(folded_reference)
        total_reference += ref_len
        total_recognition_errors += recognition_cer * ref_len
        total_seconds += result.seconds
        per_page.append(
            {
                "page": index,
                "cer_order_insensitive": round(recognition_cer, 4),
                "cer_strict": round(
                    character_error_rate(folded_reference, folded_recognised), 4
                ),
                "line_match_fraction": round(matched_fraction, 3),
                # None means "not measured", never "perfect".  The summary below
                # reports such pages separately rather than averaging a value in.
                "reading_order_score": (
                    round(order_score, 3) if order_score is not None else None
                ),
                "reference_chars": ref_len,
                "recognised_chars": len(folded_recognised),
                "reference_lines": len(reference_lines),
                "recognised_lines": len(recognised_lines),
                "seconds": round(result.seconds, 2),
                "mean_confidence": round(
                    sum(line[4] for line in result.lines) / len(result.lines), 3
                )
                if result.lines
                else None,
            }
        )
    return {
        "document": name,
        "pages": per_page,
        "cer": round(total_recognition_errors / total_reference, 4)
        if total_reference
        else None,
        "reference_chars": total_reference,
        "seconds": round(total_seconds, 2),
        "dpi": dpi,
    }


def _production_dpi() -> int:
    """The render resolution the product itself uses.

    Imported from the adapter rather than repeated here.  If this script used its
    own default, an acceptance run could pass at a resolution the product never
    uses -- which is precisely how the 200 dpi figure in an earlier version of this
    evidence came to disagree with the calibrated 250 dpi in the code.
    """
    try:
        from rr_adapters.ocr import OCR_RENDER_DPI
    except Exception:
        return 200
    return int(OCR_RENDER_DPI)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=2, help="pages per document")
    parser.add_argument("--engine", action="append", help="restrict to these engines")
    parser.add_argument(
        "--dpi",
        type=int,
        default=None,
        help=(
            "render resolution; defaults to the calibrated production value from "
            "rr_adapters.ocr.OCR_RENDER_DPI, so this evidence measures what the "
            "product actually does"
        ),
    )
    parser.add_argument("--out", default="benchmarks/out/ocr_spike.json")
    args = parser.parse_args()

    dpi = args.dpi if args.dpi is not None else _production_dpi()

    selected = args.engine or list(ENGINES)
    availability: dict[str, str] = {}
    instances: dict[str, Engine] = {}
    for key in selected:
        cls = ENGINES[key]
        ok, detail = cls.available()
        availability[key] = f"{'available' if ok else 'unavailable'}: {detail}"
        print(f"{key:12} {availability[key]}")
        if ok:
            try:
                instances[key] = cls()
            except Exception as exc:
                availability[key] = f"failed to initialise: {type(exc).__name__}: {exc}"
                print(f"{key:12} {availability[key]}")

    if not instances:
        print("\nNo OCR engine is available. OCR acceptance remains PENDING.")
        return 2

    documents = [
        ("scan_a_clean_300dpi", "pdf"),
        ("scan_b_two_column_noisy", "pdf"),
        ("scan_c_skewed", "pdf"),
        ("scan_d_low_dpi", "pdf"),
        ("scan_e_grayscale", "pdf"),
    ]
    report: dict = {
        "engines": availability,
        "dpi": dpi,
        "results": {},
    }
    for key, engine in instances.items():
        print(f"\n=== engine: {key} (version {engine.version}) ===")
        engine_results = []
        for name, _kind in documents:
            pdf = CORPUS / "files" / f"{name}.pdf"
            truth = load_truth(name)
            page_count = len(truth["pages"])
            indices = list(range(min(args.limit, page_count)))
            started = time.perf_counter()
            result = evaluate_document(engine, name, pdf, truth, indices, dpi)
            wall = time.perf_counter() - started
            result["wall_seconds"] = round(wall, 2)
            engine_results.append(result)
            print(
                f"  {name:26} pages={indices} CER={result['cer']} "
                f"ref_chars={result['reference_chars']} ocr={result['seconds']}s wall={wall:.1f}s"
            )
            for page in result["pages"]:
                print(
                    f"      page {page['page']}: cer={page['cer_order_insensitive']} "
                    f"strict={page['cer_strict']} lines={page['recognised_lines']}"
                    f"/{page['reference_lines']} order={page['reading_order_score']} "
                    f"conf={page['mean_confidence']}"
                )
        report["results"][key] = engine_results

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {out}")

    # Summarise against the spec threshold, reporting each document separately:
    # a single averaged number would hide that a low-dpi or noisy scan performs
    # worse.  The threshold is applied only to the documents it is defined for
    # (clear scans); degraded inputs are reported, not excused.
    print("\nOCR recognition CER against the 5% provisional threshold (spec 12.2):")
    print("  metric: character error rate ignoring line order")
    clear_scan_documents = {"scan_a_clean_300dpi", "scan_c_skewed", "scan_e_grayscale"}
    for key, results in report["results"].items():
        for result in results:
            if result["cer"] is None:
                continue
            name = result["document"]
            if name in clear_scan_documents:
                verdict = "PASS" if result["cer"] <= 0.05 else "FAIL"
            elif "low_dpi" in name:
                verdict = "reported separately (low-resolution scan)"
            else:
                verdict = "reported separately (degraded scan, not a clear scan)"
            order = ", ".join(
                f"p{p['page']}={'not measured' if p['reading_order_score'] is None else p['reading_order_score']}"
                for p in result["pages"]
            )
            print(f"  {key:12} {name:26} {result['cer']:.4f}  {verdict}")
            print(f"  {'':12} {'':26} reading-order score: {order}")
            unmatched = [
                p["page"] for p in result["pages"] if p["reading_order_score"] is None
            ]
            if unmatched:
                # Stated explicitly: an unmeasured score is not a pass, and a
                # summary that quietly omitted it would read as one.
                print(
                    f"  {'':12} {'':26} order NOT MEASURED on page(s) "
                    f"{unmatched}: too few reference lines could be matched"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
