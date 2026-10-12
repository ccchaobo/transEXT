"""Regression: an arXiv margin stamp must not destroy abstract columns."""
import pymupdf
from rr_adapters.pdf_extract import PdfTextExtractor
from document_core.layout import analyse
from document_core.blocks import body_font_size
from document_core.layout import Paragraph
from document_core.coords import BBox


def test_rotated_margin_stamp_is_preserved_outside_body_flow(tmp_path):
    path=tmp_path/'margin.pdf'
    doc=pymupdf.open()
    page=doc.new_page(width=600,height=800)
    page.insert_text((20,550),'arXiv:2502.08560v2 [cs.CV] 13 Jul 2025',fontsize=18,rotate=90)
    left=['Keywords:','Disease Progression','Spatiotemporal Models','Generative Models']
    right=['The growing availability of longitudinal MRI datasets has',
           'facilitated modeling of disease progression and makes it possible',
           'to predict future scans for individual patients with consistency.',
           'Our model integrates metadata and prior knowledge.']
    for i,text in enumerate(left):page.insert_text((65,240+i*14),text,fontsize=10)
    for i,text in enumerate(right):page.insert_text((245,240+i*14),text,fontsize=10)
    doc.save(path);doc.close()
    extraction=PdfTextExtractor().extract(path)
    result=analyse(extraction.lines_by_page)
    keywords=next(p for p in result.paragraphs if p.text.startswith('Keywords:'))
    abstract=next(p for p in result.paragraphs if p.text.startswith('The growing'))
    assert len(keywords.lines)==1
    assert len([p for p in result.paragraphs if p.kind=='list_item'])==3
    assert len(abstract.lines)==4
    assert 'Disease Progression' not in abstract.text
    assert all(line.bbox.x0>.35 for line in abstract.lines)
    assert any('arXiv:' in p.text for p in result.furniture)
    assert sum(len(p.lines) for p in result.paragraphs+result.furniture)==len(extraction.all_lines)


def test_dense_table_cells_do_not_set_body_font():
    def paragraph(text,size):
        return Paragraph(text=text,raw_text=text,lines=[],page_indices=[0],
                         bbox=BBox(.1,.1,.8,.2),font_size=size,kind='paragraph',bold=False)
    cells=[paragraph('0.004 ± 0.002',5.8) for _ in range(200)]
    prose=[paragraph('The longitudinal model predicts individual progression. '*8,10)]
    assert body_font_size(cells+prose)==10


def test_page_continuations_are_independently_anchored():
    from document_core.layout import TextLine, LayoutParams, _should_merge
    a=TextLine(text='We observe the same progression in all patients and',
               bbox=BBox(.1,.85,.9,.87),page_index=0,font_size=10)
    b=TextLine(text='patients exhibit the same progression over time.',
               bbox=BBox(.1,.08,.9,.10),page_index=1,font_size=10)
    assert not _should_merge([a],[b],LayoutParams())
