"""Is the extractor itself losing text, and if so, how?

Coverage is identical before and after the layout pipeline for the affected pages,
so the pipeline is innocent and the loss is in ``PdfTextExtractor``.  This compares
the extractor's output against PyMuPDF's own plain-text extraction for the same
page, which is an independent answer to "what text is on this page".

Two candidate mechanisms are tested, because they need different fixes:

* the span loop iterates every span, so if spans are dropped it is the loop;
* ``get_text("dict")`` may omit text that ``get_text("text")`` reports, in which
  case the structured API is the wrong one to read.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pymupdf

from rr_adapters.pdf_extract import PdfTextExtractor
from rr_m0.layout_metrics import BODY_TYPES, body_coverage

CORPUS = Path("benchmarks/corpus")
NAME = "pdf_a_single_column"


def main() -> int:
    truth = json.loads((CORPUS / "truth" / f"{NAME}.json").read_text(encoding="utf-8"))
    pdf = CORPUS / "files" / f"{NAME}.pdf"
    extraction = PdfTextExtractor().extract(pdf)

    doc = pymupdf.open(str(pdf))
    for page_index in (0, 1):
        page = doc.load_page(page_index)
        plain = page.get_text("text")

        dict_text = ""
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                dict_text += "".join(s.get("text", "") for s in line.get("spans", [])) + "\n"

        adapter_text = "\n".join(
            ln.text for ln in extraction.lines_by_page.get(page_index, [])
        )

        ref = " ".join(
            b["text"] for b in truth["pages"][page_index]["blocks"]
            if (b.get("type") or "paragraph") in BODY_TYPES and b["text"].strip()
        )

        print(f"\n=== page {page_index} ===")
        for label, text in (
            ("truth reference", ref),
            ("get_text('text')", plain),
            ("get_text('dict')", dict_text),
            ("adapter output", adapter_text),
        ):
            cov, missing, length = body_coverage(ref, text)
            print(f"  {label:18} chars={len(text.split()) and len(text) or 0:5} "
                  f"coverage={cov:.4f} missing={missing}")

        plain_words = Counter(plain.split())
        dict_words = Counter(dict_text.split())
        adapter_words = Counter(adapter_text.split())
        print(f"  words: plain={sum(plain_words.values())} "
              f"dict={sum(dict_words.values())} adapter={sum(adapter_words.values())}")

        lost_plain_to_adapter = plain_words - adapter_words
        print(f"  words in plain text but not in adapter output: "
              f"{sum(lost_plain_to_adapter.values())}")
        for word, count in lost_plain_to_adapter.most_common(12):
            print(f"      {word!r} x{count}")
        lost_dict_to_adapter = dict_words - adapter_words
        print(f"  words in dict text but not in adapter output: "
              f"{sum(lost_dict_to_adapter.values())}")
        for word, count in lost_dict_to_adapter.most_common(12):
            print(f"      {word!r} x{count}")
        lost_plain_to_dict = plain_words - dict_words
        print(f"  words in plain text but not in dict text: "
              f"{sum(lost_plain_to_dict.values())}")
        for word, count in lost_plain_to_dict.most_common(12):
            print(f"      {word!r} x{count}")
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
