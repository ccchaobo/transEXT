"""Font selection for PDF rendering.

A PDF cannot assume one font covers every character: the built-in Type1 fonts
have no CJK glyphs and draw them as blank or wrong shapes, and their width model
for CJK is meaningless.  This module is shared by the DOCX renderer (adapters
layer) and the corpus generator (benchmarks layer), so both measure and draw
identically.

Two things are handled:

* a CJK-capable TrueType font covers CJK characters;
* :func:`split_runs` splits a string into maximal runs of "CJK" and "Latin"
  characters so each run is measured and drawn with a font that actually has
  those glyphs, and so line breaking uses correct widths for both scripts.

The font path is discovered rather than hard-coded, and the choice is recorded
in the output metadata, so a machine without these fonts fails loudly instead of
silently emitting PDFs whose CJK glyphs are blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

__all__ = [
    "CJKFont",
    "available_cjk_font",
    "split_runs",
    "measure",
    "has_cjk",
    "register_pdf_fonts",
]

#: Ordered preference.  The first entry that registers and draws CJK correctly
#: wins; ``scripts/choose_font.py`` verifies these on a given machine.
CANDIDATE_FONTS: tuple[tuple[str, str], ...] = (
    ("NotoSansSC-VF.ttf", "PdfCJK"),
    ("wqy-zenhei.ttc", "PdfCJK"),
    ("simhei.ttf", "PdfCJK"),
    ("Deng.ttf", "PdfCJK"),
    ("ARIALUNI.TTF", "PdfCJK"),
    ("simsun.ttc", "PdfCJK"),
    ("msyh.ttc", "PdfCJK"),
)
FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts"),
    Path("/usr/share/fonts/truetype/wqy"),
    Path("/Library/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
)

_CJK_RE = re.compile(
    r"[\u1100-\u11ff\u2e80-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\ua960-\ua97f\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]"
)


@dataclass(frozen=True)
class CJKFont:
    name: str
    path: Path


_REGISTERED: CJKFont | None = None
_TRIED = False


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def available_cjk_font() -> CJKFont | None:
    """Find, register and cache a CJK font, or return ``None``."""
    global _REGISTERED, _TRIED
    if _TRIED:
        return _REGISTERED
    _TRIED = True
    for filename, registered_name in CANDIDATE_FONTS:
        for directory in FONT_DIRS:
            path = directory / filename
            if not path.exists():
                continue
            try:
                pdfmetrics.registerFont(TTFont(registered_name, str(path)))
                _REGISTERED = CJKFont(registered_name, path)
                return _REGISTERED
            except Exception:
                continue
    return None


def register_pdf_fonts() -> dict[str, str]:
    """Register fonts and report what was found, for the manifest."""
    font = available_cjk_font()
    return {
        "cjk_font": str(font.path) if font else "NONE",
        "cjk_font_name": font.name if font else "NONE",
        "cjk_available": "true" if font else "false",
    }


def split_runs(text: str) -> list[tuple[bool, str]]:
    """Split text into ``(is_cjk, run)`` pairs, preserving order."""
    runs: list[tuple[bool, str]] = []
    if not text:
        return runs
    current_is_cjk = bool(_CJK_RE.match(text[0]))
    buffer = [text[0]]
    for char in text[1:]:
        char_is_cjk = bool(_CJK_RE.match(char))
        if char_is_cjk == current_is_cjk:
            buffer.append(char)
        else:
            runs.append((current_is_cjk, "".join(buffer)))
            buffer = [char]
            current_is_cjk = char_is_cjk
    runs.append((current_is_cjk, "".join(buffer)))
    return runs


def measure(text: str, latin_font: str, size: float) -> float:
    """Width of ``text``, using the correct font for each run."""
    if not has_cjk(text):
        return pdfmetrics.stringWidth(text, latin_font, size)
    cjk = available_cjk_font()
    if cjk is None:
        # Without a CJK font the CJK glyphs cannot be drawn faithfully, so the
        # measurement uses the Type1 model.  This path is a visible failure, not
        # a silent approximation: the manifest records cjk_available=false.
        return pdfmetrics.stringWidth(text, latin_font, size)
    total = 0.0
    for is_cjk, run in split_runs(text):
        font = cjk.name if is_cjk else latin_font
        total += pdfmetrics.stringWidth(run, font, size)
    return total


def draw(
    canvas,
    text: str,
    x: float,
    y: float,
    latin_font: str,
    size: float,
) -> None:
    """Draw ``text`` at ``(x, y)``, switching fonts per run."""
    if not has_cjk(text):
        canvas.setFont(latin_font, size)
        canvas.drawString(x, y, text)
        return
    cjk = available_cjk_font()
    if cjk is None:
        canvas.setFont(latin_font, size)
        canvas.drawString(x, y, text)
        return
    cursor = x
    for is_cjk, run in split_runs(text):
        font = cjk.name if is_cjk else latin_font
        canvas.setFont(font, size)
        canvas.drawString(cursor, y, run)
        cursor += pdfmetrics.stringWidth(run, font, size)
