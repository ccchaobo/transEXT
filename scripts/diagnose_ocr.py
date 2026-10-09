"""Compare OCR output against corpus ground truth, line by line.

A single character error rate hides *what* went wrong.  This prints aligned
evidence: reference text, recognised text, and the first differing region, so a
systematic fault (wrong reading order, duplicated text, a doubled rotation) can be
told apart from ordinary recognition noise.

Run::

    python scripts/diagnose_ocr.py scan_a_clean_300dpi --page 0
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

import pymupdf
from PIL import Image

try:  # works when run as a module
    from scripts.spike_ocr import _normalise, edit_distance
except ModuleNotFoundError:  # works when run as a plain script
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise, edit_distance

CORPUS = Path("benchmarks/corpus")


def reference_text(name: str, page_index: int, joined: bool = True) -> str:
    truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
    lines: list[str] = []
    for block in TruthPage(truth, page_index).blocks:
        lines.append(block)
    return ("\n".join(lines) if joined else " ".join(lines))


class TruthPage:
    def __init__(self, truth: dict, page_index: int) -> None:
        self.page = next(p for p in truth["pages"] if p["index"] == page_index)

    @property
    def blocks(self) -> list[str]:
        return [b["text"] for b in self.page["blocks"]]

    @property
    def block_records(self) -> list[dict]:
        return self.page["blocks"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("document")
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--raw-dpi", type=int, default=0, help="render this dpi instead")
    parser.add_argument("--rows", type=int, default=25)
    args = parser.parse_args()

    pdf = CORPUS / "files" / f"{args.document}.pdf"
    doc = pymupdf.open(str(pdf))
    page = doc.load_page(args.page)
    print(f"page rect: {page.rect}")
    print(f"page rotation: {page.rotation}")
    print(f"cropbox: {page.cropbox}  mediabox: {page.mediabox}")
    dpi = args.raw_dpi or args.dpi
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    print(f"rendered: {pixmap.width}x{pixmap.height} at {dpi} dpi")
    doc.close()

    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    import numpy as np

    result, elapsed = engine(np.asarray(image))
    print(f"engine elapsed: {elapsed}")
    if not result:
        print("NO TEXT RECOGNISED")
        return 1

    recognised_lines = [text for _box, text, _score in result]
    reference = reference_text(args.document, args.page)
    reference_lines = TruthPage(
        json.loads((CORPUS / "truth" / f"{args.document}.json").read_text(encoding="utf-8")),
        args.page,
    ).blocks

    print(f"\nrecognised {len(recognised_lines)} lines, reference {len(reference_lines)} blocks")
    print(f"normalised chars: recognised={len(_normalise(chr(10).join(recognised_lines)))} "
          f"reference={len(_normalise(reference))}")
    distance = edit_distance(_normalise(reference), _normalise(chr(10).join(recognised_lines)))
    print(f"edit distance: {distance}  CER={distance / max(1, len(_normalise(reference))):.4f}")

    print("\n--- reference blocks (first %d) ---" % args.rows)
    for line in reference_lines[: args.rows]:
        print(f"  R| {line[:100]}")
    print("\n--- recognised lines (first %d) ---" % args.rows)
    for line in recognised_lines[: args.rows]:
        print(f"  O| {line[:100]}")

    # Order comparison: are the same lines present but in a different order?
    ref_norm = [_normalise(line) for line in reference_lines]
    ocr_norm = [_normalise(line) for line in recognised_lines]
    ref_set = set(ref_norm)
    ocr_set = set(ocr_norm)
    exact = len(ref_set & ocr_set)
    print(f"\nidentical normalised lines: {exact} of {len(ref_set)} reference / {len(ocr_set)} recognised")
    only_ref = [line for line in ref_norm if line not in ocr_set][:6]
    only_ocr = [line for line in ocr_norm if line not in ref_set][:6]
    print("  reference lines not matched exactly:")
    for line in only_ref:
        print(f"    {line[:96]}")
    print("  recognised lines not in reference:")
    for line in only_ocr:
        print(f"    {line[:96]}")

    # Show the first divergence in the flattened strings.
    flat_ref = _normalise(reference)
    flat_ocr = _normalise(chr(10).join(recognised_lines))
    matcher = difflib.SequenceMatcher(None, flat_ref, flat_ocr, autojunk=False)
    print("\n--- first divergences (flattened, punctuation/space removed) ---")
    shown = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        print(f"  {tag:8} ref[{i1}:{i2}]={flat_ref[i1:i2][:60]!r}")
        print(f"           ocr[{j1}:{j2}]={flat_ocr[j1:j2][:60]!r}")
        print(f"           context ref: ...{flat_ref[max(0, i1 - 40):i1]!r}")
        shown += 1
        if shown >= 8:
            break

    # Is the recognised text a duplicate or scrambled version?
    print("\n--- structure checks ---")
    print(f"  recognised char count / reference char count = "
          f"{len(flat_ocr) / max(1, len(flat_ref)):.2f}")
    seen: dict[str, int] = {}
    for line in ocr_norm:
        seen[line] = seen.get(line, 0) + 1
    duplicates = {line: n for line, n in seen.items() if n > 1 and len(line) > 12}
    print(f"  duplicated recognised lines (len>12): {len(duplicates)}")
    for line, count in list(duplicates.items())[:5]:
        print(f"    x{count} {line[:72]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
