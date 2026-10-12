"""Inspect all pages of the user's real paper in a fresh headless Chrome session."""
import json
import sys
import hashlib
import argparse
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'var/_browser_tools'))
from playwright.sync_api import sync_playwright, expect

DOC='doc_7f670dd5a6dd4b95a3ee'
BASE='http://127.0.0.1:8765'
OUT=ROOT/'docs/acceptance/2026-10-12/layout-alignment'
MEASURE=r'''() => {
  const shells=[...document.querySelectorAll('.page-shell')];
  const boxes=shells.map(n=>{const r=n.getBoundingClientRect();return {w:n.clientWidth,h:n.clientHeight,top:r.top};});
  const panels=[...document.querySelectorAll('.layout-block')].map(n=>({id:n.dataset.blockId,
    left:parseFloat(n.style.left)/100,top:parseFloat(n.style.top)/100,
    width:parseFloat(n.style.width)/100,height:parseFloat(n.style.height)/100,
    pixels:n.clientWidth,font:parseFloat(getComputedStyle(n).fontSize),
    pixelHeight:n.clientHeight,
    overflowing:n.dataset.overflowing==='1',text:n.textContent}));
  const collisions=[];
  for(let i=0;i<panels.length;i++)for(let j=i+1;j<panels.length;j++) {
    const a=panels[i],b=panels[j];
    const w=Math.min(a.left+a.width,b.left+b.width)-Math.max(a.left,b.left);
    const h=Math.min(a.top+a.height,b.top+b.height)-Math.max(a.top,b.top);
    if(w>.002 && h>.002)collisions.push([a.id,b.id,w,h]);
  }
  return {boxes,panels,collisions,bodyOverflow:document.documentElement.scrollWidth>innerWidth+1};
}'''


def main():
    global DOC,BASE,OUT
    parser=argparse.ArgumentParser();parser.add_argument('--document',default=DOC);parser.add_argument('--base-url',default=BASE);parser.add_argument('--output',type=Path,default=OUT);parser.add_argument('--browser-channel',default='chrome')
    args=parser.parse_args();DOC=args.document;BASE=args.base_url;OUT=args.output
    OUT.mkdir(parents=True,exist_ok=True)
    client=httpx.Client(trust_env=False,timeout=30)
    doc=client.get(f'{BASE}/api/v1/documents/{DOC}').json()
    original=client.get(f'{BASE}/api/v1/documents/{DOC}/original').content
    report={'document':DOC,'filename':doc['filename'],'revision':doc['active_revision_id'],
            'original_hash_matches':hashlib.sha256(original).hexdigest()==doc['sha256'].removeprefix('sha256:'),
            'translation_status':doc['translate_status'],'pages':[],'errors':[],
            'screenshots':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=args.browser_channel,headless=True)
        report['browser_channel']=args.browser_channel
        report['browser']=browser.version
        # Layout is measured in CSS pixels; rasterise at 1x for stable capture.
        report['viewport']={'width':2320,'height':1240,'raster_scale':1}
        context=browser.new_context(viewport={'width':2320,'height':1240},device_scale_factor=1,locale='zh-CN')
        page=context.new_page()
        page.on('pageerror',lambda e:report['errors'].append(str(e)))
        page.goto(f'{BASE}/#/doc/{DOC}')
        page.locator('#view-mode').select_option('overlay_side_by_side')
        while page.locator('#previous-page').is_enabled():
            before=page.locator('#footer-note').inner_text();index=int(before.split('内部页索引 ')[1].split(' ')[0])
            page.locator('#previous-page').click();expect(page.locator('#footer-note')).to_contain_text(f'内部页索引 {max(0,index-1)} ·')
        for index in range(doc['page_count']):
            expect(page.locator('#footer-note')).to_contain_text(f'内部页索引 {index} ·')
            page.evaluate('document.fonts.ready');page.wait_for_timeout(100)
            data=page.evaluate(MEASURE)
            payload=client.get(f'{BASE}/api/v1/documents/{DOC}/pages/{index}?target_language=zh').json()
            blocks={b['id']:b for b in payload['blocks']}
            regions=payload['page'].get('visual_regions',[])
            data['graphic_collisions']=[]
            data['coordinate_error']=0
            data['narrow_prose']=[]
            data['unusable_frames']=[]
            for panel in data['panels']:
                block=blocks[panel['id']]
                spans=[s['bbox'] for s in block['spans'] if s['page_index']==index]
                data['coordinate_error']=max(data['coordinate_error'],min(abs(panel['top']-s[1]) for s in spans))
                if len(panel['text'])>100 and panel['width']<.12:data['narrow_prose'].append(panel['id'])
                if panel['pixelHeight']<panel['font'] or panel['pixels']<panel['font']:data['unusable_frames'].append(panel['id'])
                for r in regions:
                    w=min(panel['left']+panel['width'],r[2])-max(panel['left'],r[0])
                    h=min(panel['top']+panel['height'],r[3])-max(panel['top'],r[1])
                    if w>.002 and h>.002:data['graphic_collisions'].append([panel['id'],r,w,h])
            for panel in data['panels']:panel.pop('text')
            data['index']=index
            data['aligned']=len(data['boxes'])==2 and abs(data['boxes'][0]['w']-data['boxes'][1]['w'])<=1 and abs(data['boxes'][0]['h']-data['boxes'][1]['h'])<=2 and abs(data['boxes'][0]['top']-data['boxes'][1]['top'])<1
            report['pages'].append(data)
            if index in {0,2,4,8,10,14,20}:
                name=f'page-{index+1:02d}-large.png'
                page.screenshot(path=str(OUT/name),timeout=45000);report['screenshots'].append(name)
            print(f"page {index+1}: aligned={data['aligned']} panels={len(data['panels'])} overlap={len(data['collisions'])} graphics={len(data['graphic_collisions'])}",flush=True)
            if index+1<doc['page_count']:page.locator('#next-page').click()
        # Restore the screenshot's page, then verify narrower panes and font controls.
        for index in range(doc['page_count']-2,-1,-1):
            page.locator('#previous-page').click()
            expect(page.locator('#footer-note')).to_contain_text(f'内部页索引 {index} ·')
        report['responsive']=[]
        for width in [1440,1024,768]:
            page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(150)
            data=page.evaluate(MEASURE);data['viewport']=width
            report['responsive'].append(data)
            page.screenshot(path=str(OUT/f'page-01-{width}.png'))
        page.set_viewport_size({'width':1920,'height':1080})
        page.locator('#assistant-toggle').click();page.wait_for_timeout(150)
        report['assistant']=page.evaluate(MEASURE)
        page.screenshot(path=str(OUT/'page-01-assistant.png'))
        page.locator('#assistant-toggle').click()
        page.locator('#overlay-original-toggle').click()
        report['original_toggle']=page.locator('#target-body .layout-block').count()==0
        page.locator('#overlay-original-toggle').click()
        page.locator('#target-body .layout-block').first.dblclick()
        report['full_paragraph']=page.locator('.full-block-dialog').is_visible()
        page.locator('.full-block-dialog button').first.click()
        page.locator('#view-mode').select_option('overlay')
        report['single_overlay_panels']=page.locator('#source-body .layout-block').count()
        page.locator('#view-mode').select_option('overlay_side_by_side')
        page.wait_for_timeout(150)
        page.screenshot(path=str(OUT/'page-01-final.png'))
        browser.close()
    report['passed']=report['original_hash_matches'] and not report['errors'] and all(
        p['aligned'] and not p['collisions'] and not p['graphic_collisions'] and not p['narrow_prose'] and not p['unusable_frames'] and p['coordinate_error']<.0001 for p in report['pages'])
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'pages','responsive','assistant'}},ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
