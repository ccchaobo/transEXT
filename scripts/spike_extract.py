"""Spike: exercise the PDF extractor and DOCX converter on corpus files.

Run: ``python scripts/spike_extract.py``
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from rr_adapters.docx_convert import DecompressionBomb, DocxConverter
from rr_adapters.pdf_extract import PdfTextExtractor, is_encrypted

CORPUS = Path("benchmarks/corpus")
FILES = CORPUS / "files"
ANOMALIES = CORPUS / "anomalies"


def pdf_report(name: str, extractor: PdfTextExtractor) -> dict:
    path = FILES / f"{name}.pdf"
    started = time.perf_counter()
    result = extractor.extract(path)
    elapsed = time.perf_counter() - started
    lines = sum(len(v) for v in result.lines_by_page.values())
    print(f"\n=== {name} ({elapsed * 1000:.0f} ms, {result.page_count} pages, {lines} lines) ===")
    for info in result.page_infos:
        print(
            f"  page {info.index}: {info.width_pt:.0f}x{info.height_pt:.0f} rot={info.rotation} "
            f"chars={info.text_chars} garbage={info.garbage_ratio:.3f} "
            f"images={info.image_count} cov={info.image_coverage:.2f} "
            f"usable={info.text_usable} scanned={info.likely_scanned} {info.reasons}"
        )
    # Show the first lines of page 0 with their boxes, to sanity-check the
    # top-left origin contract: the title must have a small y0.
    for line in sorted(result.lines_by_page.get(0, []), key=lambda ln: ln.bbox.y0)[:6]:
        print(
            f"    y0={line.bbox.y0:.3f} x0={line.bbox.x0:.3f} "
            f"size={line.font_size} bold={line.bold} {line.text[:64]!r}"
        )
    return {
        "name": name,
        "pages": result.page_count,
        "lines": lines,
        "ms": round(elapsed * 1000, 1),
        "scanned_pages": [i.index for i in result.page_infos if i.likely_scanned],
    }


def docx_report(name: str, converter: DocxConverter, tmp: Path) -> dict:
    path = FILES / f"{name}.docx"
    started = time.perf_counter()
    conversion = converter.convert(path, tmp / f"{name}.pdf")
    elapsed = time.perf_counter() - started
    kinds: dict[str, int] = {}
    for item in conversion.source_items:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
    print(f"\n=== {name} ({elapsed * 1000:.0f} ms) ===")
    print(f"  derived pages: {conversion.page_count}, items: {len(conversion.source_items)}, {kinds}")
    print(f"  stats: {json.dumps(conversion.stats, ensure_ascii=False)[:220]}")
    print(f"  issues: {len(conversion.issues)}")
    for issue in conversion.issues[:4]:
        print(f"    [{issue['severity']}] {issue['code']}: {issue['message'][:96]}")
    first = [i for i in conversion.source_items if i.kind in ("heading", "paragraph")][:2]
    for item in first:
        print(f"    {item.kind} level={item.level} style={item.style!r} {item.text[:70]!r}")
    return {
        "name": name,
        "pages": conversion.page_count,
        "items": len(conversion.source_items),
        "kinds": kinds,
        "ms": round(elapsed * 1000, 1),
        "issues": len(conversion.issues),
    }


def main() -> int:
    extractor = PdfTextExtractor()
    converter = DocxConverter()
    summary: list[dict] = []

    for name in (
        "pdf_a_single_column",
        "pdf_b_two_column",
        "pdf_c_formula_footnote",
        "pdf_d_mixed_language",
        "pdf_f_two_column_wide_table",
    ):
        summary.append(pdf_report(name, extractor))

    for name in ("scan_a_clean_300dpi", "scan_b_two_column_noisy", "mixed_f_hybrid"):
        summary.append(pdf_report(name, extractor))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for name in ("docx_paper_j", "docx_paper_c_math", "docx_paper_d_mixed"):
            summary.append(docx_report(name, converter, tmp))

    # --- anomaly handling -------------------------------------------------
    print("\n=== anomaly checks ===")
    checks: list[tuple[str, bool, str]] = []

    encrypted = ANOMALIES / "encrypted.pdf"
    try:
        encrypted_flag = is_encrypted(encrypted)
    except Exception as exc:
        encrypted_flag = False
        print("  encrypted detection raised", type(exc).__name__)
    checks.append(("encrypted detected", encrypted_flag, "is_encrypted -> True"))
    print(f"  is_encrypted(encrypted.pdf) = {encrypted_flag}")

    truncated = ANOMALIES / "corrupt_truncated.pdf"
    try:
        result = extractor.extract(truncated)
        detail = f"opened with {result.page_count} pages, {sum(len(v) for v in result.lines_by_page.values())} lines"
        checks.append(("truncated handled without crash", True, detail))
    except Exception as exc:
        checks.append(("truncated handled without crash", True, f"{type(exc).__name__}: {exc}"))
    print(f"  truncated pdf -> {checks[-1][2]}")

    blank = ANOMALIES / "blank_pages.pdf"
    result = extractor.extract(blank)
    all_blank = all(info.likely_scanned for info in result.page_infos)
    checks.append(("blank pages flagged", all_blank, f"{result.page_count} pages, all flagged as no-text"))
    print(f"  blank_pages.pdf -> all pages flagged as needing OCR: {all_blank}")

    garbage = ANOMALIES / "garbage_text_layer.pdf"
    result = extractor.extract(garbage)
    flagged = result.page_infos[0].likely_scanned
    checks.append(
        (
            "garbage text layer distrusted",
            flagged,
            f"text_chars={result.page_infos[0].text_chars} garbage={result.page_infos[0].garbage_ratio:.2f} "
            f"reasons={result.page_infos[0].reasons}",
        )
    )
    print(f"  garbage_text_layer.pdf -> flagged: {flagged} reasons={result.page_infos[0].reasons}")

    bomb = ANOMALIES / "docx_zip_bomb.docx"
    try:
        converter.convert(bomb, Path(tempfile.gettempdir()) / "bomb.pdf")
        checks.append(("zip bomb rejected", False, "conversion unexpectedly succeeded"))
        print("  zip bomb -> NOT rejected (bad)")
    except DecompressionBomb as exc:
        checks.append(("zip bomb rejected", True, str(exc)))
        print(f"  zip bomb -> rejected: {exc}")

    print("\n=== summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    failures = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
