"""Find and optionally strip UTF-8 BOMs from Python source files.

The BOM matters here because the file sandbox runs under cp936 and PowerShell's
``Set-Content -Encoding utf8`` writes one.  Python 3 rejects a BOM in a module
that is imported as a package member only in some paths, and rejects it outright
for files executed directly, so a stray BOM is a real defect rather than a
cosmetic one.

Run::

    python scripts/fix_bom.py            # report only
    python scripts/fix_bom.py --strip    # remove the BOMs found
"""

from __future__ import annotations

import argparse
import codecs
from pathlib import Path

SKIP_PARTS = {".git", "__pycache__", ".venv", "node_modules", "corpus", "vendor"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--strip", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    with_bom: list[Path] = []
    invalid: list[Path] = []
    checked = 0
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        checked += 1
        data = path.read_bytes()
        if data.startswith(codecs.BOM_UTF8):
            with_bom.append(path)
            if args.strip:
                path.write_bytes(data[len(codecs.BOM_UTF8) :])
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                invalid.append(path)
                print(f"INVALID UTF-8: {path}: {exc}")

    print(f"checked {checked} Python files")
    for path in with_bom:
        state = "stripped" if args.strip else "present"
        print(f"BOM {state}: {path}")
    if not with_bom:
        print("no BOM found")
    if invalid:
        print(f"{len(invalid)} file(s) are not valid UTF-8")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
