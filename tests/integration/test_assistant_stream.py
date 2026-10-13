import asyncio
import json
import httpx
from rr_services.assistant import AssistantRequest
from rr_services.assistant_stream import stream_answer,PublicChannels
from rr_adapters.translation import OpenAITranslationConfig
from test_api import client,import_and_parse

CONTEXT={'title':'Paper','revision_id':'revision','sources':[{'id':'S1','page_number':1,'page_index':0,'block_id':'b','text':'Method evidence'}]}
CONFIG=OpenAITranslationConfig(base_url='https://ollama.com/v1',api_key='secret',model='deepseek-v4.1-flash')


def frames(*values):return ''.join('data: '+json.dumps(value,ensure_ascii=False)+'\n\n' for value in values)+'data: [DONE]\n\n'


def test_public_markers_can_be_split_across_arbitrary_chunks():
    channels=PublicChannels();events=[]
    for text in ['[[摘','要]]证据来自 S1。[[回','答]]正式回答。']:events.extend(channels.feed(text))
    assert channels.summary=='证据来自 S1。' and channels.answer=='正式回答。'
    assert all('[[' not in event[1]['text'] for event in events)


def test_stream_exposes_public_summary_answer_usage_but_no_raw_reasoning():
    sent=[]
    def provider(incoming):
        sent.append(json.loads(incoming.content))
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=frames(
            {'choices':[{'delta':{'reasoning_content':'private raw chain'}}]},
            {'choices':[{'delta':{'content':'[[摘要]]根据给出的证据。[[回答]]'}}]},
            {'choices':[{'delta':{'content':'正式回答 [S1]。'},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'completion_tokens_details':{'reasoning_tokens':7}}}))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as c:
            return [event async for event in stream_answer(CONFIG,CONTEXT,AssistantRequest(question='解释',reasoning_effort='high'),client=c)]
    events=asyncio.run(run());result=next(data for kind,data in events if kind=='done')
    assert result['answer']=='正式回答 [S1]。' and result['summary']=='根据给出的证据。'
    assert result['metrics']['reasoning_observed'] and result['citations'][0]['id']=='S1'
    assert 'private raw chain' not in str(events) and 'secret' not in str(events)
    assert sent[0]['stream'] and sent[0]['reasoning_effort']=='high'


def test_failure_after_first_answer_keeps_partial_events_and_never_retries():
    calls=[]
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadError('private transport error')
    def provider(incoming):calls.append(incoming);return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=Broken())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as c:
            return [event async for event in stream_answer(CONFIG,CONTEXT,AssistantRequest(question='解释',include_summary=False),client=c)]
    events=asyncio.run(run())
    assert any(kind=='delta' and value['text']=='partial' for kind,value in events)
    assert events[-1][0]=='error' and events[-1][1]['partial'] and len(calls)==1


def test_closing_consumer_closes_upstream_stream():
    closed=[]
    class Live(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            await asyncio.sleep(60)
        async def aclose(self):closed.append(True)
    def provider(incoming):return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=Live())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as c:
            generator=stream_answer(CONFIG,CONTEXT,AssistantRequest(question='解释',include_summary=False),client=c)
            async for kind,value in generator:
                if kind=='delta':break
            await generator.aclose()
    asyncio.run(run());assert closed


def test_sse_route_and_capability_payload(client):
    doc=import_and_parse(client)
    client.put('/api/v1/settings/translation',json={'base_url':'https://ollama.com/v1','api_key':'secret','model':'deepseek-v4.1-flash'})
    def provider(incoming):return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=frames({'choices':[{'delta':{'content':'回答 [S1]。'},'finish_reason':'stop'}]}))
    c=httpx.AsyncClient(transport=httpx.MockTransport(provider));client.app.state.assistant_async_client=c
    try:
        response=client.post(f'/api/v1/documents/{doc}/assistant/stream',json={'question':'解释','include_summary':False})
        assert response.status_code==200 and 'text/event-stream' in response.headers['content-type']
        assert 'event: delta' in response.text and 'event: done' in response.text
        assert 'secret' not in response.text
        capabilities=client.get('/api/v1/assistant/capabilities').json()
        assert capabilities['effort_supported'] and capabilities['raw_chain_of_thought'] is False
    finally:asyncio.run(c.aclose())


def test_summary_stream_contains_body_and_reports_actual_coverage(client):
    from rr_storage import Block,Document
    from sqlalchemy import select
    doc=import_and_parse(client)
    client.put('/api/v1/settings/translation',json={'base_url':'https://example.invalid/v1','api_key':'fixture-only','model':'qa-fixture'})
    with client.app.state.database.session() as session:
        paper=session.get(Document,doc)
        selected=next(b for b in session.scalars(select(Block).where(Block.revision_id==paper.active_revision_id)) if b.type in {'title','heading'})
        selected_id=selected.id
    sent=[]
    def provider(request):
        payload=json.loads(request.content);sent.append(payload)
        assert 'paragraph' in payload['messages'][-1]['content'] or 'abstract' in payload['messages'][-1]['content']
        assert '完整文献文本' in payload['messages'][0]['content']
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=frames({'choices':[{'delta':{'content':'根据正文概括 [S1]。'},'finish_reason':'stop'}]}))
    upstream=httpx.AsyncClient(transport=httpx.MockTransport(provider));client.app.state.assistant_async_client=upstream
    try:
        response=client.post(f'/api/v1/documents/{doc}/assistant/stream',json={'question':'概括内容','block_id':selected_id,'include_summary':False})
        assert response.status_code==200
        events=[json.loads(line.removeprefix('data: ')) for line in response.text.splitlines() if line.startswith('data: ')]
        metadata=next(event for event in events if 'context_summary' in event)
        assert metadata['context_summary']['strategy']=='full_document' and metadata['context_summary']['body_selected']>0
        assert metadata['context_summary']['complete'] and not metadata['context_summary']['excerpts']
        assert len(sent)==1
    finally:asyncio.run(upstream.aclose())


def test_stream_context_limit_is_reported_once_without_excerpt_fallback():
    calls=[]
    def provider(request):calls.append(request);return httpx.Response(400,json={'error':{'code':'context_length_exceeded','message':'private provider diagnostic'}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
            return [event async for event in stream_answer(CONFIG,CONTEXT,AssistantRequest(question='解释'),client=upstream)]
    events=asyncio.run(run())
    assert events[-1][0]=='error' and events[-1][1]['code']=='assistant_context_limit'
    assert len(calls)==1 and 'private provider' not in events[-1][1]['message']
