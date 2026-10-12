"""Cancellable provider SSE -> public answer events; raw reasoning never leaves here."""
import asyncio
import json
import re
import time
import httpx
from .assistant import AssistantError,build_payload


class PublicChannels:
    def __init__(self,summary=True):self.mode='prefix' if summary else 'answer';self.pending='';self.summary='';self.answer=''
    def feed(self,text,final=False):
        self.pending+=text;events=[]
        if self.mode=='prefix':
            value=self.pending.lstrip();marker='[[摘要]]'
            if value.startswith(marker):self.pending=value[len(marker):];self.mode='summary'
            elif not final and marker.startswith(value):return events
            else:self.mode='answer'
        if self.mode=='summary':
            marker='[[回答]]';index=self.pending.find(marker)
            if index>=0:
                part=self.pending[:index];self.pending=self.pending[index+len(marker):];self.mode='answer'
            else:
                keep=0
                if not final:
                    for count in range(1,min(len(marker),len(self.pending))+1):
                        if marker.startswith(self.pending[-count:]):keep=count
                part=self.pending[:-keep] if keep else self.pending
                self.pending=self.pending[-keep:] if keep else ''
            part=part[:max(0,600-len(self.summary))]
            if part:self.summary+=part;events.append(('summary',{'text':part}))
        if self.mode=='answer' and self.pending:
            part=self.pending;self.pending='';self.answer+=part;events.append(('delta',{'text':part}))
        return events


async def stream_answer(config,context,request,*,client=None):
    payload=build_payload(config,context,request,stream=True)
    started=time.monotonic();channels=PublicChannels(request.include_summary)
    owned=client is None
    usage={};finish=None;terminated=False;reasoning_observed=False;first_answer=None;phase='connecting'
    yield 'meta',{'model':config.model,'scope':request.scope,'revision_id':context['revision_id'],'sources':context['sources'],
        'reasoning_effort':request.reasoning_effort,'effort_sent':'reasoning_effort' in payload}
    yield 'phase',{'phase':phase,'elapsed_ms':0}
    try:
        client=client or httpx.AsyncClient(timeout=httpx.Timeout(min(config.timeout_seconds,120),connect=15),follow_redirects=False)
        async with asyncio.timeout(min(config.timeout_seconds,120)):
            async with client.stream('POST',config.endpoint(),json=payload,headers={'Authorization':f'Bearer {config.api_key}','Content-Type':'application/json'}) as response:
                if not response.is_success:raise AssistantError('assistant_provider_error',f'AI 服务返回 HTTP {response.status_code}，请检查配置或手动重试。')
                if 'text/event-stream' not in response.headers.get('content-type',''):
                    raise AssistantError('assistant_stream_unsupported','服务未返回流式回答，请检查模型和服务是否支持流式协议；未自动重复请求。')
                data_lines=[];received=0
                async for line in response.aiter_lines():
                    received+=len(line)
                    if received>4_000_000:raise AssistantError('assistant_stream_limit','AI 输出超出本机接收上限，已停止接收。')
                    if line.startswith('data:'):data_lines.append(line[5:].lstrip());continue
                    if line or not data_lines:continue
                    data='\n'.join(data_lines);data_lines=[]
                    if data=='[DONE]':terminated=True;break
                    try:chunk=json.loads(data)
                    except ValueError:raise AssistantError('assistant_stream_invalid','AI 流式响应格式损坏；已保留收到的回答，未自动重试。')
                    if chunk.get('error'):raise AssistantError('assistant_provider_error','AI 服务中断了回答，请核对已收到的内容后手动重试。')
                    if chunk.get('usage'):usage=chunk['usage']
                    choices=chunk.get('choices') or []
                    if not choices:continue
                    choice=choices[0];delta=choice.get('delta') or {}
                    finish=choice.get('finish_reason') or finish
                    # Observe phase only. Never forward/store provider reasoning_content.
                    if delta.get('reasoning_content') or delta.get('reasoning'):
                        reasoning_observed=True
                        if phase=='connecting':phase='thinking';yield 'phase',{'phase':phase,'elapsed_ms':int((time.monotonic()-started)*1000)}
                    content=delta.get('content') or ''
                    if isinstance(content,list):content=''.join(p.get('text','') for p in content if isinstance(p,dict) and p.get('type') in {'text','output_text'})
                    if not isinstance(content,str):continue
                    for kind,value in channels.feed(content):
                        next_phase='summarizing' if kind=='summary' else 'answering'
                        if kind=='delta' and first_answer is None:first_answer=time.monotonic()-started
                        if phase!=next_phase:phase=next_phase;yield 'phase',{'phase':phase,'elapsed_ms':int((time.monotonic()-started)*1000)}
                        yield kind,value
        for event in channels.feed('',final=True):yield event
        if not channels.answer.strip():
            code='assistant_output_limit' if finish=='length' else 'assistant_empty_response'
            raise AssistantError(code,'服务没有生成正式回答，可能达到输出上限；未自动重试，本次请求可能已计费。')
        supplied={s['id']:s for s in context['sources']};referenced=list(dict.fromkeys(re.findall(r'\[(S\d+)\]',channels.answer)))
        unknown=[key for key in referenced if key not in supplied]
        warnings=[f"回答引用了未提供的来源：{', '.join(unknown)}，请核对。"] if unknown else []
        if finish=='length' or not (terminated or finish):warnings.append('回答可能未完整结束，请核对内容后手动重试。')
        yield 'done',{'answer':channels.answer.strip(),'summary':channels.summary.strip(),'model':config.model,'scope':request.scope,
            'revision_id':context['revision_id'],'sources':context['sources'],'citations':[supplied[k] for k in referenced if k in supplied],
            'warnings':warnings,'usage':usage,'reasoning_effort':request.reasoning_effort,
            'metrics':{'elapsed_ms':int((time.monotonic()-started)*1000),'first_answer_ms':int(first_answer*1000) if first_answer is not None else None,
                'reasoning_observed':reasoning_observed,'answer_chars':len(channels.answer)}}
    except (TimeoutError,httpx.TimeoutException):
        yield 'error',{'code':'assistant_unknown_result','message':'请求超时，已保留收到的内容；服务可能已处理，未自动重试。','partial':bool(channels.answer)}
    except httpx.RequestError:
        yield 'error',{'code':'assistant_network_error','message':'AI 连接中断，已保留收到的内容，未自动重试。','partial':bool(channels.answer)}
    except AssistantError as error:yield 'error',{'code':error.code,'message':error.message,'partial':bool(channels.answer)}
    finally:
        if owned and client is not None:await client.aclose()
