from pathlib import Path
import httpx
import pytest
from rr_adapters.grobid import parse_tei,GrobidClient,local_endpoint,StructureHint,GrobidResult
from rr_services.grobid_assist import apply_structure
from document_core.layout import Paragraph,LayoutResult
from document_core.coords import BBox

TEI=b"""<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>A sample</title></titleStmt></fileDesc></teiHeader><text><body><div><head coords="2,10,20,80,10" n="2">Methods</head><p coords="2,10,30,80,20">A body paragraph.</p><figure><head>Fake figure heading</head><figDesc>Figure 1. A sample</figDesc></figure></div></body><back><div><listBibl><biblStruct><note type="raw_reference">[1] A. Author 2020</note></biblStruct></listBibl></div></back></text></TEI>"""


def para(text,kind="paragraph",page=1):
    return Paragraph(text,text,[],[page],BBox(.1,.1,.4,.2),12,False,kind=kind)


def test_tei_structure_uses_zero_based_pages_and_ignores_figure_headings():
    result=parse_tei(TEI)
    assert result.title=="A sample"
    assert [h.kind for h in result.hints]==["heading","paragraph","caption","reference"]
    assert result.hints[0].pages==(1,)


@pytest.mark.parametrize("xml",[b"<!DOCTYPE TEI [<!ENTITY a 'secret'>]><TEI/>",b"<html/>" ,b"<TEI>"])
def test_invalid_or_entity_xml_is_rejected(xml):
    with pytest.raises(ValueError):parse_tei(xml)


@pytest.mark.parametrize("url",["https://external.example","http://192.168.1.2:8070","http://127.0.0.1.evil","http://user:password@127.0.0.1:8070","http://127.0.0.1:8070/else"])
def test_only_explicit_local_endpoint_is_allowed(url):
    with pytest.raises(ValueError):local_endpoint(url)


def test_multipart_disables_external_consolidation_and_does_not_retry(tmp_path):
    pdf=tmp_path/"sample.pdf";pdf.write_bytes(b"%PDF sample")
    captured=[]
    def provider(request):
        captured.append(request)
        return httpx.Response(200,content=TEI)
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        result=GrobidClient("http://127.0.0.1:8070/api",client=client).analyse(pdf)
    assert result.title=="A sample"
    assert len(captured)==1
    assert captured[0].content.count(b'name="teiCoordinates"')==3
    assert captured[0].url.path=="/api/processFulltextDocument"
    assert b'name="consolidateHeader"\r\n\r\n0' in captured[0].content
    assert b'name="consolidateCitations"\r\n\r\n0' in captured[0].content


def test_body_matches_fix_heading_without_changing_text_or_boxes():
    paragraphs=[para("Methods"),para("A body paragraph.","heading")]
    original=[(p.text,p.bbox,id(p)) for p in paragraphs]
    layout=LayoutResult(paragraphs,[])
    matched=apply_structure(layout,parse_tei(TEI))
    assert matched=={0:"heading",1:"paragraph"}
    assert [p.kind for p in paragraphs]==["heading","paragraph"]
    assert [(p.text,p.bbox,id(p)) for p in paragraphs]==original
    assert paragraphs[1].section_path==["Methods"]


def test_conflicts_wrong_pages_and_repeated_text_keep_original():
    layout=LayoutResult([para("Methods",page=3),para("Repeated content"),para("Repeated content")],[])
    result=GrobidResult(hints=[StructureHint("heading","Methods",(1,)),StructureHint("heading","Repeated content")])
    assert apply_structure(layout,result)=={}


def test_grobid_cannot_remove_explicit_reference_boundary():
    p=para("References","heading")
    layout=LayoutResult([p,para("A body paragraph.")],[])
    apply_structure(layout,GrobidResult(hints=[StructureHint("paragraph","References")]))
    assert p.kind=="heading"
    assert layout.paragraphs[1].section_path==["References"]


def test_same_page_duplicates_with_coordinates_are_still_ambiguous():
    layout=LayoutResult([para("Repeated content"),para("Repeated content")],[])
    assert apply_structure(layout,GrobidResult(hints=[StructureHint("heading","Repeated content",(1,))]))=={}
