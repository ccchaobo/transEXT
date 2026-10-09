"""Characterise the residual OCR errors on an ideal (undegraded) render.

Every degradation factor produced the same ~4-7% error rate, which means the
residual is the engine's own behaviour rather than image quality.  This prints
the actual error breakdown: which characters are substituted, inserted and
deleted, and how many of each, so the cause can be identified instead of
speculated about.
"""

from __future__ import annotations

import collections
import difflib
import json
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

CORPUS = Path("benchmarks/corpus")

try:
    from scripts.spike_ocr import _normalise
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise


def main() -> int:
    truth = json.loads((CORPUS / "truth" / "pdf_a_single_column.json").read_text(encoding="utf-8"))
    page = next(p for p in truth["pages"] if p["index"] == 0)
    reference = _normalise("\n".join(b["text"] for b in page["blocks"]))

    doc = pymupdf.open(str(CORPUS / "files" / "pdf_a_single_column.pdf"))
    pixmap = doc.load_page(0).get_pixmap(dpi=200, alpha=False)
    doc.close()
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

    engine = RapidOCR()
    result, _elapsed = engine(np.asarray(image))
    recognised = _normalise("\n".join(text for _box, text, _score in result))

    print(f"reference chars: {len(reference)}")
    print(f"recognised chars: {len(recognised)}")
    print()

    matcher = difflib.SequenceMatcher(None, reference, recognised, autojunk=False)
    substitutions: collections.Counter = collections.Counter()
    insertions: collections.Counter = collections.Counter()
    deletions: collections.Counter = collections.Counter()
    blocks: list[tuple[str, str, str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        ref_part = reference[i1:i2]
        ocr_part = recognised[j1:j2]
        if tag == "replace":
            # Pair up character by character for the substitution tally.
            for a, b in zip(ref_part, ocr_part):
                substitutions[(a, b)] += 1
            if len(ref_part) != len(ocr_part):
                blocks.append((tag, ref_part, ocr_part, _context(reference, i1)))
        elif tag == "insert":
            insertions[ocr_part] += 1
            blocks.append((tag, ref_part, ocr_part, _context(reference, i1)))
        else:
            deletions[ref_part] += 1
            blocks.append((tag, ref_part, ocr_part, _context(reference, i1)))

    total_errors = sum(substitutions.values()) + sum(insertions.values()) + sum(deletions.values())
    print(f"total error operations: {total_errors}")
    print(f"  substitutions: {sum(substitutions.values())}")
    print(f"  insertions:    {sum(insertions.values())}")
    print(f"  deletions:     {sum(deletions.values())}")
    print(f"  CER: {total_errors / len(reference):.4f}")
    print()

    print("most common substitutions (reference char -> recognised char):")
    for (a, b), count in substitutions.most_common(15):
        print(f"  {a!r} -> {b!r}  x{count}")
    print()

    print("insertions (text recognised but not in the reference):")
    for text, count in insertions.most_common(12):
        print(f"  {text!r} x{count}")
    print()

    print("deletions (reference text not recognised):")
    for text, count in deletions.most_common(12):
        print(f"  {text!r} x{count}")
    print()

    print("error contexts (first 25 non-equal regions):")
    for tag, ref_part, ocr_part, context in blocks[:25]:
        print(f"  {tag:8} ref={ref_part[:40]!r}")
        print(f"           ocr={ocr_part[:40]!r}")
        print(f"           ...{context}...")
    return 0


def _context(text: str, index: int, width: int = 34) -> str:
    start = max(0, index - width)
    return text[start : index + width]


if __name__ == "__main__":
    raise SystemExit(main())
