"""翻译范围规则：独立编号和图内文字保留原样。"""
import re


def is_number_label(text: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)*\.?", text.strip()))


def inside_graphic(box, regions) -> bool:
    area = max(0, box[2]-box[0]) * max(0, box[3]-box[1])
    if not area:
        return False
    return any(max(0, min(box[2], r[2])-max(box[0], r[0])) *
               max(0, min(box[3], r[3])-max(box[1], r[1])) > area * .3
               for r in regions)


def graphic_text(spans, regions_by_page) -> bool:
    # 混合正文/图内文字的合并块不能整段跳过，避免正文丢译。
    return bool(spans) and all(inside_graphic(s.bbox, regions_by_page.get(s.page_index, [])) for s in spans)
