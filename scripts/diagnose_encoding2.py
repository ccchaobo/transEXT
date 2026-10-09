"""Pin down where the mixed-language text is being corrupted."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

import rr_m0.corpus_content as cc


def main() -> int:
    title = "中英文混排文档的阅读顺序恢复"
    items = cc.paper_d()
    abstract = items[0].text
    print("source module title bytes ok:", title.encode("utf-8") in Path(
        "benchmarks/rr_m0/corpus_content.py"
    ).read_bytes())
    print("paper_d() title-ish text:", repr(abstract[:40]))
    print("abstract contains expected:", title[:6] in abstract)
    print("abstract codepoints:", [hex(ord(c)) for c in abstract[:12]])

    # What the PDF actually contains.
    doc = pymupdf.open("benchmarks/corpus/files/pdf_d_mixed_language.pdf")
    page_text = doc.load_page(0).get_text()
    doc.close()
    print()
    print("pdf title line:", repr(page_text.splitlines()[0]))
    print("pdf contains expected title:", title in page_text)
    print("pdf contains mojibake:", "涓" in page_text)

    # What the truth JSON says.
    truth = json.loads(
        Path("benchmarks/corpus/truth/pdf_d_mixed_language.json").read_text(encoding="utf-8")
    )
    print()
    print("truth title:", repr(truth["title"]))
    print("truth contains expected:", title in json.dumps(truth, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
