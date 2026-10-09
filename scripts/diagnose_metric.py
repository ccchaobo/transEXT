"""Compute the same metric two ways on one identical input, and compare.

Two harness scripts reported character error rates of 0.0005 and 0.0410 for the
same document, page and engine run.  The engine was separately shown to be
deterministic, so the fault is in the measurement.  This script removes every
difference between the two paths by computing both metrics from one pair of
strings and printing the inputs' fingerprints.
"""

from __future__ import annotations

import collections
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

CORPUS = Path("benchmarks/corpus")


def normalise_strict(text: str) -> str:
    """The version used by scripts/spike_ocr.py."""
    text = text.replace("\ufffd", "")
    text = re.sub(r"\s+", "", text)
    text = text.lower()
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def normalise_loose(text: str) -> str:
    """A version that keeps word characters and CJK but drops only whitespace."""
    text = text.replace("\ufffd", "")
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    document = "pdf_a_single_column"
    page_index = 0
    truth = json.loads((CORPUS / "truth" / f"{document}.json").read_text(encoding="utf-8"))
    page = next(p for p in truth["pages"] if p["index"] == page_index)

    # Both plausible reference constructions.
    reference_blocks = page.get("visible_text") or "\n".join(
        b["text"] for b in page["blocks"]
    )
    reference_all_pages = " ".join(
        p.get("visible_text") or "" for p in truth["pages"]
    )
    print("reference candidates:")
    print(f"  page {page_index} blocks: {len(reference_blocks)} chars, fp={fingerprint(reference_blocks)}")
    print(f"  all pages            : {len(reference_all_pages)} chars, fp={fingerprint(reference_all_pages)}")
    print()

    doc = pymupdf.open(str(CORPUS / "files" / f"{document}.pdf"))
    pixmap = doc.load_page(page_index).get_pixmap(dpi=200, alpha=False)
    doc.close()
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

    engine = RapidOCR()
    result, _elapsed = engine(np.asarray(image))
    lines = [text for _box, text, _score in result]
    recognised = "\n".join(lines)
    print(f"recognised: {len(recognised)} chars, fp={fingerprint(recognised)}")
    print()

    for label, reference in (
        ("page-0 blocks", reference_blocks),
        ("all pages", reference_all_pages),
    ):
        for nname, nfunc in (("strict", normalise_strict), ("loose", normalise_loose)):
            ref = nfunc(reference)
            hyp = nfunc(recognised)
            distance = levenshtein(ref, hyp)
            cer = distance / max(1, len(ref))
            print(
                f"{label:14} normalise={nname:6} ref={len(ref):5} hyp={len(hyp):5} "
                f"dist={distance:5} CER={cer:.4f}"
            )
    print()

    # Which reference is right?  The page-0 truth is what page 0 of the rendered
    # image contains, so the all-pages reference must be much worse.
    ref = normalise_strict(reference_blocks)
    hyp = normalise_strict(recognised)
    matcher = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    ops = collections.Counter(tag for tag, *_ in matcher.get_opcodes() if tag != "equal")
    print(f"opcodes against page-0 reference: {dict(ops)}")
    print()
    print("non-equal regions:")
    shown = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        print(f"  {tag:8} ref[{i1}:{i2}]={ref[i1:i2][:60]!r}")
        print(f"           hyp[{j1}:{j2}]={hyp[j1:j2][:60]!r}")
        shown += 1
        if shown >= 12:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
