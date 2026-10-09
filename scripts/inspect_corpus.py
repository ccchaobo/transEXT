"""Inspect the generated corpus: file shapes and ground-truth sanity.

Run: ``python scripts/inspect_corpus.py --root benchmarks/corpus``
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf
from PIL import Image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="benchmarks/corpus")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    root = Path(args.root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    print(f"{'name':34} {'kind':12} {'layout':16} {'pg':>3} {'size':>9}  detail")
    print("-" * 110)
    for entry in manifest["entries"][: args.limit]:
        path = root / entry["path"]
        detail = ""
        if path.suffix.lower() == ".pdf":
            try:
                doc = pymupdf.open(str(path))
                chars = 0
                images = 0
                for index in range(doc.page_count):
                    page = doc.load_page(index)
                    chars += len(page.get_text())
                    images += len(page.get_images(full=True))
                rotations = [doc.load_page(i).rotation for i in range(doc.page_count)]
                detail = f"chars={chars} images={images} rot={rotations}"
                doc.close()
            except Exception as exc:  # expected for the corrupt-file anomaly
                detail = f"unreadable: {type(exc).__name__} (expected for this anomaly)"
        elif path.suffix.lower() in (".png", ".jpg", ".jpeg"):
            with Image.open(path) as image:
                detail = f"{image.size[0]}x{image.size[1]} {image.mode}"
        elif path.suffix.lower() == ".docx":
            import zipfile

            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
            detail = f"parts={len(names)} has_footnotes={'word/footnotes.xml' in names}"
        print(
            f"{entry['name']:34} {entry['kind']:12} {entry['layout']:16} "
            f"{entry['page_count']:>3} {entry['size_bytes'] / 1024:>8.1f}K  {detail}"
        )

    print()
    counts = manifest["counts"]
    print("counts:", counts)
    normal = sum(v for k, v in counts.items() if k != "anomaly")
    print(f"normal samples: {normal} (spec 12.1 requires >= 24)")
    assert normal >= 24, "corpus is below the required 24 normal samples"
    holdout = json.loads((root / "holdout.json").read_text(encoding="utf-8"))
    print(f"development: {len(holdout['development'])}  holdout: {len(holdout['holdout'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
