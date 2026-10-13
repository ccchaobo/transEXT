"""Choose bounded evidence by reading intent, rather than filling slots with headings."""
import re

BODY_TYPES = {'paragraph', 'abstract', 'list_item'}
MAX_SOURCES = 8
MAX_CONTEXT_CHARS = 18000
MAX_SOURCE_CHARS = 2500


def is_body(row):
    minimum = 80 if row.type == 'list_item' else 20
    return row.type in BODY_TYPES and len((row.clean_text or row.raw_text or '').strip()) >= minimum


def context_strategy(request):
    summary = request.kind == 'question' and bool(re.search(
        r'概括|总结|概述|综述|梳理|归纳|summari[sz]e|summary|overview', request.question, re.I))
    local = bool(re.search(r'这段|本段|该段|这部分|该部分|本节|这节|当前段落|选中|选区|划词|所选|这页|本页|当前页|这张图|该图|这个表|参考文献|this (?:paragraph|passage|page)|selected', request.question, re.I))
    if request.context_scope == 'selection':
        return 'selection'
    if request.context_scope == 'document':
        return 'overview' if summary else 'document'
    if summary and not local:
        return 'overview'
    if (local or request.kind in {'term', 'formula'}) and (request.block_id or request.selected_text):
        return 'selection'
    return 'related'


def choose_rows(candidates, request, query, tokenize):
    strategy = context_strategy(request)
    body = [row for row in candidates if is_body(row)]
    selected = next((row for row in candidates if row.id == request.block_id), None)
    if strategy == 'selection' and not (selected or request.selected_text.strip()):
        return [], strategy, body
    whole = strategy in {'overview', 'document'}
    if strategy == 'selection':
        page = selected.page_index if selected else request.page_index
        candidates = [row for row in candidates if row.page_index == page]
        body = [row for row in candidates if is_body(row)]

    def rank(row):
        score = len(query & tokenize(row.clean_text or row.raw_text or ''))
        if row == selected and not whole:
            score += 10000
        if request.kind == 'formula' and row.type == 'formula':
            score += 5
        if row.page_index == request.page_index and not whole:
            score += .2
        return -score, row.order_index

    chosen = []
    def add(row):
        if row is not None and row not in chosen and len(chosen) < MAX_SOURCES:
            chosen.append(row)

    if strategy == 'overview':
        labels = {}; heading = ''
        for row in candidates:
            if row.type == 'heading':
                heading = row.clean_text or row.raw_text or ''
            labels[row.id] = re.sub(r'\s+', '', ' '.join([*(row.section_path or []), heading])).lower()
        sections = [r'abstract|摘要', r'introduction|background|引言|绪论|背景',
                    r'method|approach|framework|方法|模型|技术路线',
                    r'result|experiment|evaluation|结果|实验|评估',
                    r'conclusion|discussion|limitation|结论|讨论|局限']
        for index, pattern in enumerate(sections):
            rows = [row for row in body if re.search(pattern, labels[row.id]) or (index == 0 and row.type == 'abstract')]
            if rows:
                add(min(rows, key=rank))
        # Also cover the document's extent when section names were not recognised.
        if body:
            for fraction in [0, 1, .5, .25, .75]:
                add(body[round((len(body)-1)*fraction)])
        for row in sorted(body, key=rank):
            add(row)
    else:
        if not whole:
            add(selected)
            if selected and selected.type in {'heading', 'title'}:
                add(next((row for row in body if row.order_index > selected.order_index), None))
        # Metadata or section headings must not consume every evidence slot.
        for row in sorted(body, key=rank):
            if sum(is_body(item) for item in chosen) >= min(2, len(body)):
                break
            add(row)
        pool = body if strategy == 'document' else candidates
        for row in sorted(pool, key=rank):
            add(row)
    return chosen, strategy, body
