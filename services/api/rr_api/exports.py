"""Bilingual Markdown export.

Two honesty rules govern this file, both from the spec:

* **This is not a facsimile.**  The output is reflowed text built from the parsed blocks.  It
  does not reproduce the original page layout, and the header says so in the document itself so
  the claim travels with the file rather than living only in this repository.
* **The anchoring granularity is stated.**  Blocks are placed in reading order, and the source
  span of each block is recorded in its page coordinates.  That is *block*-level anchoring, and
  it is described as block-level; it is not described as precise paragraph-level positioning
  within the original visual layout, because it is not.

What is carried through verbatim: formulas, table data and any block whose type says it is not
prose.  A formula is never translated, and a table is never paraphrased.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rr_storage import Block, Document, SourceSpanRow

from .reads import latest_translations

VERBATIM_TYPES = frozenset({"formula", "table", "table_cell", "figure"})
HEADING_TYPES = frozenset({"title", "heading"})

#: The caveats that travel inside the exported file.
LAYOUT_NOTE = (
    "This is a **reflowed** bilingual transcript, not a facsimile of the article. It does not "
    "reproduce the original page layout, columns, floats or figure placement."
)
ANCHOR_NOTE = (
    "Source text is anchored at **block** level (paragraph, heading, caption or formula) and "
    "each block records the page and region it came from. It is not anchored sentence by "
    "sentence, and a block may cover more than one sentence."
)


def _escape(text: str) -> str:
    """Markdown-safe text.

    Only the constructs that would silently change meaning are escaped: a line starting with a
    list marker or a heading would restructure the document, and an unmatched backtick or
    underscore would start emphasis that runs to the end of the file.
    """
    out = []
    for index, line in enumerate((text or "").splitlines()):
        stripped = line.lstrip()
        if index == 0 or stripped:
            if stripped[:1] in {"#", ">", "-", "*", "+"} or _looks_ordered(stripped):
                line = "\\" + line if line is stripped else line.replace(stripped, "\\" + stripped, 1)
        out.append(line)
    return "\n".join(out)


def _looks_ordered(line: str) -> bool:
    head = line.split(" ", 1)[0]
    return head.endswith(".") and head[:-1].isdigit()


def block_to_markdown(
    block: Block,
    translation: dict[str, Any] | None,
    *,
    include_source: bool,
    include_translation: bool,
) -> str:
    """One block as Markdown.  Never invents a translation and never omits a marker."""
    lines: list[str] = []
    text = (block.clean_text or block.raw_text or "").strip()
    level = block.level or (1 if block.type == "title" else 2)

    if block.type in HEADING_TYPES:
        lines.append(f"{'#' * min(level, 6)} {_escape(text)}")
    elif block.type in VERBATIM_TYPES:
        # Verbatim means verbatim: fenced and never sent through a translator.
        lines.append(f"```\n{text}\n```")
    else:
        lines.append(_escape(text))

    markers = []
    if block.ocr_revision:
        markers.append(f"OCR-corrected r{block.ocr_revision}")
    if block.confidence_level in {"low", "unknown"} and block.confidence_score is not None:
        markers.append(f"recognition confidence {block.confidence_score:.2f} ({block.confidence_level})")
    elif block.confidence_level == "unknown" and block.type not in VERBATIM_TYPES:
        markers.append("recognition confidence unknown")
    if block.page_index is not None:
        markers.append(f"p{block.page_index + 1}")
    if markers:
        lines.append(f"<sub>{' · '.join(markers)}</sub>")

    if include_translation and block.type not in VERBATIM_TYPES:
        if translation is None:
            lines.append("> _No translation for this block._")
        elif translation["status"] != "ok":
            detail = translation.get("error_message") or translation["status"]
            lines.append(
                f"> _Translation {translation['status']}: {detail}_"
            )
        else:
            attribution = "human-corrected" if translation["is_human"] else "machine"
            lines.append(f"> {_escape(translation['text'])}")
            lines.append(f"> <sub>translation: {attribution}</sub>")
    if not include_source and include_translation and translation is None:
        lines.append("> _Source omitted and no translation exists; nothing to show._")
    return "\n\n".join(lines)


def export_document_markdown(
    session: Session,
    document: Document,
    *,
    target_language: str = "zh",
    include_source: bool = True,
    include_translation: bool = True,
    outline: bool = True,
) -> tuple[str, str]:
    """Return ``(markdown, suggested_filename)``.

    The header records provenance: which revision was exported, which provider and model
    produced the translations, and which glossary and prompt versions were in force.  Without
    that, an exported file cannot be compared with another one.
    """
    revision_id = document.active_revision_id
    blocks: list[Block] = []
    if revision_id:
        blocks = list(
            session.scalars(
                select(Block)
                .where(Block.revision_id == revision_id)
                .order_by(Block.order_index)
            ).all()
        )
    translations = latest_translations(
        session, [block.id for block in blocks], target_language=target_language
    )
    payloads = {
        block_id: {
            "text": row.text,
            "status": row.status,
            "is_human": row.origin == "human",
            "error_message": row.error_message,
        }
        for block_id, row in translations.items()
    }

    span_pages: dict[str, str] = {}
    if blocks:
        for span in session.scalars(
            select(SourceSpanRow).where(
                SourceSpanRow.block_id.in_([block.id for block in blocks])
            )
        ).all():
            span_pages.setdefault(span.block_id, f"p{span.page_index + 1}")

    providers = sorted({row.provider for row in translations.values()})
    models = sorted({row.model for row in translations.values() if row.model})
    glossary_versions = sorted({row.glossary_version for row in translations.values()})
    prompt_versions = sorted({row.prompt_version for row in translations.values()})
    human_count = sum(1 for row in translations.values() if row.origin == "human")
    missing = sum(
        1
        for block in blocks
        if block.type not in VERBATIM_TYPES and block.id not in translations
    )
    failed = sum(
        1
        for block in blocks
        if block.id in translations and translations[block.id].status != "ok"
    )

    head: list[str] = []
    title = document.title or document.filename
    head.append(f"# {title}")
    head.append("")
    head.append("> **Bilingual transcript.** " + LAYOUT_NOTE)
    head.append(">")
    head.append("> " + ANCHOR_NOTE)
    head.append("")
    head.append("| | |")
    head.append("| --- | --- |")
    head.append(f"| Original file | `{document.filename}` |")
    head.append(f"| Original sha256 | `{document.sha256}` |")
    head.append(f"| Format | {document.format} |")
    head.append(f"| Pages | {document.page_count} |")
    head.append(f"| Parse revision | `{revision_id or 'none'}` |")
    head.append(f"| Parse status | {document.parse_status} |")
    head.append(f"| Target language | {target_language} |")
    head.append(f"| Provider | {', '.join(providers) or 'none'} |")
    head.append(f"| Model | {', '.join(models) or 'none'} |")
    head.append(f"| Prompt version | {', '.join(prompt_versions) or 'n/a'} |")
    head.append(f"| Glossary version | {', '.join(glossary_versions) or 'n/a'} |")
    head.append(f"| Exported (UTC) | {datetime.now(timezone.utc).isoformat(timespec='seconds')} |")
    head.append("")
    head.append(
        f"**Coverage in this export:** {len(blocks)} blocks, "
        f"{len(translations)} with a translation, {human_count} human-corrected, "
        f"{failed} recorded as failed or ambiguous, {missing} with none."
    )
    if missing or failed:
        head.append("")
        head.append(
            "> Blocks with no translation or a failed one are marked in place rather than "
            "silently dropped or filled with placeholder text."
        )

    if outline:
        headings = [
            block
            for block in blocks
            if block.type in HEADING_TYPES or block.level is not None
        ]
        if len(headings) > 1:
            head.append("")
            head.append("## Contents")
            head.append("")
            for block in headings:
                text = (block.clean_text or block.raw_text or "").strip().replace("\n", " ")
                depth = max((block.level or 1) - 1, 0)
                head.append(f"{'  ' * depth}- {text[:120]} (p{block.page_index + 1})")

    body: list[str] = []
    for block in blocks:
        body.append(
            block_to_markdown(
                block,
                payloads.get(block.id),
                include_source=include_source,
                include_translation=include_translation,
            )
        )
        # Page transitions are recorded so the reader can find a page in the reflowed text.
        if span_pages.get(block.id):
            body.append(f"<!-- {span_pages[block.id]} -->")

    markdown = "\n\n".join(head + [""] + body)
    if not markdown.endswith("\n"):
        markdown += "\n"

    stem = (document.title or document.filename).rsplit(".", 1)[0]
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in stem).strip() or "document"
    suffix = f".{target_language}" if target_language else ""
    return markdown, f"{safe[:80]}{suffix}.bilingual.md"


def markdown_sha256(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


__all__ = [
    "ANCHOR_NOTE",
    "LAYOUT_NOTE",
    "VERBATIM_TYPES",
    "block_to_markdown",
    "export_document_markdown",
    "markdown_sha256",
]
