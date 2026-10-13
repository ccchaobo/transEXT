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
OUT=ROOT/'docs/acceptance/2026-10-13/assistant-context'


def serve(data,port):
    import uvicorn
    app=create_app(Settings(data_dir=Path(data)),serve_frontend=False)
    state={'calls':0,'body_payloads':0}
    def provider(request):
        payload=json.loads(request.content);state['calls']+=1
        body=payload['messages'][-1]['content']
        if 'SELF_AUTHORED_BODY' not in body:
            return httpx.Response(400,json={'error':'body missing'})
        state['body_payloads']+=1
        text='[[摘要]]【受控验收】依据本次提供的正文。[[回答]]【受控验收】正文介绍了一种方法及其实验结果 [S1]。'
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
    OUT.mkdir(parents=True,exist_ok=True);checks=[]
    with tempfile.TemporaryDirectory(dir=ROOT/'var',prefix='assistant-context-',ignore_cleanup_errors=True) as temporary:
        data=Path(temporary);identifier=seed(data)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        environment={key:value for key,value in os.environ.items() if not key.startswith('RR_TRANSLATION_')}
        environment['RR_ENV_FILE']=str(data/'no-env')
        log=(data/'server.log').open('w',encoding='utf8')
        server=subprocess.Popen([sys.executable,__file__,'--serve',str(data),str(port)],cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        client=httpx.Client(trust_env=False,timeout=10)
        try:
            for _ in range(50):
                try:
                    if client.get(url+'/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.2)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.goto(f'{url}/#/doc/{identifier}')
                expect(page.locator('#footer-note')).to_contain_text('内部页索引 0 ·')
                payload=client.get(f'{url}/api/v1/documents/{identifier}/pages/0').json()
                title=next(b for b in payload['blocks'] if b['type'] in {'title','heading'})
                page.locator(f'#source-body .source-text-line[data-block-id="{title["id"]}"]').first.click()
                page.locator('#assistant-toggle').click()
                page.locator('#assistant-question').fill('概括内容')
                page.locator('#assistant-send').click()
                expect(page.locator('.assistant-answer-text').last).to_contain_text('受控验收')
                expect(page.locator('.assistant-streaming')).to_have_count(0)
                expect(page.locator('.assistant-coverage').last).to_contain_text('全文概括')
                checks.append({'name':'selected title does not limit an automatic document summary','passed':True})
                page.screenshot(path=str(OUT/'overview-with-selected-title.png'))
                page.locator('#assistant-context-scope').select_option('selection')
                page.locator('#assistant-question').fill('概括这段内容')
                page.locator('#assistant-send').click()
                expect(page.locator('.assistant-answer-text')).to_have_count(2)
                expect(page.locator('.assistant-streaming')).to_have_count(0)
                expect(page.locator('.assistant-coverage').last).to_contain_text('所选内容')
                checks.append({'name':'explicit selected-content mode reaches the service and is displayed','passed':True})
                page.set_viewport_size({'width':390,'height':844})
                page.wait_for_timeout(150)
                checks.append({'name':'scope controls and coverage do not overflow the mobile viewport','passed':page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')})
                page.screenshot(path=str(OUT/'mobile-context-controls.png'))
                page.set_viewport_size({'width':1440,'height':1000})
                for prompt,strategy in [('概括本文的研究问题','全文概括'),('解释本文的方法','全文检索'),('总结主要结论与局限','全文概括')]:
                    page.reload()
                    expect(page.locator('#footer-note')).to_contain_text('内部页索引 0 ·')
                    page.locator(f'#source-body .source-text-line[data-block-id="{title["id"]}"]').first.click()
                    page.locator('#assistant-toggle').click()
                    page.locator('#assistant-context-scope').select_option('selection')
                    previous=client.get(url+'/__qa/context-status').json()['calls']
                    page.get_by_role('button',name=prompt,exact=True).click()
                    expect(page.locator('#assistant-context-scope')).to_have_value('document')
                    expect(page.locator('#assistant-question')).to_have_value(prompt)
                    expect(page.locator('#assistant-panel > .assistant-context').first).to_contain_text('不使用之前的选区')
                    assert client.get(url+'/__qa/context-status').json()['calls']==previous,'preset must not automatically call the model'
                    with page.expect_request(lambda request: request.method=='POST' and '/assistant/stream' in request.url) as pending:
                        page.locator('#assistant-send').click()
                    request=pending.value.post_data_json
                    assert request['context_scope']=='document' and not request['selected_text'] and request['block_id'] is None
                    expect(page.locator('.assistant-answer-text').last).to_contain_text('受控验收')
                    expect(page.locator('.assistant-streaming')).to_have_count(0)
                    expect(page.locator('.assistant-coverage').last).to_contain_text(strategy)
                    checks.append({'name':f'preset {prompt} clears a selected title, uses document context and waits for send','passed':True})
                    if strategy=='全文检索':page.screenshot(path=str(OUT/'method-preset-with-selected-title.png'))
                browser.close()
            state=client.get(url+'/__qa/context-status').json()
            checks.append({'name':'all five provider payloads contain actual source body','passed':state['calls']==state['body_payloads']==5,'counts':state})
        finally:
            server.terminate();server.wait(timeout=15);log.close();client.close()
    report={'checks':checks,'passed':all(check['passed'] for check in checks),'upstream':'controlled fixture','paid_requests':0}
    (OUT/'browser-report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--serve':serve(sys.argv[2],sys.argv[3])
    else:raise SystemExit(main())
