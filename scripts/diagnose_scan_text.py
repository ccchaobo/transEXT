"""Check whether the scanned PDF's visible text is complete.

The OCR result is systematically shorter than the ground truth, which suggests
either the rendering is cropping text or the recorded ground truth contains text
that was never drawn.  This compares the two directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

CORPUS = Path("benchmarks/corpus")


def main() -> int:
    truth = json.loads(
        (CORPUS / "truth" / "scan_a_clean_300dpi.json").read_text(encoding="utf-8")
    )
    page = next(p for p in truth["pages"] if p["index"] == 0)
    print(f"ground-truth blocks on page 0: {len(page['blocks'])}")
    for block in page["blocks"][:4]:
        print(f"  [{block['type']:9}] len={len(block['text'])} {block['text'][:70]!r}")
    truth_chars = sum(len(b["text"]) for b in page["blocks"])
    print(f"ground-truth page 0 characters: {truth_chars}")

    # What is visible in the *source* text PDF (the thing that was rasterised)?
    source = pymupdf.open(str(CORPUS / "files" / "pdf_a_single_column.pdf"))
    source_text = source.load_page(0).get_text()
    source.close()
    print(f"\nsource pdf_a page 0 extracted characters: {len(source_text)}")

    # What is visible in the *scan*?  It has no text layer by construction.
    scan = pymupdf.open(str(CORPUS / "files" / "scan_a_clean_300dpi.pdf"))
    scan_page = scan.load_page(0)
    pixmap = scan_page.get_pixmap(dpi=150, alpha=False)
    scan.close()
    print(f"scan page 0 rendered: {pixmap.width}x{pixmap.height}")

    # Compare the ground truth against the source text block by block: the scan
    # was produced from this file, so any text in the truth but not in the source
    # was never drawn.
    missing = []
    for block in page["blocks"]:
        text = block["text"]
        probe = text[:40].strip()
        if probe and probe not in source_text:
            missing.append((block["type"], probe, len(text)))
    print(f"\ntruth blocks whose first 40 chars are absent from the source text: {len(missing)}")
    for kind, probe, length in missing[:8]:
        print(f"  [{kind:9}] len={length} {probe!r}")

    # The reverse: text in the source that the truth does not describe.
    truth_all = " ".join(b["text"] for b in page["blocks"])
    source_lines = [ln for ln in source_text.splitlines() if ln.strip()]
    absent = []
    for line in source_lines:
        probe = line.strip()[:40]
        if probe and probe not in truth_all:
            absent.append(probe)
    print(f"\nsource lines whose first 40 chars are absent from the truth: {len(absent)}")
    for probe in absent[:8]:
        print(f"  {probe!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
