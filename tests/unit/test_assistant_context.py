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


def test_full_document_includes_titles_headings_and_all_body_across_sections():
    request=AssistantRequest(question='概括内容',block_id='b0',selected_text='A scientific research paper')
    chosen,mode,body=choose_rows(paper(),request,tokens(request.question),tokens)
    assert mode=='full_document'
    assert chosen==paper() and len(chosen)>8
    assert {row.section_path[0] for row in body}=={'Abstract','1 Introduction','4 Methods','5 Results','6 Conclusion'}


def test_question_keywords_do_not_change_or_reorder_full_document():
    request=AssistantRequest(question='Technical Section',block_id='b0')
    chosen,_,_=choose_rows(paper(),request,tokens(request.question),tokens)
    assert chosen==paper()


def test_legacy_local_scope_still_provides_every_page():
    request=AssistantRequest(question='概括这段内容',block_id='b22',page_index=3)
    chosen,mode,_=choose_rows(paper(),request,tokens(request.question),tokens)
    assert mode=='full_document' and chosen==paper()
    assert context_strategy(request.model_copy(update={'context_scope':'selection'}))=='full_document'


def test_legacy_selection_without_anchor_still_provides_full_document():
    rows,mode,_=choose_rows(paper(),AssistantRequest(question='解释',context_scope='selection'),set(),tokens)
    assert mode=='full_document' and rows==paper()


def test_headers_references_long_blocks_and_structured_tables_are_not_filtered():
    from rr_services.assistant_context import source_text
    rows=[block(index,kind,'content '+kind) for index,kind in enumerate(['title','author','heading','paragraph','caption','formula','reference','header','footer','page_number'])]
    rows.append(block(10,'paragraph','LONG '*4000+'END'))
    table=block(11,'table','');table.table_data={'headers':['Method','MAE'],'rows':[['A',0.01]]};rows.append(table)
    chosen,mode,_=choose_rows(rows,AssistantRequest(question='方法'),set(),tokens)
    assert chosen==rows and source_text(chosen[-2]).endswith('END')
    assert len(source_text(chosen[-2]))>18000 and 'MAE' in source_text(chosen[-1])


def test_whitespace_only_clean_text_does_not_hide_readable_raw_text():
    from rr_services.assistant_context import source_text
    row=block(1,'paragraph','Readable original');row.clean_text=' \n'
    assert source_text(row)=='Readable original'
