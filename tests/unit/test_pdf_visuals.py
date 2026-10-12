from pathlib import Path

import pymupdf
import numpy as np
import pytest
from rr_adapters.pdf_visuals import visual_regions


@pytest.mark.parametrize("rotation", [0,90,180,270])
@pytest.mark.parametrize("cropped", [False,True])
def test_vector_region_uses_display_coordinates(tmp_path: Path, rotation: int, cropped: bool):
    target = tmp_path / f"vector-{rotation}-{cropped}.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=300,height=400)
        page.draw_rect(pymupdf.Rect(80,120,160,220), color=(0,0,1), fill=(0,0,1))
        if cropped:
            page.set_cropbox(pymupdf.Rect(30,40,280,360))
        page.set_rotation(rotation)
        doc.save(target)
    regions = visual_regions(target,0)
    assert regions
    with pymupdf.open(target) as doc:
        page=doc[0]
        pixels=page.get_pixmap(alpha=False)
        rgb=np.frombuffer(pixels.samples,dtype=np.uint8).reshape(pixels.height,pixels.width,pixels.n)
        rows,cols=np.where((rgb[:,:,2]>200)&(rgb[:,:,0]<50)&(rgb[:,:,1]<50))
        assert len(rows)>100
        x,y=(cols.mean()+.5)/pixels.width,(rows.mean()+.5)/pixels.height
    assert any(box[0] <= x <= box[2] and box[1] <= y <= box[3] for box in regions)


def test_page_background_does_not_swallow_all_text(tmp_path: Path):
    target=tmp_path/'background.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=300,height=400)
        page.draw_rect(page.rect,color=None,fill=(1,1,1))
        page.insert_text((40,60),'A readable paper')
        doc.save(target)
    assert visual_regions(target,0)==[]


def test_composite_images_include_interleaved_labels(tmp_path):
    import io
    from PIL import Image
    from rr_adapters.pdf_visuals import visual_regions
    pdf = tmp_path / "composite.pdf"
    stream = io.BytesIO()
    Image.new("RGB", (30, 30), "red").save(stream, format="PNG")
    with pymupdf.open() as doc:
        page = doc.new_page(width=600,height=800)
        for y in [160,240,320]:
            for x in [100,250]:
                page.insert_image(pymupdf.Rect(x,y,x+148,y+60),stream=stream.getvalue())
            page.insert_text((200,y-5),"Sample 1",fontsize=8)
        doc.save(pdf)
    regions = visual_regions(pdf,0)
    assert len(regions)==1
    box=regions[0]
    assert box[1]<=150/800 and box[3]>=380/800


def test_numeric_table_rules_protect_whole_cells_but_not_caption(tmp_path):
    pdf=tmp_path / "three-line-table.pdf"
    with pymupdf.open() as doc:
        page=doc.new_page(width=600,height=800)
        page.insert_text((100,270),"Table 1. Evaluation",fontsize=10)
        for y in [300,320,380]:
            page.draw_line((100,y),(400,y))
        for y in [340,360]:
            page.insert_text((105,y),"Real images",fontsize=10)
            page.insert_text((230,y),"0.0231",fontsize=10)
            page.insert_text((320,y),"0.9997",fontsize=10)
        doc.save(pdf)
    boxes=visual_regions(pdf,0)
    assert any(b[0]<=100/600 and b[2]>=400/600 and b[1]<=300/800 and b[3]>=380/800 for b in boxes)
    assert all(b[1]>270/800 for b in boxes)


def test_repeated_rules_without_table_cells_do_not_protect_body(tmp_path):
    pdf=tmp_path / "rules.pdf"
    with pymupdf.open() as doc:
        page=doc.new_page(width=600,height=800)
        for y in [300,320,340]:
            page.draw_line((100,y),(400,y))
            page.insert_text((100,y-5),"An ordinary sentence with 2026 in it",fontsize=10)
        doc.save(pdf)
    assert all(b[3]-b[1]<.02 for b in visual_regions(pdf,0))
