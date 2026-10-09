"""The honest reading-order measure: reference sequence versus pipeline sequence.

`adjacent_order_accuracy` counts only adjacent pairs where *both* blocks were matched, so
a block the pipeline moved out of order and the matcher then failed to pair is excluded
from the score entirely.  On `pdf_b_two_column` that hid a real defect: the pipeline emits
left, right, left, right down the page, and the metric still reported 0.9858 because most
of the interleaved blocks were not matched.

This measures the thing the acceptance item is actually about — does the pipeline emit
the page's text in the order the page reads — without depending on a matcher deciding
which block is which.  For each page it builds the reference sequence and the pipeline
sequence as lists of *word* tokens in order, and takes the longest common subsequence
normalised by the shorter list.  Word tokens are used because they are unique enough to
order and they survive the pipeline legitimately merging or splitting blocks.  A pipeline
that reads across columns cannot possibly preserve the reference word order, so the
figure drops; a pipeline that reorders correctly leaves it near 1.0.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from document_core.layout import analyse
from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import BODY_TYPES
from measure_layout import load_truth_blocks

CORPUS = Path("benchmarks/corpus")
TOKEN_RE = re.compile(r"[0-9A-Za-z\u00c0-\u024f]+")


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def lcs_length(a: list[str], b: list[str]) -> int:
    """Longest common subsequence length, O(len(a) * len(b)) with one row."""
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    for token_a in a:
        current = [0]
        for j, token_b in enumerate(b, start=1):
            if token_a == token_b:
                current.append(previous[j - 1] + 1)
            else:
                current.append(max(previous[j], current[j - 1]))
        previous = current
    return previous[-1]


def main() -> int:
    extractor = PdfTextExtractor()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        e["name"]
        for e in sorted(manifest["entries"], key=lambda e: e["name"])
        if e["kind"] == "text_pdf"
    ]

    pooled_correct = 0
    pooled_total = 0
    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        result = analyse(extraction.lines_by_page)
        blocks = load_truth_blocks(truth, extraction.geometry_by_page)

        page_metrics: list[tuple[int, float, int, int]] = []
        for page in truth["pages"]:
            page_index = page["index"]
            if any(i.index == page_index and i.likely_scanned for i in extraction.page_infos):
                continue
            reference_blocks = sorted(
                (
                    b
                    for b in blocks
                    if b.page_index == page_index
                    and b.block_type in BODY_TYPES
                    and b.has_text
                ),
                key=lambda b: b.reading_order,
            )
            reference_tokens: list[str] = []
            for b in reference_blocks:
                reference_tokens.extend(tokens(b.text))
            hypothesis_tokens: list[str] = []
            for para in result.paragraphs:
                if page_index in (para.page_indices or [0]):
                    hypothesis_tokens.extend(tokens(para.text))
            if len(reference_tokens) < 20 or len(hypothesis_tokens) < 20:
                continue
            common = lcs_length(reference_tokens, hypothesis_tokens)
            score = common / min(len(reference_tokens), len(hypothesis_tokens))
            page_metrics.append(
                (page_index, score, len(reference_tokens), len(hypothesis_tokens))
            )

        print(f"=== {name}  (truth layout={truth['layout']}) ===")
        for page_index, score, n_ref, n_hyp in page_metrics:
            pooled_correct += score * min(n_ref, n_hyp)
            pooled_total += min(n_ref, n_hyp)
            flag = "OK " if score >= 0.95 else "LOW"
            print(
                f"  page {page_index}: order={score:.4f} {flag} "
                f"(ref words={n_ref}, pipeline words={n_hyp})"
            )

    print("\n" + "=" * 70)
    print(f"POOLED reading-order score (word LCS) : "
          f"{pooled_correct / pooled_total:.4f}")
    print(f"  target >= 0.95")
    print(f"  {'PASS' if pooled_correct / pooled_total >= 0.95 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
