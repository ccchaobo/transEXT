"""Print an exact, reproducible inventory of the development environment.

The inventory exists to make two claims checkable rather than asserted:

* every measured figure in the evidence tables belongs to a specific set of
  versions, so a number can be reproduced or shown to be stale;
* the licence of every dependency is recorded, because at least one of them
  (PyMuPDF) carries an obligation on distribution that must not be discovered
  after the fact.

Licence detection is deliberately layered. ``License`` metadata is frequently a
full licence text rather than an identifier, and many modern wheels omit it
entirely, so falling back to the trove classifier and then to the bundled licence
files is the difference between a real inventory and a column of "UNKNOWN".
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Sequence

TRACKED = [
    "fastapi",
    "uvicorn",
    "starlette",
    "pydantic",
    "sqlalchemy",
    "aiosqlite",
    "httpx",
    "python-multipart",
    "pypdf",
    "PyMuPDF",
    "Pillow",
    "python-docx",
    "numpy",
    "reportlab",
    "pytest",
    "pytest-asyncio",
    "rapidocr-onnxruntime",
    "onnxruntime",
    "opencv-python",
    "pyclipper",
    "shapely",
    "six",
    "PyYAML",
]

#: Licences that impose an obligation worth flagging in the inventory.
#:
#: The AGPL pattern also matches the spelled-out form.  Metadata says "GNU AFFERO
#: GPL 3.0" rather than "AGPL", so matching only the acronym silently downgraded
#: the strongest obligation in the inventory to the generic copyleft note.
OBLIGATION_PATTERNS = (
    (
        re.compile(r"agpl|affero", re.IGNORECASE),
        "strong copyleft: network use triggers distribution obligations",
    ),
    (re.compile(r"\bgpl\b", re.IGNORECASE), "copyleft"),
    (re.compile(r"commercial", re.IGNORECASE), "dual licence: a commercial option exists"),
)

#: Known licence identifiers for packages whose metadata omits one.  Recorded
#: explicitly rather than guessed, and each is checkable against the linked
#: project.
KNOWN_LICENSES = {
    "fastapi": "MIT",
    "uvicorn": "BSD-3-Clause",
    "starlette": "BSD-3-Clause",
    "pydantic": "MIT",
    "sqlalchemy": "MIT",
    "aiosqlite": "MIT",
    "httpx": "BSD-3-Clause",
    "python-multipart": "Apache-2.0",
    "pypdf": "BSD-3-Clause",
    "PyMuPDF": "AGPL-3.0-or-later OR Artifex Commercial",
    "Pillow": "MIT-CMU",
    "python-docx": "MIT",
    "numpy": "BSD-3-Clause",
    "reportlab": "BSD-3-Clause",
    "pytest": "MIT",
    "pytest-asyncio": "Apache-2.0",
    "rapidocr-onnxruntime": "Apache-2.0",
    "onnxruntime": "MIT",
    "opencv-python": "Apache-2.0",
    "pyclipper": "MIT",
    "shapely": "BSD-3-Clause",
    "six": "MIT",
    "PyYAML": "MIT",
}


def _license_from_metadata(dist: metadata.Distribution) -> str:
    """The short licence identifier from metadata, if one is present.

    A long ``License`` field is almost always the licence *text*; taking the first
    line of it gives something reportable, whereas rejecting it because it is long
    (as an earlier version did) discarded the information entirely.
    """
    meta = dist.metadata
    classifiers = meta.get_all("Classifier") or []
    for classifier in classifiers:
        if classifier.startswith("License ::"):
            parts = [part.strip() for part in classifier.split("::")]
            if len(parts) >= 2 and parts[-1]:
                return parts[-1]
    license_field = meta.get("License")
    if license_field:
        text = license_field.strip()
        if text:
            if len(text) <= 120:
                return text
            # Long text: the first non-empty line is usually a name or a heading.
            for line in text.splitlines():
                line = line.strip()
                if line and len(line) <= 120:
                    return line
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()
    return "UNKNOWN"


def _obligations(license_text: str) -> list[str]:
    """Obligations implied by a licence string."""
    found: list[str] = []
    for pattern, note in OBLIGATION_PATTERNS:
        if pattern.search(license_text):
            found.append(note)
    return found


def _resolve_license(name: str, dist: metadata.Distribution) -> tuple[str, str]:
    """Return ``(licence, provenance)`` for a distribution."""
    detected = _license_from_metadata(dist)
    if detected != "UNKNOWN":
        return detected, "package metadata"
    known = KNOWN_LICENSES.get(name)
    if known:
        return known, "recorded in this script; verify against the project URL"
    return "UNKNOWN", "not determinable from metadata"


def build_inventory() -> dict[str, object]:
    packages: dict[str, dict[str, object]] = {}
    for name in TRACKED:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            packages[name] = {
                "version": "NOT INSTALLED",
                "license": "",
                "license_source": "",
                "obligations": [],
                "home": "",
            }
            continue
        license_text, source = _resolve_license(name, dist)
        packages[name] = {
            "version": dist.version,
            "license": license_text,
            "license_source": source,
            "obligations": _obligations(license_text),
            "home": dist.metadata.get("Home-page")
            or dist.metadata.get("Project-URL")
            or "",
        }
    return {
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "packages": packages,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory the environment")
    parser.add_argument("--out", type=Path, default=None, help="write JSON here")
    parser.add_argument("--quiet", action="store_true", help="do not print the JSON")
    args = parser.parse_args(argv)

    info = build_inventory()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if not args.quiet:
        print(json.dumps(info, indent=2, ensure_ascii=False))

    packages = info["packages"]
    assert isinstance(packages, dict)
    unknown = [name for name, entry in packages.items() if entry["license"] == "UNKNOWN"]
    print(
        f"\n{len(packages)} tracked packages; "
        f"{len(unknown)} with an undetermined licence",
        file=sys.stderr,
    )
    for name in unknown:
        print(f"  undetermined: {name}", file=sys.stderr)

    flagged = [
        (name, entry["license"])
        for name, entry in packages.items()
        if entry["obligations"]
    ]
    if flagged:
        print("\nlicences carrying an obligation:", file=sys.stderr)
        for name, license_text in flagged:
            print(f"  {name}: {license_text}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
