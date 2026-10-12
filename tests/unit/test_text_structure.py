from types import SimpleNamespace
import pytest
from document_core.text_structure import structure,translation_input,translation_parts
from document_core.navigation import navigation_level
from document_core.layout import TextLine,LayoutParams,assemble_paragraphs
from document_core.coords import BBox


def test_wrapped_list_and_nested_indentation_are_separate_from_visual_lines():
    raw='Introduction\n(1) First item with\nwrapped continuation.\n(a) Nested evidence.\n(2) Second item.'
    texts=raw.splitlines();offset=0;spans=[]
    for i,text in enumerate(texts):
        spans.append(SimpleNamespace(ordinal=i,text_start=offset,text_end=offset+len(text),bbox=[.16 if i==3 else .1,.1+i*.03,.7,.12+i*.03]));offset+=len(text)+1
    value=structure(raw,spans)
    assert [p['marker'] for p in value['parts']]==['','(1)','(a)','(2)']
    assert value['parts'][1]['text']=='First item with wrapped continuation.'
    assert value['parts'][2]['indent_em']>value['parts'][1]['indent_em']
    translated=translation_parts(value,'引言 （1）第一点。（a）嵌套证据。（2）第二点。')
    assert translated['preserved'] and len(translated['parts'])==4
    assert translated['parts'][2]['indent_em']==value['parts'][2]['indent_em']
    source=translation_input(raw,spans)
    assert '\n(1)' in source and '\n    (a)' in source


def test_missing_marker_is_reported_instead_of_inventing_an_item():
    value=structure('• First\n• Second')
    result=translation_parts(value,'第一项和第二项。')
    assert not result['preserved'] and result['parts'][0]['text']=='第一项和第二项。'


def test_heading_number_is_not_rewritten_as_a_list():
    assert not structure('1. Introduction',block_type='heading')['has_list']
    assert not structure('The model has four contributions: (i) speed; (ii) stability.')['has_list']


def test_list_starts_break_paragraphs_but_wrapped_lines_stay_together():
    lines=[TextLine('(i) The first item continues',BBox(.1,.1,.8,.12),font_size=10),
        TextLine('on this next visual line.',BBox(.14,.123,.8,.143),font_size=10),
        TextLine('(ii) The second item.',BBox(.1,.146,.8,.166),font_size=10)]
    result=assemble_paragraphs([(0,i,line) for i,line in enumerate(lines)],{0:lines},LayoutParams())
    assert len(result)==2 and all(p.kind=='list_item' for p in result)
    assert 'next visual line' in result[0].text


@pytest.mark.parametrize('text',['Method','E','Image-based metrics','0.004 ± 0.002*','20 (A) Age Distribution',
    '1 −βtxt−1, βtI), where βtfollows a variance','A B S T R A C T arXiv:2502.08560v2','Figure 3. Predictions'])
def test_body_table_and_graph_labels_are_not_navigation(text):assert navigation_level(text) is None


def test_spaced_article_metadata_is_not_an_appendix():
    assert navigation_level('A R T I C L E I N F O') is None
    assert navigation_level('A B S T R A C T')==1


@pytest.mark.parametrize('text,level',[('1. Introduction',1),('4.2. ControlNet - conditioning on subject brain MRI',2),
    ('5.6.1. Global-level uncertainty increases with prediction distance',3),('References',1),('A. Auxiliary models',1),('A.2. Disease Course Mapping (DCM)',2)])
def test_real_numbered_and_appendix_headings_remain_navigation(text,level):assert navigation_level(text)==level
