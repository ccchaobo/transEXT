"""A line-level reading-order measure that cannot be blinded by a short block.

Why this exists.  The word-LCS measure already in the repo scores the two-column pages of
``pdf_b_two_column`` at 0.9733 while the page title is emitted THIRD, after a whole column
of body text.  That is not a small error - the title should be first - but LCS finds the
longest common subsequence, so it happily drops the 8-word title to keep the 200-word
abstract and charges about 1%.  A metric that does not notice a misplaced title cannot be
used to decide how the page should be cut.

This measures something else: the order of **lines**, which are the atoms of the page.
Lines come straight from the text layer, so they are matched between the truth and the
pipeline by geometry rather than by text, which means a pipeline that MERGES blocks
correctly (or wrongly) does not disturb the measurement, and a pipeline that reorders them
does.  The reported figure is the fraction of line pairs that are in the same relative
order as the truth, which penalises displacing a short block exactly as much as a long one.

It is deliberately independent of the paragraph assembler: it calls ``segment_bands``, so
it measures the ORDER the layout produced, not what later stages did with it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from document_core.layout import LayoutParams, segment_bands
from rr_adapters.pdf_extract import PdfTextExtractor

CORPUS = Path("benchmarks/corpus")
IOU_THRESHOLD = 0.5


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def truth_lines(truth: dict, page_index: int) -> list[tuple[str, tuple[float, float, float, float]]]:
    """The page's lines in the recorded reading order, in raw PDF coordinates."""
    page = truth["pages"][page_index]
    blocks = sorted(page.get("blocks") or [], key=lambda b: b.get("reading_order", 0))
    out = []
    for block in blocks:
        texts = block.get("line_texts") or []
        boxes = block.get("line_bboxes") or []
        pages = block.get("line_page_indices") or [page_index] * len(texts)
        for text, box, owner in zip(texts, boxes, pages):
            if owner != page_index or not box:
                continue
            out.append((text, tuple(box)))
    return out


def pipeline_lines(extraction, page_index: int, params: LayoutParams):
    """The page's lines in the order the layout produced, in raw PDF coordinates."""
    lines = extraction.lines_by_page.get(page_index) or []
    if not lines:
        return []
    geometry = extraction.geometry_by_page.get(page_index)
    if geometry is None:
        return []
    bands = segment_bands(lines, geometry.media_height, params)
    out = []
    for band in bands:
        for member in band.line_indices:
            line = lines[member]
            box = geometry.norm_to_raw_box(line.bbox)
            out.append((line.text, box.as_tuple()))
    return out


def compare(pipeline, truth) -> tuple[int, int, int]:
    """Greedy geometry match, then count out-of-order pairs.

    Returns (matched, inversions, possible_pairs).
    """
    used: set[int] = set()
    matched: list[tuple[int, int]] = []  # (pipeline position, truth position)
    for position, (_, box) in enumerate(pipeline):
        best = None
        best_score = IOU_THRESHOLD
        for index, (_, tbox) in enumerate(truth):
            if index in used:
                continue
            score = _iou(box, tbox)
            if score > best_score:
                best, best_score = index, score
        if best is not None:
            used.add(best)
            matched.append((position, best))

    inversions = 0
    pairs = 0
    for i in range(len(matched)):
        for j in range(i + 1, len(matched)):
            pairs += 1
            if matched[i][1] > matched[j][1]:
                inversions += 1
    return len(matched), inversions, pairs


def main() -> int:
    extractor = PdfTextExtractor()
    horizontal_first = "--horizontal-first" in sys.argv
    vertical_first = "--vertical-first" in sys.argv
    if not horizontal_first and not vertical_first:
        horizontal_first = LayoutParams().cut_horizontal_first
    params = LayoutParams(cut_horizontal_first=horizontal_first)
    print(f"cut order: {'horizontal first' if horizontal_first else 'vertical first'}")
    print()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    names = [
        entry["name"]
        for entry in sorted(manifest["entries"], key=lambda e: e["name"])
        if entry["kind"] == "text_pdf"
    ]

    total_matched = total_inversions = total_pairs = 0
    worst: list[tuple[float, str, int, int, int]] = []

    for name in names:
        truth = json.loads((CORPUS / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        extraction = extractor.extract(CORPUS / "files" / f"{name}.pdf")
        doc_matched = doc_inversions = doc_pairs = 0
        for page_index in range(extraction.page_count):
            truth_seq = truth_lines(truth, page_index)
            if len(truth_seq) < 5:
                continue
            pipe_seq = pipeline_lines(extraction, page_index, params)
            matched, inversions, pairs = compare(pipe_seq, truth_seq)
            if pairs == 0:
                continue
            doc_matched += matched
            doc_inversions += inversions
            doc_pairs += pairs
            accuracy = 1.0 - inversions / pairs
            if accuracy < 0.999:
                worst.append((accuracy, name, page_index, inversions, pairs))
        accuracy = 1.0 - doc_inversions / doc_pairs if doc_pairs else 1.0
        total_matched += doc_matched
        total_inversions += doc_inversions
        total_pairs += doc_pairs
        flag = "" if accuracy >= 0.999 else "   <-- out of order"
        print(f"  {name:34} lines={doc_matched:4} pairs={doc_pairs:5} "
              f"inversions={doc_inversions:4} accuracy={accuracy:.4f}{flag}")

    print()
    pooled = 1.0 - total_inversions / total_pairs if total_pairs else 1.0
    print(f"POOLED line-order accuracy: {pooled:.4f}   "
          f"(matched {total_matched} lines, {total_inversions} inverted pairs of "
          f"{total_pairs})")
    if worst:
        print()
        print("WORST PAGES")
        for accuracy, name, page_index, inversions, pairs in sorted(worst)[:12]:
            print(f"  {name:34} page {page_index}  accuracy={accuracy:.4f}  "
                  f"{inversions}/{pairs} pairs inverted")
    return 0 if pooled >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
