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
