"""Real browser library acceptance, against isolated files/database only."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pymupdf
from rr_services import DocumentService, Settings, open_runtime
from rr_worker import Worker

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'var/_browser_tools'))
from playwright.sync_api import sync_playwright, expect
OUT=Path(os.environ.get('RR_ACCEPTANCE_OUTPUT',str(ROOT/'docs/acceptance/2026-10-12/library-management')))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[]
    def check(name,ok=True,detail=None):
        checks.append({'name':name,'passed':bool(ok),'detail':detail})
        print(('PASS' if ok else 'FAIL')+' '+name,flush=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'var',prefix='library-e2e-',ignore_cleanup_errors=True) as temp:
        data=Path(temp);settings=Settings(data_dir=data)
        runtime=open_runtime(settings);worker=Worker(settings,runtime.workspace,runtime.queue,database=runtime.database)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        env=dict(os.environ)
        for key in list(env):
            if key.startswith('RR_TRANSLATION_') or key=='RESEARCH_READER_DB_URL':env.pop(key)
        env.update(RR_DATA_DIR=str(data),RR_PORT=str(port),RR_ENV_FILE=str(data/'no-env-file'),RR_GROBID_URL='')
        log=(OUT/'server.log').open('w',encoding='utf-8')
        server=subprocess.Popen([sys.executable,'-m','rr_api'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        client=httpx.Client(trust_env=False,timeout=30)
        try:
            for _ in range(100):
                try:
                    if client.get(url+'/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('isolated API did not start')
            paths=[]
            for index in range(2):
                pdf=pymupdf.open();page=pdf.new_page();page.insert_text((60,80),f'Library research paper {index+1}')
                page.insert_text((60,120),'The longitudinal model predicts future medical imaging data.')
                path=data/f'文献{index+1}.pdf';pdf.save(path);pdf.close();paths.append(path)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':900},locale='zh-CN')
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url)
                expect(page.locator('.library-manager')).to_be_visible()
                expect(page.locator('.library-empty')).to_contain_text('文献库为空')
                check('空库显示管理界面与导入入口',page.locator('#library-import').is_visible())
                page.screenshot(path=str(OUT/'01-empty.png'))
                page.locator('#file-input').set_input_files([str(p) for p in paths])
                expect(page.locator('.library-table tbody tr')).to_have_count(2)
                check('批量导入后停留在文献库',page.url.rstrip('/').endswith('#/') or '#/doc/' not in page.url)
                assert worker.serve(poll_seconds=0,max_jobs=2,exit_when_idle=True,idle_grace=0)==2
                page.reload();expect(page.locator('.library-table tbody tr')).to_have_count(2)
                check('重开文献库仍显示两篇原记录')
                page.locator('.library-nav-heading button').click()
                page.locator('.library-dialog input[name=name]').fill('脑影像研究')
                page.locator('.library-dialog button').filter(has_text='保存').click()
                expect(page.locator('.library-toolbar h1')).to_have_text('脑影像研究')
                page.get_by_role('button',name='全部文献').click()
                expect(page.locator('.library-table tbody tr')).to_have_count(2)
                page.locator('.library-table tbody tr').first.click()
                page.locator('input[name=title]').fill('脑影像方法研究')
                page.locator('textarea[name=authors]').fill('张三\nAlice Doe')
                page.locator('input[name=year]').fill('2025')
                page.locator('input[name=publication]').fill('科研期刊')
                page.locator('input[name=doi]').fill('10.1000/example')
                page.locator('input[name=tags]').fill('MRI，扩散模型')
                page.locator('textarea[name=notes]').fill('<b>科研笔记</b>：核对随访数据。')
                page.locator('.library-memberships label').filter(has_text='脑影像研究').locator('input').check()
                page.locator('#library-save').click()
                expect(page.locator('.library-save-message')).to_have_text('已保存')
                check('详情可编辑标题、作者、年份、来源、DOI、标签和笔记')
                check('笔记作为文本保存',page.locator('textarea[name=notes]').input_value().startswith('<b>科研笔记</b>'))
                item_id=page.locator('.library-table tbody tr.active').get_attribute('data-id')
                original=client.get(f'{url}/api/v1/documents/{item_id}/original').content
                revision=client.get(f'{url}/api/v1/documents/{item_id}').json()['active_revision_id']
                page.screenshot(path=str(OUT/'02-metadata.png'))
                search=page.get_by_label('搜索文献')
                search.fill('张三');expect(page.locator('.library-table tbody tr')).to_have_count(1)
                check('中文作者检索')
                search.fill('没有匹配的词');expect(page.locator('.library-table tbody tr')).to_have_count(0)
                search.fill('');expect(page.locator('.library-table tbody tr')).to_have_count(2)
                page.get_by_label('选择当前已加载文献').check()
                expect(page.locator('.library-batch')).to_contain_text('已选 2 篇')
                page.locator('.library-batch').get_by_role('button',name='添加标签',exact=True).click()
                page.get_by_label('批量标签').fill('待复核')
                page.locator('.library-dialog').get_by_role('button',name='确定',exact=True).click()
                expect(page.locator('.library-tags')).to_contain_text('待复核 · 2')
                check('批量添加标签')
                page.get_by_label('选择当前已加载文献').check()
                page.locator('.library-batch').get_by_role('button',name='加入分类',exact=True).click()
                page.locator('.library-dialog').get_by_role('button',name='确定',exact=True).click()
                page.locator('.library-nav-item').filter(has_text='脑影像研究').click()
                expect(page.locator('.library-table tbody tr')).to_have_count(2)
                check('批量加入分类，两篇原件仍在同一文献库')
                page.get_by_label('选择当前已加载文献').check()
                page.locator('.library-batch').get_by_role('button',name='从此分类移除',exact=True).click()
                expect(page.locator('.library-table tbody tr')).to_have_count(0)
                page.get_by_role('button',name='全部文献').click()
                expect(page.locator('.library-table tbody tr')).to_have_count(2)
                check('从分类移除保留文献')
                page.get_by_label('选择当前已加载文献').check()
                page.locator('.library-batch').get_by_role('button',name='标为已读',exact=True).click()
                expect(page.locator('.library-table tbody tr .read-status').first).to_have_text('已读')
                page.get_by_role('button',name=__import__('re').compile('^已读')).click()
                expect(page.locator('.library-table tbody tr')).to_have_count(2)
                check('批量标为已读并按状态筛选')
                page.get_by_label('选择当前已加载文献').check()
                page.locator('.library-batch').get_by_role('button',name='收藏',exact=True).click()
                page.get_by_role('button',name='我的收藏').click()
                expect(page.locator('.library-table tbody tr')).to_have_count(2)
                check('批量收藏并筛选')
                expect(page.locator('.library-manager')).not_to_have_attribute('aria-busy','true')
                page.get_by_label('选择当前已加载文献').check()
                page.locator('.library-batch').get_by_role('button',name='移入回收站',exact=True).click()
                expect(page.locator('.library-table tbody tr')).to_have_count(0)
                page.get_by_role('button',name='回收站').click()
                expect(page.locator('.library-table tbody tr')).to_have_count(2)
                check('移入回收站后显示可恢复记录')
                page.screenshot(path=str(OUT/'03-trash.png'))
                page.get_by_label('选择当前已加载文献').check()
                page.locator('.library-batch').get_by_role('button',name='恢复',exact=True).click()
                expect(page.locator('.library-table tbody tr')).to_have_count(0)
                page.get_by_role('button',name='全部文献').click()
                expect(page.locator('.library-table tbody tr')).to_have_count(2)
                check('恢复原文档和解析修订',client.get(f'{url}/api/v1/documents/{item_id}').json()['active_revision_id']==revision)
                check('恢复后原始文件指纹一致',hashlib.sha256(client.get(f'{url}/api/v1/documents/{item_id}/original').content).hexdigest()==hashlib.sha256(original).hexdigest())
                page.locator(f'tr[data-id="{item_id}"]').dblclick()
                expect(page.locator('#view-mode')).to_be_visible()
                expect(page.locator('.block-box').first).to_be_visible()
                check('双击条目进入已有对照阅读器')
                page.get_by_role('link',name='← 文献库').click()
                expect(page.locator('.library-manager')).to_be_visible()
                check('阅读后返回管理界面')
                # A genuine but intentionally unparsed PDF seed for library pagination.
                documents=DocumentService(settings,runtime.workspace,runtime.queue)
                with runtime.database.session() as session:
                    import io
                    for index in range(101):
                        pdf=pymupdf.open();p=pdf.new_page();p.insert_text((60,80),f'Pagination sample {index}');raw=pdf.tobytes();pdf.close()
                        documents.import_file(session,filename=f'pagination-{index:03d}.pdf',stream=io.BytesIO(raw),queue_parse=False)
                page.reload();expect(page.locator('.library-table tbody tr')).to_have_count(100)
                page.locator('#library-more').click();expect(page.locator('.library-table tbody tr')).to_have_count(103)
                check('超过 100 篇可继续加载完整列表')
                page.screenshot(path=str(OUT/'04-populated.png'))
                for width in [1440,1024,768,390]:
                    page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(150)
                    check(f'{width}px 文献库无整页横向溢出',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    check(f'{width}px 分类和文献详情可访问',page.locator('.library-nav').is_visible() and page.locator('.library-details').is_visible())
                    page.screenshot(path=str(OUT/f'05-responsive-{width}.png'))
                browser.close()
            report={'checks':checks,'errors':errors,'passed':all(c['passed'] for c in checks) and not errors,
                    'scope':'隔离数据库和真实生成的测试 PDF；分页样例未解析；没有修改用户文献或调用外部翻译服务'}
            (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('BROWSER LIBRARY '+('PASS' if report['passed'] else 'FAIL'),flush=True)
            return 0 if report['passed'] else 1
        finally:
            client.close();server.terminate();server.wait(timeout=15);log.close();runtime.database.dispose()


if __name__=='__main__':raise SystemExit(main())
