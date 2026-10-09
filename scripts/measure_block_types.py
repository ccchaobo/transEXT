"""Measure block-type classification against the corpus ground truth.

The corpus records a type for every block, so the classifier can be scored rather than
eyeballed.  The comparison is per matched block, and the confusion pairs are printed,
because *which* types get mixed up decides how bad it is: reading a paragraph as a heading
is cosmetic, reading a citation as a paragraph sends a DOI through a translator.
"""

from __future__ import annotations

import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from document_core.blocks import body_font_size, classify_paragraph
from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import match_blocks_to_paragraphs
from measure_layout import load_truth_blocks

CORPUS = Path("benchmarks/corpus")


def _normalise(text: str) -> str:
    return " ".join((text or "").split())

#: Types where a mistake changes what gets translated, as opposed to only how it is shown.
TRANSLATION_CRITICAL = {"reference", "formula", "table", "figure", "page_number", "header", "footer"}

#: Structural types the pipeline does not attempt at all: there is no table or figure
#: extraction, so a table cell arrives as ordinary text and no classifier can label it
#: correctly.  Counting these as classification errors would hide the real ones — on this
#: corpus they are 62% of matched blocks.
NOT_ATTEMPTED = {"table", "table_cell", "figure"}


def main() -> int:
    extractor = PdfTextExtractor()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        e["name"]
        for e in sorted(manifest["entries"], key=lambda e: e["name"])
        if e["kind"] == "text_pdf"
    ]

    correct = 0
    considered = 0
    skipped = 0
    merged = 0
    unrelated = 0
    confusion: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], list[tuple[str, str]]] = {}
    per_type_total: Counter[str] = Counter()
    per_type_correct: Counter[str] = Counter()

    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        result = analyse(extraction.lines_by_page)
        body_size = body_font_size(result.paragraphs)

        blocks = load_truth_blocks(truth, extraction.geometry_by_page)
        paragraphs = [
            (p.text, tuple(sorted(p.page_indices or [0])), tuple(p.bbox.as_list()))
            for p in result.paragraphs
        ]
        report = match_blocks_to_paragraphs(blocks, paragraphs)

        doc_correct = doc_total = 0
        for match in report.matches:
            if match.paragraph_index is None:
                continue
            truth_type = match.block.block_type
            if truth_type in NOT_ATTEMPTED:
                skipped += 1
                continue
            para = result.paragraphs[match.paragraph_index]
            # Three buckets, because equality alone cannot tell a real merge from the
            # pipeline's text cleaning (dehyphenation, quote and dash normalisation):
            #
            #   merged     the block's text is a proper part of the paragraph, so several
            #              blocks became one paragraph.  Comparing types here would measure
            #              segmentation, not classification, so these are counted, not
            #              scored.
            #   same block the texts differ only within cleaning noise, so the pair really
            #              is one block and the type comparison is meaningful.
            #   unrelated  the matcher paired things that are not the same content at all;
            #              scoring those would attribute a matcher error to the classifier.
            block_text = _normalise(match.block.text)
            para_text = _normalise(para.text)
            if para_text != block_text and block_text and block_text in para_text:
                merged += 1
                continue
            ratio = SequenceMatcher(None, block_text, para_text).ratio()
            if ratio < 0.90:
                unrelated += 1
                continue
            decision = classify_paragraph(
                para,
                position=match.paragraph_index,
                page_index=para.page_indices[0] if para.page_indices else 0,
                body_size=body_size,
            )
            got = decision.type.value
            doc_total += 1
            per_type_total[truth_type] += 1
            if got == truth_type:
                doc_correct += 1
                per_type_correct[truth_type] += 1
            else:
                confusion[(truth_type, got)] += 1
                examples.setdefault((truth_type, got), []).append(
                    (name, _normalise(para.text)[:160])
                )
        correct += doc_correct
        considered += doc_total
        print(f"  {name:34} {doc_correct:3}/{doc_total:3}  "
              f"{(doc_correct / doc_total if doc_total else 0):.3f}")

    print()
    print(f"OVERALL block-type accuracy: {correct}/{considered} = "
          f"{(correct / considered if considered else 0):.4f}")
    print(f"  ({skipped} structural blocks skipped: no table/figure extraction exists, so "
          f"they cannot be classified and are not errors of this classifier)")
    print(f"  ({merged} matches excluded: the block's text is part of a longer paragraph, "
          f"so the pipeline merged blocks and comparing types would measure segmentation)")
    print(f"  ({unrelated} matches excluded: block and paragraph are different content, "
          f"which is a matcher error and not the classifier's)")
    print()
    print("PER TRUE TYPE")
    for block_type in sorted(per_type_total, key=lambda t: -per_type_total[t]):
        total = per_type_total[block_type]
        got = per_type_correct[block_type]
        flag = ""
        if block_type in TRANSLATION_CRITICAL and got < total:
            missed = total - got
            flag = f"   <-- {missed} mistyped, and this type is translation-critical"
        print(f"  {block_type:12} {got:3}/{total:3}  {got / total:.3f}{flag}")

    print()
    print("TOP CONFUSIONS (true -> predicted)")
    for (true_type, got), count in confusion.most_common(12):
        critical = "  [translation-critical]" if true_type in TRANSLATION_CRITICAL else ""
        print(f"  {true_type:12} -> {got:12} {count:3}{critical}")
        for source, text in examples.get((true_type, got), [])[:3]:
            print(f"        {source}: {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
