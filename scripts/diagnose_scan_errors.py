"""Compare recognised lines against ground-truth lines for one scanned page.

A CER of 0.77 while the line *count* is correct is not what image noise looks
like; noise produces dropped characters and fragmented words, not a consistent
proportion of wrong text.  A stable high ratio usually means the comparison
itself is wrong -- the recogniser read something real, but it is being compared
against the wrong reference.

So this prints the two line sequences side by side with a diff, which shows
immediately whether the recogniser is failing or the reference is mismatched.
"""

from __future__ import annotations

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
    from scripts.spike_ocr import _normalise, truth_text
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spike_ocr import _normalise, truth_text


def main() -> int:
    document = sys.argv[1] if len(sys.argv) > 1 else "scan_e_grayscale"
    page_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 300

    truth = json.loads((CORPUS / "truth" / f"{document}.json").read_text(encoding="utf-8"))
    page = next(p for p in truth["pages"] if p["index"] == page_index)

    reference = truth_text(truth, page_index)
    reference_lines = [line for line in reference.split("\n") if line.strip()]
    reference_flat = _normalise(reference)

    doc = pymupdf.open(str(CORPUS / "files" / f"{document}.pdf"))
    pixmap = doc.load_page(page_index).get_pixmap(dpi=dpi, alpha=False)
    doc.close()
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

    engine = RapidOCR()
    result, _elapsed = engine(np.asarray(image))
    recognised_lines = [text for _box, text, _score in (result or [])]
    recognised_flat = _normalise("\n".join(recognised_lines))

    print(f"document      : {document} page {page_index} @ {dpi} dpi")
    print(f"render        : {pixmap.width}x{pixmap.height}")
    print(f"truth lines   : {len(reference_lines)}")
    print(f"ocr lines     : {len(recognised_lines)}")
    print(f"truth chars   : {len(reference_flat)}")
    print(f"ocr chars     : {len(recognised_flat)}")
    print()

    print("--- ground truth, first 12 lines ---")
    for index, line in enumerate(reference_lines[:12]):
        print(f"  T{index:>3} | {line}")
    print()
    print("--- recognised, first 12 lines ---")
    for index, line in enumerate(recognised_lines[:12]):
        print(f"  O{index:>3} | {line}")
    print()

    matcher = difflib.SequenceMatcher(None, reference_lines, recognised_lines, autojunk=False)
    print("--- line diff ---")
    shown = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        shown += 1
        if shown > 14:
            print("  ... more")
            break
        print(f"  {tag}: truth[{i1}:{i2}] vs ocr[{j1}:{j2}]")
        for line in reference_lines[i1:i2][:6]:
            print(f"      - {line}")
        for line in recognised_lines[j1:j2][:6]:
            print(f"      + {line}")

    ratio = matcher.ratio()
    print()
    print(f"line-sequence similarity: {ratio:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
