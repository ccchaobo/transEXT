"""Byte-level diagnosis of the mixed-language corpus files."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path("benchmarks/corpus")
EXPECTED = "中英文混排文档的阅读顺序恢复"


def show(label: str, value: str) -> None:
    print(f"{label:28} {value[:60]!r}")
    print(f"{'':28} codepoints={[hex(ord(c)) for c in value[:12]]}")


def main() -> int:
    print("expected title:", repr(EXPECTED))

    # 1. the Python source that defines the title
    source = Path("benchmarks/rr_m0/corpus_content.py").read_bytes()
    needle = EXPECTED.encode("utf-8")
    print(f"corpus_content.py contains expected utf-8 bytes: {needle in source}")

    # 2. the ground-truth JSON for the PDF and the DOCX
    for name in ("pdf_d_mixed_language", "docx_paper_d_mixed"):
        truth = json.loads((ROOT / "truth" / f"{name}.json").read_text(encoding="utf-8"))
        title = truth.get("title", "")
        show(f"truth[{name}].title", title)
        print(f"{'':28} matches expected: {title == EXPECTED}")

    # 3. the DOCX XML itself
    path = ROOT / "files" / "docx_paper_d_mixed.docx"
    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
    print(f"document.xml size: {len(xml_bytes)}")
    print(f"document.xml contains expected utf-8 bytes: {EXPECTED.encode('utf-8') in xml_bytes}")
    text = xml_bytes.decode("utf-8", "replace")
    index = text.find("<w:t")
    print("first w:t fragment:", repr(text[index : index + 160]))

    # 4. report from python-docx directly
    from docx import Document

    doc = Document(str(path))
    show("python-docx paragraph[0]", doc.paragraphs[0].text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
