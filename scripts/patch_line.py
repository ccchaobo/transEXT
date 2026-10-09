"""Replace a line in a UTF-8 file, addressing it by line number.

This exists because PowerShell's ``Get-Content``/``Set-Content`` round-trip
decodes UTF-8 as the console code page (cp936 on this machine) and silently
destroys every non-ASCII character, and because ``Set-Content -Encoding utf8``
adds a UTF-8 BOM that breaks Python source files.  Both facts are handled here:
the replacement travels as ASCII ``\\uXXXX`` escapes, flags are stripped from the
argument list, and a byte-order mark is detected and preserved rather than
introduced.

Usage::

    python scripts/patch_line.py <file> <line-number> "<text with \\uXXXX escapes>" [--force]

The replacement may be passed as several arguments if the shell splits it; they
are rejoined with single spaces.  ``--force`` (or ``-f``) permits replacing a
line that does not look corrupted.
"""

from __future__ import annotations

import codecs
import sys
from pathlib import Path

#: Characters that only appear when UTF-8 text has been decoded as cp936.
#: Their presence is what makes a line a repair candidate.
_MOJIBAKE_CHARS = "\u9225\u951b\u9286\u9225\u93c2\u9428"
_FLAGS = {"--force", "-f"}


def _looks_corrupted(line: str) -> bool:
    if any(char in line for char in _MOJIBAKE_CHARS):
        return True
    # Unpaired surrogates survive a lossy cp936 decode and are never valid here.
    return any(0xD800 <= ord(char) <= 0xDFFF for char in line)


def read_text_preserving_bom(path: Path) -> tuple[str, bool]:
    """Return ``(text, had_bom)``, decoding UTF-8 with or without a BOM."""
    data = path.read_bytes()
    had_bom = data.startswith(codecs.BOM_UTF8)
    if had_bom:
        data = data[len(codecs.BOM_UTF8) :]
    return data.decode("utf-8"), had_bom


def write_text_preserving_bom(path: Path, text: str, had_bom: bool) -> None:
    data = text.encode("utf-8")
    path.write_bytes(codecs.BOM_UTF8 + data if had_bom else data)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    path = Path(argv[1])
    line_number = int(argv[2])
    force = any(arg in _FLAGS for arg in argv[3:])
    payload = [arg for arg in argv[3:] if arg not in _FLAGS]
    raw = " ".join(payload)
    # The argument arrives as ASCII containing ``\uXXXX`` escapes; decode it to
    # real characters before writing.
    replacement = raw.encode("ascii", "backslashreplace").decode("unicode_escape")

    original, had_bom = read_text_preserving_bom(path)
    lines = original.splitlines(keepends=True)
    if not 1 <= line_number <= len(lines):
        print(f"line {line_number} out of range (file has {len(lines)} lines)")
        return 1
    current = lines[line_number - 1]
    if replacement == current.rstrip("\n"):
        print("already correct; nothing to do")
        return 0
    if not _looks_corrupted(current) and not force:
        print(
            f"refusing to patch line {line_number}: it does not look corrupted.\n"
            f"  {current!r}\n"
            "Pass --force to override."
        )
        return 1
    print(f"was: {current!r}")
    ending = "\n" if current.endswith("\n") else ""
    lines[line_number - 1] = replacement + ending
    write_text_preserving_bom(path, "".join(lines), had_bom)
    print(f"now: {lines[line_number - 1]!r}")
    print(f"bom preserved: {had_bom}")

    # Prove the file is still valid UTF-8 with the intended content and no BOM.
    check, check_bom = read_text_preserving_bom(path)
    print(f"replacement present: {replacement in check}")
    print(f"bom state unchanged: {check_bom == had_bom}")
    if check.startswith("\ufeff"):
        print("WARNING: a BOM is present at the start of the text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
