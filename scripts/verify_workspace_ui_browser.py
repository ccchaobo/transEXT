"""Workspace UI acceptance against self-authored fixtures and an isolated database."""
import hashlib
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
from sqlalchemy import select
from rr_services import Settings, DocumentService, open_runtime
from rr_storage import Block, Document, Translation
from rr_worker import Worker
from verify_assistant_context_browser import seed

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('RR_UI_ACCEPTANCE_OUTPUT',str(ROOT/'docs/acceptance/2026-10-13/workspace-ui')))
sys.path.insert(0, str(ROOT/'var/_browser_tools'))
from playwright.sync_api import sync_playwright, expect


def fixtures(data):
    first = seed(data)
    runtime = open_runtime(Settings(data_dir=data))
    identifiers = [first]
    with runtime.database.session() as session:
        for index in range(6):
            stream = io.BytesIO(); pdf = canvas.Canvas(stream,pagesize=(600,800))
            pdf.setFont('Helvetica-Bold',16); pdf.drawString(50,740,f'Self-authored research example {index+1}')
            pdf.setFont('Helvetica',11); pdf.drawString(50,680,f'This is an isolated UI fixture {index+1}, not a published research paper.')
            pdf.save()
            result = DocumentService(runtime.settings,runtime.workspace,runtime.queue).import_file(session,filename=f'示例文献-{index+1}.pdf',stream=io.BytesIO(stream.getvalue()))
            identifiers.append(result.document.id)
    Worker(runtime.settings,runtime.workspace,runtime.queue,database=runtime.database).serve(max_jobs=6,poll_seconds=0,exit_when_idle=True,idle_grace=0)
    with runtime.database.session() as session:
        doc = session.get(Document,first)
        for block in session.scalars(select(Block).where(Block.document_id==first)):
            text = {'title':'结构化科研阅读：从文献到洞察', 'heading':{'1 Introduction':'1 引言','2 Methods':'2 方法','3 Results and Conclusion':'3 结果与结论'}.get(block.clean_text,block.clean_text)}.get(block.type,'【自编验收译文】本研究采用结构化信息提取方法，保留原始段落与阅读顺序，并通过独立实验验证正文来源的覆盖情况。')
            session.add(Translation(id=f'ui_{block.id}',document_id=first,block_id=block.id,revision_id=doc.active_revision_id,source_hash=block.text_hash,provider='self-authored-fixture',cache_key=f'ui_{block.id}',text=text,status='ok',origin='human'))
    runtime.database.dispose()
    return identifiers


def main():
    OUT.mkdir(parents=True,exist_ok=True); checks=[]; errors=[]
    def check(name, passed=True, detail=None):
        checks.append({'name':name,'passed':bool(passed),'detail':detail})
        print(('PASS ' if passed else 'FAIL ')+name,flush=True)
        assert passed, name
    with tempfile.TemporaryDirectory(dir=ROOT/'var',prefix='workspace-ui-',ignore_cleanup_errors=True) as temporary:
        data=Path(temporary); identifiers=fixtures(data)
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        environment={key:value for key,value in os.environ.items() if not key.startswith('RR_TRANSLATION_')}
        environment['RR_ENV_FILE']=str(data/'no-env')
        log=(data/'server.log').open('w',encoding='utf-8')
        server=subprocess.Popen([sys.executable,str(ROOT/'scripts/verify_assistant_context_browser.py'),'--serve',str(data),str(port)],cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        client=httpx.Client(trust_env=False,timeout=15)
        try:
            for _ in range(100):
                try:
                    if client.get(url+'/health').status_code==200: break
                except httpx.HTTPError: pass
                time.sleep(.1)
            titles=['结构化科研阅读：从文献到洞察','大语言模型的指令对齐与评估','医学影像中的扩散建模方法','跨模态表征与知识检索','科学文献中的图表理解','面向长文档的上下文组织','高效的实验设计与结果分析']
            for index,identifier in enumerate(identifiers):
                payload=client.get(f'{url}/api/v1/library/items/{identifier}').json()
                meta=payload['bibliography']; meta.update(title=titles[index],authors=['科研团队',f'Example Author {index+1}'],year=2026-index%3,publication='自编 UI 验收文献',read_status=['reading','to_read','read'][index%3],starred=index in {0,2})
                reply=client.patch(f'{url}/api/v1/library/items/{identifier}',json={'bibliography':meta,'tags':['待复核','方法研究'] if index%2==0 else ['精读'],'collection_ids':[]}); reply.raise_for_status()
            collection=client.post(url+'/api/v1/library/collections',json={'name':'生成式人工智能'}).json()['id']
            empty_collection=client.post(url+'/api/v1/library/collections',json={'name':'医学影像研究'}).json()['id']
            client.post(url+'/api/v1/library/batch',json={'ids':identifiers[:4],'action':'add_collection','collection_id':collection}).raise_for_status()
            originals=[hashlib.sha256(client.get(f'{url}/api/v1/documents/{identifier}/original').content).hexdigest() for identifier in identifiers]
            revisions=[client.get(f'{url}/api/v1/documents/{identifier}').json()['active_revision_id'] for identifier in identifiers]
            with sync_playwright() as pw:
                channel=os.environ.get('RR_UI_BROWSER','chrome')
                browser=pw.chromium.launch(channel=channel,headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN',color_scheme='light')
                def snapshot(name):
                    page.wait_for_timeout(220)
                    page.screenshot(path=str(OUT/name))
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(url); expect(page.locator('.library-table tbody tr')).to_have_count(7)
                expect(page.locator('.library-manager')).not_to_have_attribute('aria-busy','true')
                snapshot('01-library-light.png')
                row=page.locator(f'[data-collection-id="{collection}"]'); trigger=row.locator('.more-button')
                trigger.focus(); page.keyboard.press('ArrowDown')
                expect(page.get_by_role('menu')).to_be_visible(); expect(page.get_by_role('menuitem',name='重命名')).to_be_focused()
                page.keyboard.press('End'); expect(page.get_by_role('menuitem',name='删除分类')).to_be_focused()
                page.keyboard.press('Escape'); expect(page.get_by_role('menu')).to_have_count(0); expect(trigger).to_be_focused()
                trigger.click(); page.keyboard.press('Tab'); expect(page.get_by_role('menu')).to_have_count(0)
                check('Tab 退出菜单且页面仍可继续操作',page.evaluate('document.activeElement !== document.body'))
                check('分类菜单支持方向键、End、Escape 与焦点恢复')
                row.click(button='right'); expect(page.get_by_role('menu')).to_be_visible()
                page.wait_for_timeout(200)
                snapshot('02-collection-menu.png')
                page.get_by_role('menuitem',name='重命名').click()
                expect(page.locator('.library-dialog')).to_be_visible()
                page.locator('.library-dialog input').fill('生成式 AI 与评估')
                page.locator('.library-dialog').get_by_role('button',name='保存',exact=True).click()
                expect(row.locator('.library-nav-item')).to_contain_text('生成式 AI 与评估')
                check('右键打开分类菜单，重命名后保留分类及成员')
                emptyrow=page.locator(f'[data-collection-id="{empty_collection}"]')
                emptyrow.locator('.more-button').click(); page.get_by_role('menuitem',name='删除分类').click()
                expect(page.locator('.confirm-dialog')).to_be_visible()
                expect(page.locator('.confirm-dialog').get_by_role('button',name='取消')).to_be_focused()
                page.wait_for_timeout(200)
                snapshot('03-confirm-dialog.png')
                page.keyboard.press('Escape'); expect(emptyrow).to_be_visible()
                emptyrow.locator('.more-button').click(); page.get_by_role('menuitem',name='删除分类').click()
                page.locator('.confirm-dialog').get_by_role('button',name='删除分类').click()
                expect(emptyrow).to_have_count(0); expect(page.locator('.library-table tbody tr')).to_have_count(7)
                check('删除分类先确认，Escape 取消；确认删除不删除文献')
                paper=page.locator(f'tr[data-id="{identifiers[0]}"]')
                paper.locator('.more-button').click()
                page.get_by_role('menuitem',name='标为已读').click()
                expect(paper.locator('.read-status')).to_have_text('已读')
                paper.click(button='right'); expect(page.get_by_role('menuitem',name='移入回收站')).to_be_visible()
                page.wait_for_timeout(200)
                snapshot('04-document-menu.png')
                page.locator('.library-toolbar h1').click(); expect(page.get_by_role('menu')).to_have_count(0)
                check('条目菜单作用于目标文献，点击外部关闭')
                paper.dispatch_event('contextmenu',{'clientX':1430,'clientY':990})
                expect(page.get_by_role('menu')).to_be_visible(); page.wait_for_timeout(200)
                boundary=page.get_by_role('menu').bounding_box()
                check('视口右下角的右键菜单会翻转并限制在视口内',boundary['x']+boundary['width']<=1441 and boundary['y']+boundary['height']<=1001)
                page.keyboard.press('Escape')
                page.get_by_label('文献排序').click()
                custom=page.evaluate('CSS.supports("appearance", "base-select")')
                if custom:
                    page.wait_for_timeout(200)
                    snapshot('05-select-picker.png')
                    page.get_by_role('option',name='标题',exact=True).click()
                else: page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')
                expect(page.get_by_label('文献排序')).to_have_value('title')
                check('下拉控件保留原生选择事件与排序行为',detail={'custom_picker_supported':custom})
                page.locator('#appearance-button').click(); page.get_by_role('menuitemradio',name='深色',exact=True).click()
                expect(page.locator('html')).to_have_attribute('data-theme','dark'); page.reload()
                expect(page.locator('html')).to_have_attribute('data-theme','dark')
                expect(page.locator('.library-table tbody tr')).to_have_count(7)
                snapshot('06-library-dark.png')
                check('深色外观刷新后保留')
                page.locator('#appearance-button').click(); page.get_by_role('menuitemradio',name='跟随系统',exact=True).click()
                page.emulate_media(color_scheme='light'); expect(page.locator('html')).to_have_attribute('data-theme','light')
                page.emulate_media(color_scheme='dark'); expect(page.locator('html')).to_have_attribute('data-theme','dark')
                page.locator('#appearance-button').click(); page.get_by_role('menuitemradio',name='浅色',exact=True).click()
                check('跟随系统实时响应，手动选择可覆盖系统')
                for width in [1024,768,390]:
                    page.set_viewport_size({'width':width,'height':900})
                    page.wait_for_timeout(120)
                    check(f'{width}px 文献库无整页横向溢出',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    row.locator('.more-button').click(); expect(page.get_by_role('menu')).to_be_visible()
                    box=page.get_by_role('menu').bounding_box()
                    check(f'{width}px 菜单位于视口内且不被导航裁切',box['x']>=0 and box['x']+box['width']<=width+1 and box['y']>=0 and box['y']+box['height']<=901)
                    if width==390: snapshot('07-mobile-menu.png')
                    page.keyboard.press('Escape')
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator(f'tr[data-id="{identifiers[0]}"]').dblclick()
                expect(page.locator('#footer-note')).to_contain_text('内部页索引 0 ·')
                page.locator('#assistant-toggle').click(); expect(page.locator('#assistant-panel')).to_be_visible()
                snapshot('08-reader-assistant.png')
                page.locator('#view-mode').select_option('overlay')
                expect(page.locator('.reader')).to_have_attribute('data-mode','overlay')
                source=page.locator('#source-body .page-shell').first
                dimensions=source.bounding_box()
                check('阅读视图切换正常，原件画布宽高比保持 600:800',abs(dimensions['height']/dimensions['width']-800/600)<.01)
                page.locator('#view-mode').select_option('side_by_side')
                for width in [1024,768,390]:
                    page.set_viewport_size({'width':width,'height':900})
                    check(f'{width}px 阅读器无整页横向溢出',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator('#appearance-button').click(); page.get_by_role('menuitemradio',name='深色',exact=True).click()
                page.locator('#view-mode').select_option('overlay'); snapshot('09-reader-dark-paper.png')
                panel=page.locator('.overlay-panel').first
                check('深色界面中的覆盖译文仍为白纸黑字',panel.evaluate('(element) => getComputedStyle(element).backgroundColor')=='rgb(255, 255, 255)' and panel.evaluate('(element) => getComputedStyle(element).color')=='rgb(37, 41, 55)')
                page.locator('#settings-button').click(); expect(page.locator('#settings-dialog')).to_be_visible()
                snapshot('10-settings-dark.png'); page.locator('#settings-close').click()
                page.emulate_media(reduced_motion='reduce')
                page.locator('#appearance-button').click()
                check('减少动态效果设置禁用弹层动画',page.get_by_role('menu').evaluate('(element) => getComputedStyle(element).animationName')=='none')
                page.keyboard.press('Escape')
                fallback=browser.new_page(viewport={'width':390,'height':844},locale='zh-CN')
                fallback.add_init_script('HTMLElement.prototype.showPopover = undefined; HTMLElement.prototype.hidePopover = undefined;')
                fallback.goto(url); expect(fallback.locator('.library-manager')).to_be_visible()
                fallback.locator('#appearance-button').click(); expect(fallback.get_by_role('menu')).to_be_visible()
                fallback.get_by_role('menuitemradio',name='深色',exact=True).click()
                expect(fallback.locator('html')).to_have_attribute('data-theme','dark')
                check('没有 Popover API 时固定定位菜单仍可用')
                browser.close()
            final=[hashlib.sha256(client.get(f'{url}/api/v1/documents/{identifier}/original').content).hexdigest() for identifier in identifiers]
            check('分类与菜单操作保留所有测试原件和解析版本',final==originals and revisions==[client.get(f'{url}/api/v1/documents/{identifier}').json()['active_revision_id'] for identifier in identifiers])
            state=client.get(url+'/__qa/context-status').json(); check('UI 验收没有产生模型请求',state['calls']==0)
            check('浏览器无未捕获 JavaScript 错误',not errors,errors)
            report={'passed':all(check['passed'] for check in checks),'checks':checks,'browser':channel,'fixture':'self-authored isolated documents','paid_requests':0}
            (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        finally:
            server.terminate();server.wait(timeout=15);log.close();client.close()
    return 0


if __name__=='__main__': raise SystemExit(main())
