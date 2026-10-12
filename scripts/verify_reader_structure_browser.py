"""Real PDF + controlled upstream stream in an isolated DB, never the user's library."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
import pymupdf
from sqlalchemy import select
from rr_services import open_runtime,Settings,DocumentService
from rr_storage import Document,Block,Translation,set_setting
from rr_worker import Worker
from rr_api import create_app
from rr_api.reads import block_payload,latest_translations
from document_core.text_structure import structure,translation_parts
from document_core.cleaning import clean_text

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/acceptance/2026-10-12/reader-structure-stream'
sys.path.insert(0,str(ROOT/'var/_browser_tools'))


def fixture_server(data,port):
    import uvicorn
    app=create_app(Settings(data_dir=Path(data)),serve_frontend=False)
    state={'closed':0,'calls':0}
    class ProviderStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            value={'choices':[{'delta':{'reasoning_content':'PRIVATE_REASONING_DO_NOT_FORWARD'}}]}
            yield ('data: '+json.dumps(value)+'\n\n').encode()
            text='[[摘要]]依据提供的原文核对方法。[[回答]]【界面验收样例】回答正在逐段输出，保留来源 [S1]。'
            for index in range(0,len(text),3):
                await asyncio.sleep(.15)
                yield ('data: '+json.dumps({'choices':[{'delta':{'content':text[index:index+3]}}]},ensure_ascii=False)+'\n\n').encode()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        async def aclose(self):state['closed']+=1
    def upstream(request):
        state['calls']+=1
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=ProviderStream())
    app.state.assistant_async_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    @app.get('/__qa/status')
    def status():return state
    from fastapi.staticfiles import StaticFiles
    app.mount('/',StaticFiles(directory=ROOT/'apps/web',html=True),name='fixture-web')
    uvicorn.run(app,host='127.0.0.1',port=int(port),log_level='error')


def seed(data):
    real=open_runtime();settings=Settings(data_dir=data);isolated=open_runtime(settings)
    with real.database.session() as s:
        old=s.get(Document,'doc_7f670dd5a6dd4b95a3ee');source=real.workspace.resolve(old.source_path);original_hash=old.sha256
        rows=list(s.scalars(select(Block).where(Block.revision_id==old.active_revision_id)))
        translated=latest_translations(s,[b.id for b in rows],target_language='zh');cache={}
        def remember(key,value):cache.setdefault(clean_text(key),[]).append(value)
        for block in rows:
            row=translated.get(block.id)
            if not row or row.status!='ok':continue
            remember(block.clean_text or block.raw_text,row.text)
            src=structure(block.raw_text or block.clean_text,block.spans,block_type=block.type)
            tgt=translation_parts(src,row.text)
            if src['has_list'] and tgt['preserved'] and len(src['parts'])==len(tgt['parts']):
                for a,b in zip(src['parts'],tgt['parts']):
                    remember((a.get('marker','')+' '+a['text']).strip(),(b.get('marker','')+' '+b['text']).strip())
        with isolated.database.session() as target:
            with source.open('rb') as file:
                result=DocumentService(settings,isolated.workspace,isolated.queue).import_file(target,filename=old.filename,stream=file)
            identifier=result.document.id
    worker=Worker(settings,isolated.workspace,isolated.queue,database=isolated.database)
    worker.serve(max_jobs=1,poll_seconds=0,exit_when_idle=True,idle_grace=0)
    with isolated.database.session() as s:
        doc=s.get(Document,identifier);reused=0
        for block in s.scalars(select(Block).where(Block.revision_id==doc.active_revision_id)):
            values=list(dict.fromkeys(cache.get(clean_text(block.clean_text or block.raw_text),[])))
            if len(values)!=1:continue
            s.add(Translation(id='tr_'+uuid4().hex[:24],document_id=doc.id,block_id=block.id,revision_id=doc.active_revision_id,
                version=1,source_hash=hashlib.sha256(block.clean_text.encode()).hexdigest(),provider='cached-validation',model='existing-translation',
                prompt_version='v1',source_language='en',target_language='zh',text=values[0],status='ok',cache_key=uuid4().hex,is_current=True))
            reused+=1
        doc.translate_status='partially_succeeded'
        for key,value in {'translation.base_url':'https://ollama.com/v1','translation.model':'interface-fixture','translation.api_key':'isolated-dummy'}.items():set_setting(s,key,value,is_secret=key.endswith('api_key'))
    real.database.dispose();isolated.database.dispose()
    return identifier,original_hash,reused


def main():
    from playwright.sync_api import sync_playwright,expect
    OUT.mkdir(parents=True,exist_ok=True);checks=[];errors=[]
    with tempfile.TemporaryDirectory(dir=ROOT/'var',prefix='reader-structure-',ignore_cleanup_errors=True) as temp:
        data=Path(temp);print('seeding isolated PDF',flush=True);identifier,original_hash,reused=seed(data);print('seed ready',flush=True)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        env=dict(os.environ);env['RR_ENV_FILE']=str(data/'no-env')
        for key in list(env):
            if key.startswith('RR_TRANSLATION_') or key=='RESEARCH_READER_DB_URL':env.pop(key)
        log=(OUT/'fixture-server.log').open('w',encoding='utf-8')
        server=subprocess.Popen([sys.executable,__file__,'--fixture-server',str(data),str(port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        client=httpx.Client(trust_env=False,timeout=30)
        try:
            for _ in range(100):
                try:
                    if client.get(url+'/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('fixture did not start')
            print('server ready, verifying outline',flush=True);outline=client.get(f'{url}/api/v1/documents/{identifier}/outline').json()['outline']
            checks.append({'name':'真实论文目录排除图表、单词、数值，包含 5.5 节','passed':any(e['title'].startswith('5.5.') for e in outline) and not any(e['title'] in {'Method','E','Image-based metrics','A R T I C L E I N F O'} or '±' in e['title'] for e in outline),'headings':len(outline)})
            (OUT/'outline.json').write_text(json.dumps(outline,ensure_ascii=False,indent=2),encoding='utf-8')
            print('outline ready, starting browser',flush=True)
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge',headless=True);page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
                page.on('pageerror',lambda e:(errors.append(str(e)),print('browser_error',str(e),flush=True)))
                page.on('response',lambda r:print('http_error',r.status,r.url,flush=True) if r.status>=400 else None)
                print('browser ready, opening reader',flush=True);page.goto(f'{url}/#/doc/{identifier}');page.wait_for_timeout(1000);print('view status',page.locator('#view').inner_text()[:400],flush=True);page.locator('#view-mode').select_option('overlay_side_by_side')
                while page.locator('#previous-page').is_enabled():
                    before=page.locator('#footer-note').inner_text();index=int(before.split('内部页索引 ')[1].split(' ')[0])
                    page.locator('#previous-page').click();expect(page.locator('#footer-note')).to_contain_text(f'内部页索引 {max(0,index-1)} ·')
                page.locator('#next-page').click();expect(page.locator('#footer-note')).to_contain_text('内部页索引 1 ·')
                expect(page.locator('#target-body .structured-list-item').first).to_be_visible()
                checks.append({'name':'真实 PDF 分点恢复，使用既有译文而非重新翻译','passed':page.locator('#target-body .structured-list-item').count()>=4,'reused_translation_blocks':reused})
                print('list layout verified, capturing image',flush=True);page.screenshot(path=str(OUT/'real-page2-list.png'))
                page.locator('#view-mode').select_option('side_by_side');expect(page.locator('#target-body .structured-list-item').first).to_be_visible()
                checks.append({'name':'段落卡片同样保留编号和悬挂缩进','passed':True})
                print('assistant before',page.url,page.locator('#assistant-panel').count(),page.locator('#assistant-question').count(),flush=True);page.locator('#assistant-toggle').click();print('assistant after',page.url,page.locator('#assistant-panel').count(),page.locator('#assistant-question').count(),flush=True);page.locator('#assistant-question').fill('解释本文方法')
                page.locator('#assistant-send').click()
                live=page.locator('.assistant-streaming .assistant-answer-text')
                expect(live).to_contain_text('界面验收',timeout=15000)
                checks.append({'name':'完整回答结束前已显示第一段文字','passed':page.locator('.assistant-streaming').count()==1})
                page.screenshot(path=str(OUT/'assistant-streaming.png'))
                expect(page.locator('.assistant-streaming')).to_have_count(0,timeout=15000)
                checks.append({'name':'公开摘要、思考强度、真实耗时和来源可见；无原始思考文本','passed':page.locator('.assistant-summary').count()>0 and page.locator('.assistant-source').count()>0 and 'PRIVATE_REASONING' not in page.locator('#assistant-panel').inner_text()})
                page.locator('#assistant-question').fill('再次解释');page.locator('#assistant-send').click()
                expect(page.locator('.assistant-streaming .assistant-answer-text')).to_contain_text('界面验收',timeout=15000)
                page.locator('#assistant-stop').click();expect(page.get_by_text('已停止接收',exact=True)).to_be_visible()
                for _ in range(30):
                    if client.get(url+'/__qa/status').json()['closed']>=2:break
                    time.sleep(.1)
                stats=client.get(url+'/__qa/status').json()
                checks.append({'name':'停止后部分回答保留并关闭上游连接，无自动重试','passed':stats['closed']>=2 and stats['calls']==2,'stats':stats})
                page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(OUT/'assistant-mobile.png'))
                checks.append({'name':'390px 助手不造成整页横向溢出','passed':page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')})
                browser.close()
            print('interaction verified, starting full-page geometry',flush=True)
            alignment=subprocess.run([sys.executable,str(ROOT/'scripts/verify_real_layout_alignment.py'),'--document',identifier,'--base-url',url,'--output',str(OUT/'layout'),'--browser-channel','msedge'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
            (OUT/'layout-run.log').write_text(alignment.stdout+alignment.stderr,encoding='utf-8')
            checks.append({'name':'真实 BrLP 隔离副本 21 页几何与图表保护回归','passed':alignment.returncode==0})
            original=client.get(f'{url}/api/v1/documents/{identifier}/original').content
            checks.append({'name':'原件指纹保持一致','passed':'sha256:'+hashlib.sha256(original).hexdigest()==original_hash})
        finally:
            server.terminate();server.wait(timeout=15);client.close();log.close()
    report={'checks':checks,'errors':errors,'passed':all(c['passed'] for c in checks) and not errors,'browser_channel':'msedge','ai_service':'controlled streaming fixture; no external AI requests','user_library_modified':False}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=True))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--fixture-server':fixture_server(sys.argv[2],sys.argv[3])
    else:main()
