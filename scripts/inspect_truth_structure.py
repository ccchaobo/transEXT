"""Look at one two-column truth file in detail before writing the metrics.

The matching rules for the layout metrics depend on the real structure: how blocks
are numbered, whether table cells are separate blocks, and what a cross-page
paragraph looks like.  This prints that structure so the rules are chosen from the
data rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

CORPUS = Path("benchmarks/corpus")
NAME = "pdf_b_two_column"
truth = json.loads((CORPUS / "truth" / f"{NAME}.json").read_text(encoding="utf-8"))

print(f"=== {NAME} ===")
print(f"  layout={truth['layout']} columns={truth['columns']} "
      f"pages={truth['page_count']} cross_page_paragraph={truth['cross_page_paragraph']}")

for page in truth["pages"][:2]:
    print(f"\n  --- page {page['index']} ({len(page['blocks'])} blocks) ---")
    for block in page["blocks"]:
        text = block["text"]
        print(
            f"    idx={block['index']:3} order={block['reading_order']:3} "
            f"{block['type']:11} pages={block['page_indices']} "
            f"n_lines={block['n_lines']:2} bbox={[round(v) for v in block['bbox']]}"
        )
        print(f"        {text[:78]!r}")

print("\n=== reading_order numbering across the whole document ===")
orders = []
for page in truth["pages"]:
    for block in page["blocks"]:
        orders.append((block["reading_order"], page["index"], block["index"], block["type"]))
orders.sort()
print(f"  {len(orders)} blocks, reading_order range "
      f"{orders[0][0]}..{orders[-1][0]}, contiguous={orders[-1][0] - orders[0][0] + 1 == len(orders)}")
print("  first 6:", [(o[0], o[1], o[3]) for o in orders[:6]])
print("  last 6 :", [(o[0], o[1], o[3]) for o in orders[-6:]])
