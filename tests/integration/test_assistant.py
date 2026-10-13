import httpx
from rr_services.assistant import AssistantRequest, prepare_context, answer_question, AssistantError
from rr_adapters.translation import OpenAITranslationConfig
from test_api import client, import_and_parse


def test_assistant_refuses_without_configuration(client):
    doc = import_and_parse(client)
    reply = client.post(f"/api/v1/documents/{doc}/assistant", json={"question": "本文的方法是什么？"})
    assert reply.status_code == 409
    assert reply.json()["detail"]["error_code"] == "translation_not_configured"


def test_context_is_complete_and_selected_block_does_not_limit_scope(client):
    doc = import_and_parse(client)
    block = client.get(f"/api/v1/documents/{doc}/pages/1").json()["blocks"][0]
    request = AssistantRequest(question="这个术语在本文中的含义", block_id=block["id"], kind="term")
    with client.app.state.database.session() as session:
        context = prepare_context(session, doc, request)
    assert block["id"] in {source['block_id'] for source in context['sources']}
    assert context['context_summary']['strategy']=='full_document'
    assert context['context_summary']['complete'] and not context['context_summary']['excerpts']
    assert {source['page_number'] for source in context['sources']}=={1,2}


def test_foreign_block_is_rejected_before_service_call(client):
    doc = import_and_parse(client)
    reply = client.post(f"/api/v1/documents/{doc}/assistant", json={"question":"解释", "block_id":"foreign"})
    assert reply.status_code == 400


def test_answer_uses_chat_protocol_and_only_validates_supplied_citations(client):
    doc = import_and_parse(client)
    request = AssistantRequest(question="解释公式", kind="formula")
    with client.app.state.database.session() as session:
        context = prepare_context(session, doc, request)
    sent = []
    def provider(incoming):
        import json
        sent.append(json.loads(incoming.content))
        return httpx.Response(200, json={"choices":[{"message":{"content":"本文解释 [S1]。未知引文 [S99]。"}}],
            "usage":{"total_tokens":12}})
    config = OpenAITranslationConfig(base_url="https://example.invalid/v1", api_key="test-secret", model="test-model")
    with httpx.Client(transport=httpx.MockTransport(provider)) as transport:
        result = answer_question(config, context, request, client=transport)
    assert sent[0]["model"] == "test-model"
    assert "公式" in sent[0]["messages"][0]["content"]
    assert result["citations"][0]["id"] == "S1"
    assert all(source["id"] != "S99" for source in result["citations"])
    assert result["warnings"]
    assert "test-secret" not in str(result)


def test_timeout_has_no_automatic_retry(client):
    config = OpenAITranslationConfig(base_url="https://example.invalid/v1", api_key="x", model="x")
    calls = []
    def provider(incoming):
        calls.append(incoming)
        raise httpx.ReadTimeout("timeout", request=incoming)
    with httpx.Client(transport=httpx.MockTransport(provider)) as transport:
        import pytest
        with pytest.raises(AssistantError, match="未自动重试"):
            answer_question(config, {"title":"sample", "sources":[], "revision_id":"r"},
                AssistantRequest(question="解释"), client=transport)
    assert len(calls) == 1


def test_configured_route_uses_saved_translation_configuration(client):
    doc = import_and_parse(client)
    assert client.put("/api/v1/settings/translation", json={"base_url":"https://example.invalid/v1",
        "model":"qa-model", "api_key":"qa-test-secret"}).status_code == 200
    captured = []
    def provider(incoming):
        captured.append(incoming)
        return httpx.Response(200, json={"choices":[{"message":{"content":"依据文献回答 [S1]。"}}]})
    with httpx.Client(transport=httpx.MockTransport(provider)) as transport:
        client.app.state.assistant_client = transport
        response = client.post(f"/api/v1/documents/{doc}/assistant", json={"question":"Reading order", "kind":"term"})
    assert response.status_code == 200
    assert response.json()["model"] == "qa-model"
    assert response.json()["citations"][0]["block_id"]
    assert captured[0].headers["Authorization"] == "Bearer qa-test-secret"
    assert "qa-test-secret" not in response.text


def test_thinking_only_cutoff_is_reported_without_exposing_reasoning_or_retry():
    import pytest
    calls=[]
    def provider(incoming):
        calls.append(incoming)
        return httpx.Response(200,json={"choices":[{"finish_reason":"length",
            "message":{"content":"", "reasoning_content":"private thinking"}}]})
    cfg=OpenAITranslationConfig(base_url="https://example.invalid/v1",api_key="x",model="x")
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises(AssistantError) as exc:
            answer_question(cfg,{"title":"sample","sources":[],"revision_id":"r"},AssistantRequest(question="解释"),client=client)
    assert exc.value.code=="assistant_output_limit"
    assert "private thinking" not in str(exc.value)
    assert len(calls)==1


def test_text_parts_and_truncated_answer_warning():
    def provider(incoming):
        return httpx.Response(200,json={"choices":[{"finish_reason":"length", "message":{
            "content":[{"type":"text","text":"正式回答"},{"type":"reasoning","text":"private"}]}}]})
    cfg=OpenAITranslationConfig(base_url="https://example.invalid/v1",api_key="x",model="x")
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        result=answer_question(cfg,{"title":"sample","sources":[],"revision_id":"r"},AssistantRequest(question="解释"),client=client)
    assert result["answer"]=="正式回答"
    assert any("不完整" in w for w in result["warnings"])


def test_ollama_budget_leaves_room_for_answer_with_bounded_thinking():
    import json
    sent=[]
    def provider(incoming):
        sent.append(json.loads(incoming.content))
        return httpx.Response(200,json={"choices":[{"message":{"content":"回答"}}]})
    cfg=OpenAITranslationConfig(base_url="https://ollama.com/v1",api_key="x",model="deepseek-v4.1-flash")
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        answer_question(cfg,{"title":"sample","sources":[],"revision_id":"r"},AssistantRequest(question="解释"),client=client)
    assert sent[0]["max_tokens"]==8192
    assert sent[0]["reasoning_effort"]=="low"


def test_summary_context_contains_all_types_including_selected_title(client):
    from rr_storage import Block,Document
    from sqlalchemy import select
    doc=import_and_parse(client)
    with client.app.state.database.session() as session:
        current=session.get(Document,doc)
        rows=list(session.scalars(select(Block).where(Block.revision_id==current.active_revision_id)))
        selected=next(row for row in rows if row.type in {'title','heading'})
        context=prepare_context(session,doc,AssistantRequest(question='概括内容',block_id=selected.id,selected_text=selected.clean_text))
    assert context['context_summary']['strategy']=='full_document'
    assert context['context_summary']['body_selected']>0
    assert selected.id in {source['block_id'] for source in context['sources']}
    assert len(context['sources'])==len([row for row in rows if (row.clean_text or row.raw_text).strip() or row.table_data])
    assert not any(source['truncated'] for source in context['sources'])


def test_heading_only_parse_is_reported_as_complete_available_text(client):
    from rr_storage import Block,Document
    from sqlalchemy import select
    doc=import_and_parse(client)
    client.put('/api/v1/settings/translation',json={'base_url':'https://example.invalid/v1','model':'qa-fixture','api_key':'fixture-only'})
    with client.app.state.database.session() as session:
        current=session.get(Document,doc)
        for row in session.scalars(select(Block).where(Block.revision_id==current.active_revision_id)):
            row.type='heading'
    calls=[]
    def provider(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'本次解析只提供标题类文字，缺少可确认的方法细节。'}}]})
    with httpx.Client(transport=httpx.MockTransport(provider)) as transport:
        client.app.state.assistant_client=transport
        reply=client.post(f'/api/v1/documents/{doc}/assistant',json={'question':'概括全文'})
    assert reply.status_code==200 and len(calls)==1
    assert reply.json()['context_summary']['body_available']==0
    assert reply.json()['context_summary']['complete']


def test_full_document_guard_refuses_entire_request_without_provider_call(client,monkeypatch):
    doc=import_and_parse(client)
    monkeypatch.setattr('rr_services.assistant.MAX_DOCUMENT_CHARS',20)
    client.put('/api/v1/settings/translation',json={'base_url':'https://example.invalid/v1','model':'fixture','api_key':'fixture'})
    calls=[]
    def provider(request):calls.append(request);return httpx.Response(200,json={})
    with httpx.Client(transport=httpx.MockTransport(provider)) as transport:
        client.app.state.assistant_client=transport
        response=client.post(f'/api/v1/documents/{doc}/assistant',json={'question':'概括全文'})
    assert response.status_code==413 and response.json()['detail']['error_code']=='full_document_too_large'
    assert not calls


def test_readonly_context_endpoint_and_legacy_local_field_use_same_full_text(client):
    doc=import_and_parse(client)
    reply=client.get(f'/api/v1/documents/{doc}/assistant/context')
    assert reply.status_code==200 and reply.json()['complete']
    with client.app.state.database.session() as session:
        context=prepare_context(session,doc,AssistantRequest(question='解释这段',context_scope='selection',page_index=1))
    assert context['context_summary']['source_count']==reply.json()['source_count']
    assert context['context_summary']['pages']==[1,2]


def test_provider_context_limit_is_reported_without_excerpt_retry(client):
    doc=import_and_parse(client)
    with client.app.state.database.session() as session:context=prepare_context(session,doc,AssistantRequest(question='解释'))
    config=OpenAITranslationConfig(base_url='https://example.invalid/v1',api_key='fixture',model='fixture')
    calls=[]
    def provider(request):calls.append(request);return httpx.Response(400,json={'error':{'code':'context_length_exceeded','message':'secret internal details'}})
    import pytest
    with httpx.Client(transport=httpx.MockTransport(provider)) as transport:
        with pytest.raises(AssistantError) as error:answer_question(config,context,AssistantRequest(question='解释'),client=transport)
    assert error.value.code=='assistant_context_limit' and 'secret' not in str(error.value) and len(calls)==1
