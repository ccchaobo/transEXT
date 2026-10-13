"""Browser regression with a self-authored paper and controlled upstream; no cloud charges."""
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from reportlab.pdfgen import canvas
from rr_api import create_app
from rr_services import Settings,open_runtime,DocumentService
from rr_storage import set_setting
from rr_worker import Worker

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/acceptance/2026-10-13/assistant-full-document'


def serve(data,port):
    import uvicorn
    app=create_app(Settings(data_dir=Path(data)),serve_frontend=False)
    state={'calls':0,'body_payloads':0,'full_payloads':0,'streams_closed':0}
    def provider(request):
        payload=json.loads(request.content);state['calls']+=1
        body=payload['messages'][-1]['content']
        if 'SELF_AUTHORED_BODY' not in body:
            return httpx.Response(400,json={'error':'body missing'})
        state['body_payloads']+=1
        from sqlalchemy import select
        from rr_storage import Block,Document
        from rr_services.assistant_context import source_text
        with app.state.database.session() as session:
            document=session.scalars(select(Document).where(Document.deleted_at.is_(None))).first()
            blocks=list(session.scalars(select(Block).where(Block.revision_id==document.active_revision_id).order_by(Block.order_index)))
            readable=[block for block in blocks if source_text(block).strip()]
            expected='\n\n'.join(f'[S{index+1}] 第{block.page_index+1}页 · {block.type}：\n{source_text(block)}' for index,block in enumerate(readable))
            complete=expected in body
        if not complete:return httpx.Response(400,json={'error':'complete document missing'})
        state['full_payloads']+=1
        if 'CONTEXT_LIMIT_FIXTURE' in body:return httpx.Response(400,json={'error':{'code':'context_length_exceeded'}})
        text='[[摘要]]【受控验收】依据完整文献文本，含标题与参考文献。[[回答]]## 研究问题\n\n【受控验收】全文介绍了 **结构化模型** 与独立实验 [S3]。\n\n1. 保留完整文献\n   - 正文与参考文献\n   - 图注与表格文字\n2. 根据全文回答\n\n| 项目 | 内容 |\n|---|---|\n| 范围 | 完整解析文本 |\n\n```python\nscore = (x + y) / 2\n```\n\n<img src=x onerror="window.__markdown_executed=true">'
        if 'STOP_FIXTURE' in body:
            class Slow(httpx.AsyncByteStream):
                async def __aiter__(self):
                    frame={'choices':[{'delta':{'content':'[[回答]]【受控验收】已收到部分内容。'}}]}
                    yield ('data: '+json.dumps(frame,ensure_ascii=False)+'\n\n').encode()
                    await asyncio.sleep(3)
                    yield b'data: [DONE]\n\n'
                async def aclose(self):state['streams_closed']+=1
            return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=Slow())
        frames='data: '+json.dumps({'choices':[{'delta':{'content':text},'finish_reason':'stop'}]},ensure_ascii=False)+'\n\ndata: [DONE]\n\n'
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=frames)
    app.state.assistant_async_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
    @app.get('/__qa/context-status')
    def status():return state
    from fastapi.staticfiles import StaticFiles
    app.mount('/',StaticFiles(directory=ROOT/'apps/web',html=True),name='qa-web')
    uvicorn.run(app,host='127.0.0.1',port=int(port),log_level='error')


def seed(data):
    runtime=open_runtime(Settings(data_dir=data))
    stream=io.BytesIO();pdf=canvas.Canvas(stream,pagesize=(600,800))
    for index,name in enumerate(['Introduction','Methods','Results and Conclusion']):
        pdf.setFont('Helvetica-Bold',16);pdf.drawString(50,740,'A controlled context test' if index==0 else name)
        pdf.setFont('Helvetica-Bold',12);pdf.drawString(50,680,f'{index+1} {name}')
        pdf.setFont('Helvetica',10)
        for line in range(3):
            pdf.drawString(50,630-line*60,f'SELF_AUTHORED_BODY {name}: we report an arithmetic experiment, example {line+1}.')
        if index==2:
            pdf.setFont('Helvetica-Bold',12);pdf.drawString(50,230,'References')
            pdf.setFont('Helvetica',10);pdf.drawString(50,195,'[1] SELF_AUTHORED_REFERENCE: A fictional publication for UI acceptance, 2026.')
        pdf.setFont('Helvetica',8);pdf.drawString(50,35,f'SELF_AUTHORED_FOOTER page {index+1}')
        pdf.showPage()
    pdf.save()
    with runtime.database.session() as session:
        accepted=DocumentService(runtime.settings,runtime.workspace,runtime.queue).import_file(session,filename='context-fixture.pdf',stream=io.BytesIO(stream.getvalue()))
        identifier=accepted.document.id
        for key,value in {'translation.base_url':'https://example.invalid/v1','translation.model':'controlled-fixture','translation.api_key':'fixture-only'}.items():set_setting(session,key,value,is_secret=key.endswith('api_key'))
    Worker(runtime.settings,runtime.workspace,runtime.queue,database=runtime.database).serve(max_jobs=1,poll_seconds=0,exit_when_idle=True,idle_grace=0)
    runtime.database.dispose()
    return identifier


def main():
    sys.path.insert(0,str(ROOT/'var/_browser_tools'))
    from playwright.sync_api import sync_playwright,expect
    OUT.mkdir(parents=True,exist_ok=True);checks=[];errors=[];sent=[]
    def check(name,passed=True):
        checks.append({'name':name,'passed':bool(passed)});print(('PASS ' if passed else 'FAIL ')+name,flush=True);assert passed,name
    with tempfile.TemporaryDirectory(dir=ROOT/'var',prefix='assistant-full-',ignore_cleanup_errors=True) as temporary:
        data=Path(temporary);identifier=seed(data)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        environment={key:value for key,value in os.environ.items() if not key.startswith('RR_TRANSLATION_')};environment['RR_ENV_FILE']=str(data/'no-env')
        log=(data/'server.log').open('w',encoding='utf8')
        server=subprocess.Popen([sys.executable,__file__,'--serve',str(data),str(port)],cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        client=httpx.Client(trust_env=False,timeout=10)
        try:
            for _ in range(100):
                try:
                    if client.get(url+'/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            expected=client.get(f'{url}/api/v1/documents/{identifier}/assistant/context').json()
            check('全文状态接口报告完整解析文本，超过八个来源块',expected['complete'] and not expected['excerpts'] and expected['source_count']>8)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
                page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{window.__qaCopied=value;}},configurable:true});")
                page.on('pageerror',lambda error:errors.append(str(error)))
                def capture(request):
                    if request.method=='POST' and '/assistant/stream' in request.url:sent.append(request.post_data_json)
                page.on('request',capture)
                def ready():
                    target=f'{url}/#/doc/{identifier}'
                    if page.url==target:page.reload()
                    else:page.goto(target)
                    expect(page.locator('#footer-note')).to_contain_text('内部页索引 0 ·')
                    payload=client.get(f'{url}/api/v1/documents/{identifier}/pages/0').json()
                    title=next(b for b in payload['blocks'] if b['type'] in {'title','heading'})
                    page.locator(f'#source-body .source-text-line[data-block-id="{title["id"]}"]').first.click()
                    page.locator('#assistant-toggle').click()
                    expect(page.locator('.assistant-document-status')).to_contain_text('全文文本')
                    return title
                def ask(question):
                    page.locator('#assistant-question').fill(question);page.locator('#assistant-send').click()
                    expect(page.locator('.assistant-answer-text').last).to_contain_text('受控验收')
                    expect(page.locator('.assistant-streaming')).to_have_count(0)
                    expect(page.locator('.assistant-coverage').last).to_contain_text('完整全文文本')
                ready()
                expect(page.locator('#assistant-context-scope')).to_have_count(0)
                expect(page.locator('.assistant-focus')).to_be_visible()
                ask('解释关注内容')
                check('选中标题仍发送完整文献，不再出现范围切换控件',sent[-1]['block_id'] is not None and sent[-1]['context_scope']=='document')
                answer=page.locator('.assistant-answer').last
                expect(answer.locator('strong')).to_contain_text('结构化模型')
                for selector in ['h2','ol','ul','table','pre code']:expect(answer.locator(selector)).to_be_visible()
                expect(answer.locator('img,script')).to_have_count(0)
                check('回答排版包含标题、强调、嵌套列表、表格和代码，原始 HTML 不执行',not page.evaluate('Boolean(window.__markdown_executed)'))
                answer.get_by_role('button',name='复制回答',exact=True).click()
                page.wait_for_function('window.__qaCopied && window.__qaCopied.includes("结构化模型")')
                check('复制保留原回答文字与 Markdown，不操作系统剪贴板')
                expect(answer.locator('.assistant-sources')).not_to_have_attribute('open','')
                answer.locator('.assistant-sources summary').click();expect(answer.locator('.assistant-source')).to_be_visible()
                check('来源默认折叠，展开后可核对原文')
                page.wait_for_timeout(220);page.screenshot(path=str(OUT/'01-document-chat.png'))
                for prompt in ['概括本文的研究问题','解释本文的方法','总结主要结论与局限']:
                    ready();previous=client.get(url+'/__qa/context-status').json()['calls']
                    page.get_by_role('button',name=prompt,exact=True).click()
                    expect(page.locator('#assistant-question')).to_have_value(prompt)
                    expect(page.locator('.assistant-focus')).to_be_hidden()
                    check(f'预设「{prompt}」只填文本，清除旧关注点，不自动计费',client.get(url+'/__qa/context-status').json()['calls']==previous)
                    page.locator('#assistant-question').press('Enter')
                    expect(page.locator('.assistant-streaming')).to_have_count(0)
                    expect(page.locator('.assistant-coverage').last).to_contain_text('完整全文文本')
                    check(f'预设「{prompt}」使用完整文献',sent[-1]['selected_text']=='' and sent[-1]['block_id'] is None)
                page.get_by_role('button',name='对话选项',exact=True).click();page.get_by_role('menuitem',name='对话设置',exact=True).click()
                expect(page.get_by_role('dialog',name='对话设置')).to_be_visible()
                expect(page.get_by_role('dialog',name='对话设置')).to_contain_text('完整文献文本')
                page.locator('#assistant-scope').select_option('general')
                page.wait_for_timeout(220);page.screenshot(path=str(OUT/'02-chat-settings.png'))
                page.get_by_role('dialog',name='对话设置').get_by_role('button',name='完成').click()
                ask('参考文献中包含什么信息？')
                check('回答方式设置不改变全文范围',sent[-1]['scope']=='general' and sent[-1]['context_scope']=='document')
                for width in [1024,768,390]:
                    page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(120)
                    check(f'{width}px 文献聊天无整页横向溢出',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    if width==390:page.screenshot(path=str(OUT/'03-chat-mobile.png'))
                page.set_viewport_size({'width':1440,'height':1000})
                page.get_by_role('button',name='对话选项',exact=True).click();page.get_by_role('menuitem',name='新对话',exact=True).click()
                expect(page.locator('.assistant-answer')).to_have_count(0);expect(page.locator('.assistant-suggestion')).to_have_count(3)
                check('新对话清空当前聊天并保留文献接入状态')
                page.locator('#assistant-question').fill('CONTEXT_LIMIT_FIXTURE');previous=client.get(url+'/__qa/context-status').json()['calls'];page.locator('#assistant-send').click()
                expect(page.locator('.assistant-answer').last).to_contain_text('上下文容量')
                expect(page.locator('.assistant-streaming')).to_have_count(0)
                check('模型拒绝全文长度时明确报告，不改发节选或重试',client.get(url+'/__qa/context-status').json()['calls']==previous+1)
                page.locator('#assistant-question').fill('STOP_FIXTURE');page.locator('#assistant-send').click()
                expect(page.locator('.assistant-streaming .assistant-answer-text')).to_contain_text('已收到部分内容')
                page.locator('#assistant-stop').click();expect(page.locator('.assistant-streaming')).to_have_count(0)
                expect(page.locator('.assistant-answer').last).to_contain_text('已停止接收')
                check('停止流式输出保留已收到内容，不重复请求')
                blocked=browser.new_page(viewport={'width':1440,'height':1000})
                blocked.route('**/assistant/context',lambda route:route.fulfill(json={'strategy':'related','complete':False,'source_count':8}))
                blocked.goto(f'{url}/#/doc/{identifier}');expect(blocked.locator('#footer-note')).to_contain_text('内部页索引 0 ·');blocked.locator('#assistant-toggle').click()
                expect(blocked.locator('.assistant-document-status')).to_contain_text('后端尚未提供完整全文')
                blocked.locator('#assistant-question').fill('概括全文');expect(blocked.locator('#assistant-send')).to_be_disabled()
                check('后端仍返回局部资料时禁止发送，不把八段伪装成全文')
                browser.close()
            state=client.get(url+'/__qa/context-status').json()
            check('每次实际模型请求都包含全部可读文字，含标题、参考文献和页脚',state['calls']==state['full_payloads']==7)
            check('浏览器无未捕获脚本错误',not errors)
            report={'checks':checks,'passed':all(check['passed'] for check in checks),'provider':state,'coverage':expected,'paid_requests':0,'fixture':'self-authored full document; no user text'}
            (OUT/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            print(json.dumps({'passed':report['passed'],'checks':len(checks),'provider':state}),flush=True)
        finally:
            server.terminate();server.wait(timeout=15);log.close();client.close()
    return 0


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--serve':serve(sys.argv[2],sys.argv[3])
    else:raise SystemExit(main())
