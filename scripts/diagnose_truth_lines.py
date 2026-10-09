"""Why does a two-column page's ground truth contain only one line?

``scan_b_two_column_noisy`` recognises 96 lines on page 0, but its recorded
``visible_text`` splits into a single line.  That makes the reading-order metric
vacuous (it returns a perfect score when fewer than two lines can be matched), so
the "order=1.0" figures for those pages mean nothing until this is understood.

This prints what the truth actually contains and where it comes from, so the
ground-truth defect can be fixed rather than worked around.
"""

from __future__ import annotations

import json
from pathlib import Path

CORPUS = Path("benchmarks/corpus")

for name in ("scan_b_two_column_noisy", "scan_e_grayscale", "scan_a_clean_300dpi"):
    truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
    print(f"=== {name} ===")
    for page in truth["pages"][:1]:
        print(f"  page index: {page['index']}")
        visible = page.get("visible_text", "")
        print(f"  visible_text: {len(visible)} chars, "
              f"{len(visible.splitlines())} raw line(s)")
        print(f"    head: {visible[:160]!r}")
        print(f"    tail: {visible[-120:]!r}")
        blocks = page.get("visible_blocks", page.get("blocks", []))
        print(f"  visible_blocks: {len(blocks)}")
        for block in blocks[:4]:
            text = block.get("text", "")
            print(
                f"    kind={block.get('kind')!r:14} "
                f"lines={len(text.splitlines())} chars={len(text)} "
                f"head={text[:60]!r}"
            )
    print()
