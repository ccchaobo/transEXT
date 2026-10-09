"""How well one pipeline block corresponds to one corpus block.

Reading order being correct is not enough. The reader anchors a translation to a block, so
what matters is whether one block is one *thing*: a paragraph that has absorbed three others
cannot be paired with its translation piece by piece, and a paragraph split in two produces
two anchors for one thought.

Reports, per document, over the corpus blocks that carry text:

* **1:1**     one block matches one paragraph, so an anchor means what it says
* **merged**  the block's text is part of a longer paragraph, so the anchor is too coarse
* **split**   several blocks match the same paragraph
* **missing** the block matched nothing at all, so it has no anchor

A block that is merged is not wrong in the way a missing block is: the text is still there
and still translated, it is just not addressable on its own.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import match_blocks_to_paragraphs
from measure_layout import load_truth_blocks

CORPUS = Path("benchmarks/corpus")
STRUCTURE_ONLY = {"table", "table_cell", "figure"}


def normalise(text: str) -> str:
    return " ".join((text or "").split())


def main() -> int:
    extractor = PdfTextExtractor()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        entry["name"]
        for entry in sorted(manifest["entries"], key=lambda e: e["name"])
        if entry["kind"] == "text_pdf"
    ]

    totals: Counter[str] = Counter()
    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        layout = analyse(extraction.lines_by_page)

        blocks = [
            block
            for block in load_truth_blocks(truth, extraction.geometry_by_page)
            if normalise(block.text) and block.block_type not in STRUCTURE_ONLY
        ]
        paragraphs = [
            (p.text, tuple(sorted(p.page_indices or [0])), tuple(p.bbox.as_list()))
            for p in layout.paragraphs
        ]
        report = match_blocks_to_paragraphs(blocks, paragraphs)

        # Which paragraphs were claimed, so that "split" can be seen.
        claimed: Counter[int] = Counter()
        status: Counter[str] = Counter()
        for match in report.matches:
            if match.paragraph_index is None:
                status["missing"] += 1
                continue
            claimed[match.paragraph_index] += 1
            paragraph_text = normalise(paragraphs[match.paragraph_index][0])
            block_text = normalise(match.block.text)
            if paragraph_text != block_text and block_text in paragraph_text:
                status["merged"] += 1
            elif paragraph_text == block_text:
                status["1:1"] += 1
            else:
                status["differs"] += 1
        # Blocks that matched but whose paragraph is shared with another block: the anchor
        # is not distinct even though the text lines up.
        for match in report.matches:
            if match.paragraph_index is None:
                continue
            if claimed[match.paragraph_index] > 1 and status["1:1"]:
                status["split"] += 1

        total = sum(status.values())
        totals.update(status)
        one_to_one = status["1:1"]
        print(f"  {name:34} blocks={total:4} paragraphs={len(layout.paragraphs):4}  "
              f"1:1={one_to_one:3} ({one_to_one / total if total else 0:.3f})  "
              f"merged={status['merged']:3} split={status['split']:3} "
              f"missing={status['missing']:3}")

    total = sum(totals.values())
    print()
    print(f"POOLED over {total} corpus blocks")
    for key in ("1:1", "merged", "split", "missing", "differs"):
        count = totals[key]
        print(f"  {key:9} {count:4}  {count / total if total else 0:.3f}")
    anchored = totals["1:1"]
    print()
    print(f"blocks addressable on their own: {anchored}/{total} = "
          f"{anchored / total if total else 0:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
