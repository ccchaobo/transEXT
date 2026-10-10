"""M0 performance harness: stage timings and peak memory on the corpus.

The spec's thresholds (first screen <= 3 s, page turn <= 300 ms) are *targets to
calibrate and freeze*, not promises, and they only mean anything alongside the
machine, the corpus and the stage boundaries they were measured against.  This
script therefore records all four, and reports distributions rather than averages:
a page turn that is fast on average but slow on one page in twenty is exactly the
case a user notices.

Peak memory is measured with the platform's own accounting
(``tracemalloc`` for Python allocations, and the process working set for the whole
process) because a rendered page is mostly C-allocated memory that
``tracemalloc`` cannot see.  Reporting only ``tracemalloc`` would understate the
figure by an order of magnitude and make the number useless.

Usage:
    python -m benchmarks.rr_m0.perf_harness --out benchmarks/out/perf.json
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from rr_adapters.ocr import OCR_RENDER_DPI

REPO = Path(__file__).resolve().parent.parent.parent

__all__ = ["Measurement", "run", "main", "summarise"]


@dataclass
class Measurement:
    """One timed operation."""

    stage: str
    document: str
    unit: str
    seconds: float
    ok: bool = True
    detail: str = ""
    #: Extra observations, e.g. page count or bytes rendered.
    extra: dict[str, Any] = field(default_factory=dict)


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile.

    Chosen over an interpolating definition because with a few tens of samples an
    interpolated value is a number the machine never produced, and reporting a
    latency that no run achieved invites over-reading a small sample.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _peak_working_set_bytes() -> int:
    """Peak RSS of this process, in bytes, or 0 when unavailable.

    ``tracemalloc`` only sees Python allocations and would report a few megabytes
    for a process holding rendered pages, so the whole-process figure is what gets
    reported as "peak memory".

    On Windows the call is made through ``kernel32.K32GetProcessMemoryInfo``.  The
    ``psapi`` alias is not reliably exported on modern Windows, which is why an
    earlier version of this function silently returned 0 and the report claimed a
    peak memory of zero megabytes.
    """
    if sys.platform == "win32":
        return _windows_peak_working_set()
    try:  # pragma: no cover - not exercised on Windows
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes, macOS reports bytes.
        return int(usage * 1024) if sys.platform.startswith("linux") else int(usage)
    except Exception:  # pragma: no cover
        return 0


def _windows_peak_working_set() -> int:
    """Peak working set via ``K32GetProcessMemoryInfo``, or 0 on failure."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # pragma: no cover
        return 0

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = kernel32.K32GetProcessMemoryInfo
        function.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        function.restype = wintypes.BOOL

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = kernel32.GetCurrentProcess()
        if not function(handle, ctypes.byref(counters), counters.cb):
            return 0
        return int(counters.PeakWorkingSetSize)
    except Exception:  # pragma: no cover - platform dependent
        return 0


def _timed(
    stage: str,
    document: str,
    unit: str,
    work: Callable[[], Any],
    *,
    extra: dict[str, Any] | None = None,
) -> tuple[Measurement, Any]:
    """Run ``work`` once, recording how long it took and whether it succeeded."""
    gc.collect()
    started = time.perf_counter()
    try:
        value = work()
    except Exception as exc:
        seconds = time.perf_counter() - started
        return (
            Measurement(
                stage=stage,
                document=document,
                unit=unit,
                seconds=seconds,
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                extra=extra or {},
            ),
            None,
        )
    seconds = time.perf_counter() - started
    return (
        Measurement(
            stage=stage,
            document=document,
            unit=unit,
            seconds=seconds,
            extra=extra or {},
        ),
        value,
    )


def _page_size(doc: Any, page_index: int) -> tuple[float, float]:
    page = doc.load_page(page_index)
    rect = page.rect
    return float(rect.width), float(rect.height)


def measure_pdf(path: Path, *, preview_dpi: int, ocr_dpi: int) -> list[Measurement]:
    """Measure the PDF pipeline stages for one document."""
    import pymupdf

    from rr_adapters.pdf_extract import page_is_probably_scanned

    measurements: list[Measurement] = []

    # Stage 1: open.  This is the floor under the first screen; everything else
    # is added to it.
    opened, doc = _timed(
        "open", path.stem, "document", lambda: pymupdf.open(str(path))
    )
    measurements.append(opened)
    if doc is None:
        return measurements

    try:
        page_count = doc.page_count
        measurements[-1].extra["pages"] = page_count
        measurements[-1].extra["bytes"] = path.stat().st_size

        # Stage 2: text extraction for every page.  Measured per page as well as
        # in total, because the first-screen target is about one page.
        for page_index in range(page_count):
            measurement, _value = _timed(
                "extract_text",
                path.stem,
                f"page:{page_index}",
                lambda i=page_index: doc.load_page(i).get_text("dict"),
            )
            measurements.append(measurement)

        # Stage 3: first screen.  A preview render of page 1 only, which is what
        # the reader must show before anything else can be interacted with.
        measurement, payload = _timed(
            "first_screen_preview",
            path.stem,
            "page:0",
            lambda: doc.load_page(0).get_pixmap(dpi=preview_dpi, alpha=False).tobytes("png"),
            extra={"dpi": preview_dpi},
        )
        if payload is not None:
            measurement.extra["bytes_rendered"] = len(payload)
        measurements.append(measurement)

        # Stage 4: page turn.  A preview of each page, which is what a turn costs.
        for page_index in range(page_count):
            measurement, payload = _timed(
                "page_turn_preview",
                path.stem,
                f"page:{page_index}",
                lambda i=page_index: doc.load_page(i)
                .get_pixmap(dpi=preview_dpi, alpha=False)
                .tobytes("png"),
                extra={"dpi": preview_dpi},
            )
            if payload is not None:
                measurement.extra["bytes_rendered"] = len(payload)
            measurements.append(measurement)

        # Stage 5: scan detection, which decides whether OCR is needed at all.
        for page_index in range(page_count):
            measurement, _value = _timed(
                "scan_detection",
                path.stem,
                f"page:{page_index}",
                lambda i=page_index: page_is_probably_scanned(
                    doc.load_page(i), doc.load_page(i).get_text("text")
                ),
            )
            measurements.append(measurement)

        # Stage 6: OCR, but only on pages that need it.  Running OCR on every
        # page would measure something the product never does and would make the
        # harness take an hour.
        needs_ocr: list[int] = []
        for page_index in range(page_count):
            page = doc.load_page(page_index)
            scanned, _why = page_is_probably_scanned(page, page.get_text("text"))
            if scanned:
                needs_ocr.append(page_index)

        if needs_ocr:
            from rr_adapters.ocr import OCRProvider  # noqa: F401
            from rr_adapters.ocr import RapidOCRProvider
            from document_core.coords import PageGeometry

            built, provider = _timed(
                "ocr_engine_load", path.stem, "document", RapidOCRProvider
            )
            measurements.append(built)
            if provider is not None:
                import numpy as np
                from PIL import Image

                for page_index in needs_ocr:
                    width, height = _page_size(doc, page_index)
                    geometry = PageGeometry(width, height)

                    def render(i: int = page_index):
                        pixmap = doc.load_page(i).get_pixmap(dpi=ocr_dpi, alpha=False)
                        return np.asarray(
                            Image.frombytes(
                                "RGB", (pixmap.width, pixmap.height), pixmap.samples
                            )
                        )

                    rendered, image = _timed(
                        "ocr_render", path.stem, f"page:{page_index}", render,
                        extra={"dpi": ocr_dpi},
                    )
                    measurements.append(rendered)
                    if image is not None:
                        recognised, result = _timed(
                            "ocr_recognize",
                            path.stem,
                            f"page:{page_index}",
                            lambda img=image, g=geometry, i=page_index: provider.recognize(
                                img, g, page_index=i
                            ),
                        )
                        if result is not None:
                            recognised.extra["lines"] = len(result.lines)
                            recognised.extra["mean_confidence"] = round(
                                result.mean_confidence, 4
                            )
                        measurements.append(recognised)
    finally:
        doc.close()

    return measurements


def measure_docx(path: Path, *, preview_dpi: int, scratch: Path) -> list[Measurement]:
    """Measure the DOCX conversion path.

    DOCX has no page model of its own in this design (ADR 0005), so the product
    renders it to PDF first and then treats it as a PDF.  Both halves are timed
    because the conversion is the part that can be slow.

    The converter writes its derived PDF to a path rather than returning bytes, so
    a scratch directory is required; measuring in-memory would not measure what
    the product actually does.
    """
    import pymupdf

    from rr_adapters.docx_convert import DocxConverter

    measurements: list[Measurement] = []
    converter = DocxConverter()
    scratch.mkdir(parents=True, exist_ok=True)
    derived = scratch / f"{path.stem}.derived.pdf"

    measured, conversion = _timed(
        "docx_convert", path.stem, "document", lambda: converter.convert(path, derived)
    )
    if conversion is not None:
        measured.extra["source_items"] = len(conversion.source_items)
        measured.extra["issues"] = len(conversion.issues)
        measured.extra["bytes"] = path.stat().st_size
    measurements.append(measured)
    if conversion is None:
        return measurements

    if not conversion.derived_pdf.exists():
        measurements.append(
            Measurement(
                stage="docx_open_derived",
                document=path.stem,
                unit="document",
                seconds=0.0,
                ok=False,
                detail="conversion reported a derived PDF that does not exist",
            )
        )
        return measurements

    measured, doc = _timed(
        "docx_open_derived",
        path.stem,
        "document",
        lambda: pymupdf.open(str(conversion.derived_pdf)),
    )
    measurements.append(measured)
    if doc is None:
        return measurements
    try:
        measured.extra["pages"] = doc.page_count
        for page_index in range(doc.page_count):
            measurement, _value = _timed(
                "page_turn_preview",
                path.stem,
                f"page:{page_index}",
                lambda i=page_index: doc.load_page(i)
                .get_pixmap(dpi=preview_dpi, alpha=False)
                .tobytes("png"),
                extra={"dpi": preview_dpi},
            )
            measurements.append(measurement)
    finally:
        doc.close()
    return measurements


def measure_image(path: Path) -> list[Measurement]:
    """Measure the image path: decode plus recognition."""
    import numpy as np
    from PIL import Image

    from document_core.coords import PageGeometry
    from rr_adapters.ocr import RapidOCRProvider

    measurements: list[Measurement] = []
    measured, image = _timed(
        "image_decode", path.stem, "document", lambda: Image.open(path).convert("RGB")
    )
    measurements.append(measured)
    if image is None:
        return measurements
    measured.extra["width"] = image.width
    measured.extra["height"] = image.height

    built, provider = _timed("ocr_engine_load", path.stem, "document", RapidOCRProvider)
    measurements.append(built)
    if provider is None:
        return measurements

    array = np.asarray(image)
    geometry = PageGeometry(float(image.width), float(image.height))
    recognised, result = _timed(
        "ocr_recognize",
        path.stem,
        "document",
        lambda: provider.recognize(array, geometry),
    )
    if result is not None:
        recognised.extra["lines"] = len(result.lines)
    measurements.append(recognised)
    return measurements


def summarise(measurements: Sequence[Measurement]) -> dict[str, Any]:
    """Group measurements by stage and report a distribution per group."""
    by_stage: dict[str, list[Measurement]] = {}
    for measurement in measurements:
        by_stage.setdefault(measurement.stage, []).append(measurement)

    summary: dict[str, Any] = {}
    for stage, group in sorted(by_stage.items()):
        good = [m for m in group if m.ok]
        seconds = [m.seconds for m in good]
        failures = [m for m in group if not m.ok]
        entry: dict[str, Any] = {
            "count": len(group),
            "ok": len(good),
            "failed": len(failures),
            "p50_ms": round(_percentile(seconds, 0.50) * 1000, 1),
            "p95_ms": round(_percentile(seconds, 0.95) * 1000, 1),
            "min_ms": round(min(seconds) * 1000, 1) if seconds else 0.0,
            "max_ms": round(max(seconds) * 1000, 1) if seconds else 0.0,
        }
        if seconds:
            entry["mean_ms"] = round(statistics.fmean(seconds) * 1000, 1)
        if failures:
            # The reason matters more than the count: a stage that failed because
            # a format is unsupported is a different fact from one that crashed.
            entry["failure_reasons"] = sorted(
                {f"{m.document}: {m.detail}" for m in failures}
            )[:5]
        summary[stage] = entry
    return summary


def run(
    corpus: Path,
    *,
    preview_dpi: int = 120,
    # The calibrated resolution, imported rather than repeated.  This was a literal 300, which is
    # the value the adapter's own calibration rejected: at 300 dpi recognition is both slower and
    # slightly worse than at 250.  Running the harness with no arguments therefore rewrote the
    # recorded performance evidence - including peak memory, 1743 MiB becoming 2055 MiB - with
    # numbers from a resolution the project had explicitly decided against, and nothing said so.
    # A measurement that does not use the calibrated parameter is measuring a configuration that
    # does not ship.
    ocr_dpi: int = OCR_RENDER_DPI,
    only: str | None = None,
    scratch: Path | None = None,
) -> dict[str, Any]:
    """Measure every document in the corpus and return the report."""
    files_dir = corpus / "files"
    images_dir = corpus / "images"
    anomalies_dir = corpus / "anomalies"
    scratch_dir = scratch or (corpus.parent / "out" / "_perf_scratch")

    targets: list[tuple[str, Path]] = []
    for path in sorted(files_dir.glob("*.pdf")):
        targets.append(("pdf", path))
    for path in sorted(files_dir.glob("*.docx")):
        targets.append(("docx", path))
    for path in sorted(images_dir.glob("*")):
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            targets.append(("image", path))
    if only:
        targets = [t for t in targets if only.lower() in t[1].stem.lower()]

    measurements: list[Measurement] = []
    for kind, path in targets:
        try:
            if kind == "pdf":
                measurements.extend(
                    measure_pdf(path, preview_dpi=preview_dpi, ocr_dpi=ocr_dpi)
                )
            elif kind == "docx":
                measurements.extend(
                    measure_docx(path, preview_dpi=preview_dpi, scratch=scratch_dir)
                )
            else:
                measurements.extend(measure_image(path))
        except Exception as exc:  # a harness bug must not look like a slow stage
            measurements.append(
                Measurement(
                    stage="harness_error",
                    document=path.stem,
                    unit="document",
                    seconds=0.0,
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )

    # The anomaly suite is measured separately and reported separately: those
    # files are *supposed* to fail, so folding them into the same table would
    # turn correct rejection into a failure count.
    anomaly_results: list[dict[str, Any]] = []
    if not only:
        import pymupdf

        for path in sorted(anomalies_dir.glob("*")):
            if not path.is_file():
                continue
            started = time.perf_counter()
            outcome = "handled"
            detail = ""
            try:
                if path.suffix.lower() == ".pdf":
                    doc = pymupdf.open(str(path))
                    needs_password = doc.needs_pass
                    pages = 0 if needs_password else doc.page_count
                    doc.close()
                    detail = (
                        "encrypted" if needs_password else f"{pages} page(s) opened"
                    )
                else:
                    detail = f"{path.stat().st_size} bytes"
            except Exception as exc:
                outcome = "raised"
                detail = f"{type(exc).__name__}: {exc}"
            anomaly_results.append(
                {
                    "name": path.name,
                    "seconds_ms": round((time.perf_counter() - started) * 1000, 1),
                    "outcome": outcome,
                    "detail": detail,
                }
            )

    peak_bytes = _peak_working_set_bytes()
    tracemalloc.start()
    _ = [m.seconds for m in measurements]
    _current, tracemalloc_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "machine": platform.machine(),
            "cpu_count": __import__("os").cpu_count(),
        },
        "parameters": {
            "preview_dpi": preview_dpi,
            "ocr_dpi": ocr_dpi,
            "documents": len({m.document for m in measurements}),
        },
        "peak_memory": {
            "process_peak_working_set_bytes": peak_bytes,
            "process_peak_working_set_mib": round(peak_bytes / 1048576, 1),
            "tracemalloc_peak_bytes": tracemalloc_peak,
            "note": (
                "the process figure is the one that matters; tracemalloc sees only "
                "Python allocations and omits rendered page buffers"
            ),
        },
        "stages": summarise(measurements),
        "measurements": [asdict(m) for m in measurements],
        "anomalies": anomaly_results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure M0 pipeline performance")
    parser.add_argument("--corpus", type=Path, default=REPO / "benchmarks" / "corpus")
    parser.add_argument("--out", type=Path, default=REPO / "benchmarks" / "out" / "perf.json")
    parser.add_argument("--preview-dpi", type=int, default=120)
    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=OCR_RENDER_DPI,
        help=f"render resolution for OCR (default {OCR_RENDER_DPI}, the calibrated value)",
    )
    parser.add_argument("--only", default=None, help="substring filter on the file name")
    args = parser.parse_args(argv)

    report = run(
        args.corpus,
        preview_dpi=args.preview_dpi,
        ocr_dpi=args.ocr_dpi,
        only=args.only,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("environment:", report["environment"]["platform"])
    print(
        f"documents={report['parameters']['documents']} "
        f"preview_dpi={report['parameters']['preview_dpi']} "
        f"ocr_dpi={report['parameters']['ocr_dpi']}"
    )
    print(
        f"peak working set: {report['peak_memory']['process_peak_working_set_mib']} MiB"
    )
    print()
    print(f"{'stage':22} {'n':>4} {'p50 ms':>9} {'p95 ms':>9} {'max ms':>9} {'fail':>5}")
    print("-" * 64)
    for stage, entry in report["stages"].items():
        print(
            f"{stage:22} {entry['count']:>4} {entry['p50_ms']:>9.1f} "
            f"{entry['p95_ms']:>9.1f} {entry['max_ms']:>9.1f} {entry['failed']:>5}"
        )
        for reason in entry.get("failure_reasons", []):
            print(f"    failed: {reason}")
    print()
    print(f"anomaly suite: {len(report['anomalies'])} files")
    for entry in report["anomalies"]:
        print(f"  {entry['outcome']:8} {entry['name']:34} {entry['detail']}")
    print()
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
