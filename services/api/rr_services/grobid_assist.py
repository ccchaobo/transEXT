"""把 TEI 结构与既有段落对齐；不复制 TEI 文字、不移动坐标或更换段落 id。"""
import re
import unicodedata
from document_core.layout import build_sections
from document_core.blocks import is_reference_section_heading


def normalise(text):
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC",text).casefold())


def apply_structure(layout,result):
    matched={}
    for index,paragraph in enumerate(layout.paragraphs):
        key=normalise(paragraph.text)
        if len(key)<4:
            continue
        hints=[h for h in result.hints if normalise(h.text)==key and
               (not h.pages or set(h.pages)&set(paragraph.page_indices))]
        # 没有页码的重复文字不作判断；不同结构冲突时保留原分类。
        if not hints or len({h.kind for h in hints})!=1:
            continue
        candidates = [p for p in layout.paragraphs if normalise(p.text)==key and
                      any(not h.pages or set(h.pages)&set(p.page_indices) for h in hints)]
        if len(candidates)!=1:
            continue
        kind=hints[0].kind
        matched[index]=kind
        if kind in {"heading","paragraph","abstract","caption"}:
            # 显式 References 边界不能被外部结构证据取消。
            if not is_reference_section_heading(paragraph.text):
                paragraph.kind="heading" if kind=="heading" else "paragraph"
                paragraph.level=None
    if matched:
        sections,_,_=build_sections(layout.paragraphs)
        layout.evidence["sections"]=sections
    return matched
