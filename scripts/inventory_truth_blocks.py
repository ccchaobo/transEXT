"""Inventory the corpus ground-truth blocks that the layout metrics need.

Prints what a block record actually contains, and how many blocks the text-PDF
sample provides, because the spec's region-accuracy item requires at least 200
sampled blocks and that has to be checked before a metric can be built on it.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CORPUS = Path("benchmarks/corpus")
TRUTH = CORPUS / "truth"
MANIFEST = CORPUS / "manifest.json"

manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
kinds = {entry["name"]: entry["kind"] for entry in manifest["entries"]}

print("=== kinds in the manifest ===")
for kind, count in sorted(Counter(kinds.values()).items()):
    print(f"  {kind:12} {count}")

text_pdfs = [name for name, kind in kinds.items() if kind == "text_pdf"]
print(f"\n=== block record shape (first text PDF: {text_pdfs[0]}) ===")
truth = json.loads((TRUTH / f"{text_pdfs[0]}.json").read_text(encoding="utf-8"))
page = truth["pages"][0]
block = page["blocks"][0]
print(f"  top-level keys : {sorted(truth)}")
print(f"  page keys      : {sorted(page)}")
print(f"  block keys     : {sorted(block)}")
print(f"  sample block   : {json.dumps(block, ensure_ascii=False)[:400]}")

total_blocks = 0
total_pages = 0
per_file: list[tuple[str, int, int]] = []
for name in sorted(text_pdfs):
    document = json.loads((TRUTH / f"{name}.json").read_text(encoding="utf-8"))
    blocks = sum(len(p.get("blocks", [])) for p in document["pages"])
    pages = len(document["pages"])
    total_blocks += blocks
    total_pages += pages
    per_file.append((name, pages, blocks))

print(f"\n=== text-PDF block supply ===")
for name, pages, blocks in per_file:
    print(f"  {name:34} pages={pages} blocks={blocks}")
print(f"  {'TOTAL':34} pages={total_pages} blocks={total_blocks}")
print(f"\n  spec needs >= 200 sampled blocks: {'OK' if total_blocks >= 200 else 'NOT ENOUGH'}")

types = Counter()
for name in sorted(text_pdfs):
    document = json.loads((TRUTH / f"{name}.json").read_text(encoding="utf-8"))
    for p in document["pages"]:
        for b in p.get("blocks", []):
            types[b.get("type")] += 1
print(f"\n=== block types across text PDFs ===")
for kind, count in types.most_common():
    print(f"  {kind:14} {count}")
