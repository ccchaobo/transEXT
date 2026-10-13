"""Return every readable block in reading order; never silently substitute excerpts."""
import json

BODY_TYPES = {'paragraph', 'abstract', 'list_item'}
# Resource protection only: exceeding it rejects the entire request, never truncates it.
MAX_DOCUMENT_CHARS = 2_000_000


def is_body(row):
    return row.type in BODY_TYPES and bool(source_text(row).strip())


def source_text(row):
    clean = row.clean_text or ''
    text = clean if clean.strip() else row.raw_text or ''
    table = getattr(row, 'table_data', None)
    if table:
        text += ('\n\n' if text else '') + '表格结构：\n' + json.dumps(table, ensure_ascii=False)
    return text


def context_strategy(request):
    # Kept as a compatibility entry point for callers of the previous implementation.
    return 'full_document'


def choose_rows(candidates, request, query, tokenize):
    chosen = [row for row in candidates if source_text(row).strip()]
    return chosen, 'full_document', [row for row in chosen if is_body(row)]
