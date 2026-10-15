"""Read-only live Zotero/browser acceptance. No native records are created or edited."""
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'var/_browser_tools'))
from playwright.sync_api import sync_playwright,expect

OUT=Path(os.environ.get('RR_ZOTERO_ACCEPTANCE_OUTPUT',str(ROOT/'var/zotero-browser')))
BASE=os.environ.get('RR_ZOTERO_ACCEPTANCE_URL','http://127.0.0.1:8765').rstrip('/')


def main():
    OUT.mkdir(parents=True,exist_ok=True);checks=[];errors=[]
    with urllib.request.urlopen(BASE+'/api/v1/zotero/status',timeout=10) as response:status=json.load(response)
    assert status.get('can_write'),status.get('message','Zotero pairing unavailable')
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(BASE+'/#/')
        page.get_by_role('button',name='Zotero 文献库',exact=True).click()
        dialog=page.get_by_role('dialog',name='Zotero 文献库',exact=True)
        expect(dialog).not_to_have_attribute('aria-busy','true',timeout=30000)
        with urllib.request.urlopen(BASE+f'/api/v1/zotero/items?library_id={status["user_library_id"]}&limit=100',timeout=10) as response:catalog=json.load(response)
        expect(dialog.locator('.zotero-row')).to_have_count(min(catalog['total'],100))
        checks.append({'name':'真实 Zotero 书目在浏览器可见','passed':catalog['total']>0,'total':catalog['total']})
        expect(dialog.locator('.zotero-message')).to_contain_text('已同步本机目录')
        for width,height in [(1440,1000),(768,1024),(390,844)]:
            page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(120)
            size=dialog.evaluate('(d)=>({w:d.getBoundingClientRect().width,h:d.getBoundingClientRect().height,sw:d.scrollWidth,cw:d.clientWidth})')
            info=dialog.locator('.zotero-info').evaluate('(d)=>({w:d.clientWidth,h:d.clientHeight})')
            checks.append({'name':f'{width}px Zotero 窗口不超出屏幕，详情可访问','passed':size['w']<=width and size['h']<=height and size['sw']<=size['cw']+2 and info['w']>200 and info['h']>100,'geometry':{'dialog':size,'info':info}})
            page.screenshot(path=str(OUT/f'catalog-{width}.png'))
        page.set_viewport_size({'width':1440,'height':1000})
        title=dialog.locator('.zotero-row strong').first.inner_text()
        checks.append({'name':'Zotero 富文本标题不显示 HTML 标签','passed':'<b>' not in title and '<i>' not in title})
        dialog.get_by_label('搜索 Zotero').fill(title[:15]);expect(dialog.locator('.zotero-row').first.locator('strong')).to_contain_text(title[:15])
        checks.append({'name':'真实目录搜索可用','passed':True})
        dialog.locator('.zotero-row input[type=checkbox]').first.check()
        with page.expect_download() as result:dialog.get_by_role('link',name='导出 RIS',exact=True).click()
        download=result.value;download.save_as(str(OUT/'selected-export.ris'))
        text=(OUT/'selected-export.ris').read_text(encoding='utf-8')
        checks.append({'name':'浏览器下载真实 RIS 导出','passed':'TY  -' in text and 'ER  -' in text})
        dialog.get_by_role('button',name='关闭',exact=True).click()
        page.locator('.library-table tbody tr').first.click()
        page.get_by_role('button',name='Zotero 关联与写回',exact=True).click()
        write=page.get_by_role('dialog',name='Zotero 关联与写回',exact=True)
        expect(write.get_by_label('写入目标文献库')).to_be_visible()
        checks.append({'name':'写回前明确展示目标库和可选 PDF 复制','passed':write.get_by_text('同时复制 PDF 到 Zotero',exact=False).is_visible()})
        page.screenshot(path=str(OUT/'write-preview.png'))
        write.get_by_role('button',name='关闭',exact=True).click()
        browser.close()
    report={'checks':checks,'errors':errors,'passed':all(c['passed'] for c in checks) and not errors,
        'native_library_modified':False,'zotero_version':status.get('zotero_version'),'bridge_version':status.get('bridge_version')}
    (OUT/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
