"""Choose and verify a CJK-capable font for the corpus renderer.

Two properties are required, and both are checked here rather than assumed:

1. ReportLab must be able to register and draw with the font.
2. The resulting PDF must carry a text layer that extracts back to the *same*
   characters, because the corpus ground truth is compared against extracted
   text.  A font that draws correctly but has no usable ToUnicode mapping would
   silently break the coverage metric.

Run: ``python scripts/choose_font.py``
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

PROBE = "中英文混排文档的阅读顺序恢复 Order accuracy 96.4%"
CANDIDATES = [
    ("NotoSansSC-VF.ttf", "Noto Sans SC Variable"),
    ("simhei.ttf", "SimHei"),
    ("simkai.ttf", "SimKai"),
    ("Deng.ttf", "DengXian"),
    ("ARIALUNI.TTF", "Arial Unicode MS"),
    ("simsun.ttc", "SimSun (TTC)"),
    ("msyh.ttc", "Microsoft YaHei (TTC)"),
]


def probe(font_path: Path, name: str) -> tuple[bool, bool, str]:
    """Return (registered, extract_roundtrip_ok, detail)."""
    try:
        pdfmetrics.registerFont(TTFont(name, str(font_path)))
    except Exception as exc:
        return False, False, f"register failed: {type(exc).__name__}: {exc}"
    try:
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(595, 842))
        c.setFont(name, 14)
        c.drawString(60, 700, PROBE)
        c.showPage()
        c.save()
        doc = pymupdf.open("pdf", buffer.getvalue())
        page = doc.load_page(0)
        extracted = page.get_text().strip()
        # Also test the per-character direction: fonts without a proper
        # ToUnicode map often extract as replacement characters.
        doc.close()
    except Exception as exc:
        return True, False, f"draw/extract failed: {type(exc).__name__}: {exc}"
    ok = PROBE == extracted
    detail = f"extracted={extracted[:48]!r}"
    if not ok:
        # Try a looser check: are the CJK characters present at all?
        present = sum(1 for ch in "中英文混排文档" if ch in extracted)
        detail += f" cjk_chars_present={present}/7"
    return True, ok, detail


def main() -> int:
    fonts_dir = Path("C:/Windows/Fonts")
    print(f"{'font':26} {'kind':10} {'register':9} {'roundtrip':10} detail")
    print("-" * 110)
    best: list[tuple[str, Path]] = []
    for filename, name in CANDIDATES:
        path = fonts_dir / filename
        if not path.exists():
            print(f"{filename:26} {'-':10} {'missing':9} {'-':10} not installed")
            continue
        kind = "TTC" if path.suffix.lower() == ".ttc" else "TTF"
        registered, roundtrip, detail = probe(path, name)
        print(
            f"{filename:26} {kind:10} {str(registered):9} {str(roundtrip):10} {detail}"
        )
        if registered and roundtrip:
            best.append((name, path))
    print()
    if best:
        name, path = best[0]
        print(f"RECOMMENDED: {name} at {path}")
        print("Both registration and text extraction round-trip succeeded.")
        return 0
    print("NO FONT passed both checks; a different rendering strategy is required.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
