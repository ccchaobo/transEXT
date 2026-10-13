from types import SimpleNamespace
from rr_services.assistant import AssistantRequest, tokens
from rr_services.assistant_context import choose_rows, context_strategy


def block(index, kind, text, page=0, section=''):
    return SimpleNamespace(id=f'b{index}',order_index=index,type=kind,clean_text=text,raw_text=text,
                           page_index=page,section_path=[section] if section else [])


def paper():
    rows=[block(0,'title','A scientific research paper')]
    rows += [block(i,'heading',f'{i} Technical Section',page=i%6) for i in range(1,13)]
    rows += [block(20,'abstract','We introduce an efficient model and evaluate its accuracy on longitudinal data.',0,'Abstract'),
             block(21,'paragraph','The research problem is predicting individual progression from medical observations.',1,'1 Introduction'),
             block(22,'paragraph','Our method uses a latent diffusion model with subject metadata conditioning.',3,'4 Methods'),
             block(23,'paragraph','The experiments compare the model against several baselines on held-out datasets.',5,'5 Results'),
             block(24,'paragraph','The conclusion reports improved accuracy but acknowledges limited external validation.',7,'6 Conclusion')]
    return rows


def test_overview_ignores_stale_selected_title_and_contains_real_body_across_sections():
    request=AssistantRequest(question='概括内容',block_id='b0',selected_text='A scientific research paper')
    chosen,mode,body=choose_rows(paper(),request,tokens(request.question),tokens)
    assert mode=='overview'
    assert all(row.type not in {'title','heading'} for row in chosen)
    assert {row.section_path[0] for row in chosen}=={'Abstract','1 Introduction','4 Methods','5 Results','6 Conclusion'}
    assert len(chosen)<=8


def test_related_question_reserves_body_slots_even_when_all_headings_rank_first():
    request=AssistantRequest(question='Technical Section',block_id='b0')
    chosen,_,_=choose_rows(paper(),request,tokens(request.question),tokens)
    assert chosen[0].id=='b0'
    assert sum(row.type in {'paragraph','abstract'} for row in chosen)>=2


def test_local_summary_does_not_expand_to_other_pages_and_explicit_scope_wins():
    request=AssistantRequest(question='概括这段内容',block_id='b22',page_index=3)
    chosen,mode,_=choose_rows(paper(),request,tokens(request.question),tokens)
    assert mode=='selection' and chosen[0].id=='b22'
    assert all(row.page_index==3 for row in chosen)
    assert context_strategy(request.model_copy(update={'context_scope':'document'}))=='overview'


def test_selection_without_anchor_or_text_produces_no_candidates():
    rows,mode,_=choose_rows(paper(),AssistantRequest(question='解释',context_scope='selection'),set(),tokens)
    assert mode=='selection' and not rows
