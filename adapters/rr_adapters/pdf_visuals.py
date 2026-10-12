"""读取原页图片及矢量图形区域，用于重排时避免切开图表。"""
from functools import lru_cache
from pathlib import Path
import re

import pymupdf


@lru_cache(maxsize=128)
def _regions(path: str, mtime: int, size: int, index: int) -> tuple:
    with pymupdf.open(path) as doc:
        page = doc[index]
        frame = page.rect
        rects = []
        images = []
        for item in page.get_image_info():
            box = pymupdf.Rect(item["bbox"])
            # 多幅小图组成同一插图：合并相邻画面，包含行间的样本/模型标签。
            changed = True
            while changed:
                changed = False
                for other in list(images):
                    x_gap = max(0, max(box.x0, other.x0)-min(box.x1, other.x1))
                    y_gap = max(0, max(box.y0, other.y0)-min(box.y1, other.y1))
                    if x_gap <= 8 and y_gap <= 24:
                        box |= other
                        images.remove(other)
                        changed = True
            images.append(box)
        # 小图顶部的短标签一般位于画面上方；保留它们，图外图注仍单独翻译。
        rects.extend(box + (-2, -10, 2, 2) for box in images)
        horizontal_rules = []
        for drawing in page.get_drawings():
            rect = pymupdf.Rect(drawing["rect"])
            # 忽略整页背景和边框，不能把整页正文误当成图内文字。
            if rect.width * rect.height > page.cropbox.width * page.cropbox.height * .85:
                continue
            if drawing.get("fill") and all(value > .97 for value in drawing["fill"]):
                continue
            if rect.width > 8 or rect.height > 8:
                rects.append(rect + (-2, -2, 2, 2))
            if rect.width > 40 and rect.height < 2:
                horizontal_rules.append(rect)
        # 三线表通常没有竖线：用同宽对齐横线及数值单元格确认整表，不能仅保护细线。
        groups = []
        for rule in sorted(horizontal_rules, key=lambda r: r.y0):
            group = next((g for g in groups if abs(g[-1].x0-rule.x0)<4 and
                          abs(g[-1].x1-rule.x1)<4 and rule.y0-g[-1].y1<=min(90,max(24,rule.width*.3)) and rule.y0-g[0].y0<=200), None)
            if group is None:
                groups.append([rule])
            else:
                group.append(rule)
        words = page.get_text("words")
        for group in groups:
            if len(group)<3:
                continue
            table = pymupdf.Rect(group[0].x0,group[0].y0,group[0].x1,group[-1].y1)
            numeric = [w for w in words if table.x0<=w[0] and w[2]<=table.x1 and
                       table.y0-2<=w[1] and w[3]<=table.y1+2 and
                       re.fullmatch(r"[+-]?\d+(?:\.\d+)?", w[4])]
            rows = {round(w[1]/4) for w in numeric}
            columns = {round(w[0]/10) for w in numeric}
            if len(numeric)>=4 and len(rows)>=2 and len(columns)>=2:
                rects.append(table + (-2,-2,2,2))
        boxes = []
        for rect in rects:
            rect = (rect * page.rotation_matrix) & frame
            if rect.width <= 0 or rect.height <= 0:
                continue
            box = [max(0,rect.x0/frame.width), max(0,rect.y0/frame.height),
                   min(1,rect.x1/frame.width), min(1,rect.y1/frame.height)]
            # 合并相交的图形路径，保留一张图或一个表的整体范围。
            merged = True
            while merged:
                merged = False
                for other in list(boxes):
                    if box[0] <= other[2] and other[0] <= box[2] and box[1] <= other[3] and other[1] <= box[3]:
                        box = [min(box[0],other[0]),min(box[1],other[1]),max(box[2],other[2]),max(box[3],other[3])]
                        boxes.remove(other)
                        merged = True
            boxes.append(box)
        return tuple(tuple(box) for box in boxes)


def visual_regions(path: Path, page_index: int) -> list[list[float]]:
    stat = path.stat()
    return [list(box) for box in _regions(str(path), stat.st_mtime_ns, stat.st_size, page_index)]
