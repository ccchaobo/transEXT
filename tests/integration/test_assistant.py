import httpx
from rr_services.assistant import AssistantRequest, prepare_context, answer_question, AssistantError
from rr_adapters.translation import OpenAITranslationConfig
from test_api import client, import_and_parse


def test_assistant_refuses_without_configuration(client):
    doc = import_and_parse(client)
    reply = client.post(f"/api/v1/documents/{doc}/assistant", json={"question": "本文的方法是什么？"})
    assert reply.status_code == 409
    assert reply.json()["detail"]["error_code"] == "translation_not_configured"


def test_context_is_bounded_and_selected_block_has_priority(client):
    doc = import_and_parse(client)
    block = client.get(f"/api/v1/documents/{doc}/pages/1").json()["blocks"][0]
    request = AssistantRequest(question="这个术语在本文中的含义", block_id=block["id"], kind="term")
    with client.app.state.database.session() as session:
        context = prepare_context(session, doc, request)
    assert context["sources"][0]["block_id"] == block["id"]
    assert len(context["sources"]) <= 8
    assert sum(len(source["text"]) for source in context["sources"]) <= 18000
    assert context["sources"][0]["page_number"] == 2


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
