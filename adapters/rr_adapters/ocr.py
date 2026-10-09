"""OCR provider: recognises text lines on a rendered page image.

The engine is RapidOCR with bundled ONNX models, so recognition needs no network
access and no model download.  See ADR 0004 for why this engine was chosen and for
the measurements behind the tunables exposed here.

The provider's contract is deliberately narrow: it takes a page image and returns
:class:`TextLine` objects in **normalised page coordinates**, which is the
coordinate contract from ADR 0008.  It does not decide which pages need OCR (the
parser does that, from the text layer) and it does not assemble paragraphs (the
layout module does that).  Keeping it to one job is what makes it replaceable.

Two measured facts shape this module and are worth stating where the code lives:

* **The engine is deterministic.**  Repeated runs on the same image at the same
  resolution produce byte-identical text.  Anything that varies is a property of
  the input, so a threshold on the output is meaningful rather than noise.
* **Resolution is not monotonic.**  A page can recognise better at 150 dpi than at
  200 dpi.  The fixed default below is the best of the policies that were actually
  measured, not a guess, and native resolution is deliberately *not* used because
  it measured worse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from document_core.cleaning import clean_text
from document_core.coords import BBox, PageGeometry

__all__ = [
    "OCRLine",
    "OCRResult",
    "OCRProvider",
    "RapidOCRProvider",
    "OCR_RENDER_DPI",
    "MIN_LINE_CONFIDENCE",
    "DEFAULT_MIN_LINE_CONFIDENCE",
    "available_engine",
]

#: Render resolution used for recognition.
#:
#: Calibrated in ADR 0004.  The choice is not "the highest that fits": resolution
#: is non-monotonic, so it is a parameter that has to be measured.  Against five
#: scanned documents (two pages each) on this machine:
#:
#: ====== ========= ===========
#: dpi    mean CER  mean seconds
#: ====== ========= ===========
#: 150    0.0342    4.28
#: 200    0.0448    4.75
#: 250    0.0247    5.76
#: 300    0.0298    6.19
#: 400    0.0206    7.45
#: ====== ========= ===========
#:
#: 250 dpi is chosen because it halves the error of 200 dpi for about one second
#: per page, whereas 300 dpi costs more than 250 *and* recognises slightly worse.
#: 400 dpi is the most accurate but costs 1.7 s per page more than 250 for a
#: further 0.004, which is not worth it.  This is a calibrated parameter with a
#: record: changing it means re-running ``scripts/cost_ocr_resolution.py``.
OCR_RENDER_DPI = 250

#: Lines below this confidence are kept but flagged for review rather than
#: silently dropped.  A dropped line is invisible; a flagged one is not.
#:
#: This is a *review* threshold, not a correctness signal: mean page confidence was
#: measured against true error and correlates positively with it (+0.17), so
#: confidence must not be used to choose between competing recognition attempts.
MIN_LINE_CONFIDENCE = 0.5

DEFAULT_MIN_LINE_CONFIDENCE = MIN_LINE_CONFIDENCE


@dataclass
class OCRLine:
    """One recognised line, in normalised page coordinates."""

    text: str
    bbox: BBox
    confidence: float

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < MIN_LINE_CONFIDENCE


@dataclass
class OCRResult:
    """Everything one recognition pass produced."""

    lines: list[OCRLine] = field(default_factory=list)
    engine: str = ""
    engine_version: str = ""
    render_dpi: int = OCR_RENDER_DPI
    image_width: int = 0
    image_height: int = 0
    mean_confidence: float = 0.0
    low_confidence_lines: int = 0
    seconds: float = 0.0
    issues: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """The recognised lines joined in visual order."""
        return "\n".join(line.text for line in self.lines)


def available_engine() -> tuple[str, str] | None:
    """Return ``(engine, version)`` if the OCR engine can be imported.

    Callers use this to degrade honestly: a scanned document with no OCR engine
    available is reported as unreadable, not as an empty page.
    """
    try:
        import rapidocr_onnxruntime  # noqa: PLC0415
    except Exception:  # pragma: no cover - depends on the environment
        return None
    version = getattr(rapidocr_onnxruntime, "__version__", "unknown")
    return "rapidocr", version


class OCRProvider:
    """Interface.  Implementations must not require network access."""

    name = "abstract"
    version = "0"

    def recognize(
        self,
        image: Any,
        geometry: PageGeometry,
        *,
        page_index: int = 0,
    ) -> OCRResult:
        raise NotImplementedError


class RapidOCRProvider(OCRProvider):
    """RapidOCR with bundled ONNX models.

    The engine object is created once and reused, because constructing it loads
    the models and costs seconds; per-page recognition is the cheap part.
    """

    name = "rapidocr"

    def __init__(self, *, engine: Any | None = None) -> None:
        if engine is not None:
            self._engine = engine
            self.version = "injected"
            return
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

        self._engine = RapidOCR()
        self.version = self._detect_version()

    @staticmethod
    def _detect_version() -> str:
        try:
            import rapidocr_onnxruntime  # noqa: PLC0415
        except Exception:  # pragma: no cover
            return "unknown"
        return str(getattr(rapidocr_onnxruntime, "__version__", "unknown"))

    def recognize(
        self,
        image: Any,
        geometry: PageGeometry,
        *,
        page_index: int = 0,
    ) -> OCRResult:
        """Recognise lines on ``image`` and map them into normalised space.

        ``image`` is an HxWx3 uint8 array as produced by rendering a page.  The
        mapping divides by the *image* size rather than the geometry's display
        size, because the engine's boxes are in image pixels: using the page size
        would be correct only when the render happens to match the page's aspect
        exactly, which is not guaranteed.
        """
        import time  # noqa: PLC0415

        started = time.perf_counter()
        raw, _elapsed = self._engine(image)
        seconds = time.perf_counter() - started

        height, width = _image_size(image)
        result = OCRResult(
            engine=self.name,
            engine_version=self.version,
            render_dpi=OCR_RENDER_DPI,
            image_width=width,
            image_height=height,
            seconds=seconds,
        )
        if not raw:
            # No text found is a legitimate outcome for a blank page and an
            # alarming one for a page of prose.  Which it is cannot be decided
            # here, so it is recorded and the parser judges it.
            result.issues.append("no_text_detected")
            return result

        scores: list[float] = []
        weights: list[int] = []
        for entry in raw:
            line = self._to_line(entry, width, height)
            if line is None:
                continue
            result.lines.append(line)
            scores.append(line.confidence)
            weights.append(max(1, len(line.text)))
            if line.is_low_confidence:
                result.low_confidence_lines += 1

        if result.lines:
            # Weighted by length: an unweighted mean lets one short uncertain
            # line dominate a page of long confident ones.
            total = sum(weights)
            result.mean_confidence = (
                sum(s * w for s, w in zip(scores, weights)) / total if total else 0.0
            )
        if not result.lines:
            result.issues.append("no_text_detected")
        if result.low_confidence_lines:
            result.issues.append(f"low_confidence_lines:{result.low_confidence_lines}")
        return result

    def _to_line(self, entry: Any, width: int, height: int) -> OCRLine | None:
        """Convert one engine entry into a line, or ``None`` if unusable."""
        try:
            box, text, score = entry[0], entry[1], entry[2]
        except (TypeError, IndexError, KeyError):
            return None
        text = (text or "").strip()
        if not text:
            return None
        try:
            confidence = float(score)
        except (TypeError, ValueError):
            confidence = 0.0
        bbox = _box_to_normalised(box, width, height)
        if bbox is None:
            return None
        return OCRLine(text=text, bbox=bbox, confidence=confidence)


def _image_size(image: Any) -> tuple[int, int]:
    """Return ``(height, width)`` for an array or a PIL image."""
    shape = getattr(image, "shape", None)
    if shape is not None and len(shape) >= 2:
        return int(shape[0]), int(shape[1])
    size = getattr(image, "size", None)
    if size is not None and len(size) == 2:
        return int(size[1]), int(size[0])
    raise TypeError("cannot determine image size")


def _box_to_normalised(
    box: Sequence[Sequence[float]], width: int, height: int
) -> BBox | None:
    """Normalise a quadrilateral to an axis-aligned box in 0-1 display units.

    The engine returns four corner points, which may be slightly rotated.  Taking
    the axis-aligned hull loses that tilt deliberately: the stored contract is an
    axis-aligned box (ADR 0008), and inventing a rotated box here would be a
    second, undocumented coordinate representation.
    """
    if not box or width <= 0 or height <= 0:
        return None
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
    except (TypeError, IndexError, ValueError):
        return None
    if not xs or not ys:
        return None
    # Divide by the image extent and clamp: a box the engine reports one pixel
    # outside the image would otherwise produce a coordinate outside 0-1.
    x0 = min(max(min(xs) / width, 0.0), 1.0)
    x1 = min(max(max(xs) / width, 0.0), 1.0)
    y0 = min(max(min(ys) / height, 0.0), 1.0)
    y1 = min(max(max(ys) / height, 0.0), 1.0)
    if x1 <= x0 or y1 <= y0:
        return None
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def ocr_lines_to_text_lines(
    result: OCRResult, *, page_index: int = 0
) -> list[Any]:
    """Adapt :class:`OCRLine` objects into layout :class:`TextLine` objects.

    Kept as a function rather than a method so the OCR provider does not depend on
    the layout module, and the conversion can be tested on its own.
    """
    from document_core.layout import TextLine  # noqa: PLC0415

    return [
        TextLine(
            text=clean_text(line.text, dehyphenate_lines=False, join_wrapped_lines=False),
            bbox=line.bbox,
            page_index=page_index,
            confidence=line.confidence,
            source="ocr",
        )
        for line in result.lines
    ]
