"""What does the truth record about each page's source kind?"""

from __future__ import annotations

import json
from pathlib import Path

CORPUS = Path("benchmarks/corpus")
for name in ("mixed_f_hybrid", "scan_a_clean_300dpi", "pdf_a_single_column", "img_a_clean_png"):
    path = CORPUS / "truth" / f"{name}.json"
    if not path.exists():
        print(f"{name}: no truth file")
        continue
    truth = json.loads(path.read_text(encoding="utf-8"))
    print(
        f"{name}: source_kind={truth.get('source_kind')!r} "
        f"source_format={truth.get('source_format')!r}"
    )
    for page in truth["pages"]:
        print(f"    page {page['index']}: source_kind={page.get('source_kind')!r}")
