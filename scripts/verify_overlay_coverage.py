"""Read-only browser audit of successful translations across three reader modes."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'var/_browser_tools'))
from playwright.sync_api import sync_playwright, expect

MEASURE = r'''root => {
  const body=document.querySelector(root);
  const translations=[...body.querySelectorAll('.overlay-panel,.overlay-fallback,.card')].map(n=>{
    const text=n.querySelector('.overlay-text,.tgt');
    const r=n.getBoundingClientRect();
    return {id:n.dataset.blockId,text:text?.textContent || '',fallback:n.classList.contains('overlay-fallback'),
      frame:n.classList.contains('overlay-panel')?[parseFloat(n.style.left)/100,parseFloat(n.style.top)/100,
        (parseFloat(n.style.left)+parseFloat(n.style.width))/100,(parseFloat(n.style.top)+parseFloat(n.style.height))/100]:null,
      width:r.width,height:r.height,font:parseFloat(getComputedStyle(n).fontSize),overflowing:n.dataset.overflowing==='1'};
  });
  const shells=[...document.querySelectorAll('.page-shell')].map(n=>{
    const r=n.getBoundingClientRect();return {width:r.width,height:r.height,top:r.top};
  });
  return {translations,shells,bodyOverflow:document.documentElement.scrollWidth>innerWidth+1};
}'''


def overlaps(a, b):
    return min(a[2], b[2])-max(a[0], b[0]) > .00051 and min(a[3], b[3])-max(a[1], b[1]) > .00051


def normalized(text):
    return ''.join(text.split())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--document', default='doc_ee94e17b17e84072a321')
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--browser-channel', default='msedge')
    parser.add_argument('--output', type=Path, default=ROOT/'docs/acceptance/2026-10-13/overlay-coverage')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(trust_env=False, timeout=30)
    prefix = f'{args.base_url}/api/v1/documents/{args.document}'
    doc = client.get(prefix).raise_for_status().json()
    original_hash = hashlib.sha256(client.get(prefix+'/original').raise_for_status().content).hexdigest()
    payloads = [client.get(f'{prefix}/pages/{i}?target_language=zh').raise_for_status().json() for i in range(doc['page_count'])]
    report = {'document':args.document,'filename':doc['filename'],'browser_channel':args.browser_channel,
              'original_hash_matches':original_hash == doc['sha256'].removeprefix('sha256:'),
              'pages':[],'responsive':[],'errors':[],'blocked_writes':[], 'fallback_dialog':False}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.browser_channel, headless=True)
        report['browser_version'] = browser.version
        context = browser.new_context(viewport={'width':1920,'height':1080},device_scale_factor=1,locale='zh-CN')
        def readonly(route):
            request = route.request
            if request.method != 'GET':
                report['blocked_writes'].append({'method':request.method,'path':request.url.split('/api/v1/')[1]})
                route.fulfill(status=200,json={'reading_state':None})
            elif request.url.split('?')[0].endswith('/reading-state'):
                route.fulfill(status=200,json={'reading_state':{'page_index':0,'view_mode':'overlay_side_by_side','link_scrolling':False}})
            else:
                route.continue_()
        context.route('**/api/v1/**', readonly)
        page = context.new_page()
        page.on('pageerror',lambda error:report['errors'].append(str(error)))
        page.goto(f'{args.base_url}/#/doc/{args.document}')
        for index,payload in enumerate(payloads):
            expect(page.locator('#footer-note')).to_contain_text(f'内部页索引 {index} ·')
            # Eligibility is based on semantic type and actual translation state,
            # not on whether comparisonLayout happened to emit a rectangle.
            expected={b['id']:b for b in payload['blocks'] if b.get('translatable') and
                      b['type'] not in {'formula','table','table_cell','figure'} and
                      b.get('translation',{}).get('status')=='ok' and b['translation'].get('text') and
                      b['translation']['text'].strip()!=b['text'].strip() and
                      not all(c.isdigit() or c.isspace() or not c.isalnum() for c in b['text'])}
            protected=[]
            for box in payload['page'].get('visual_regions',[]):
                pad=.025 if (box[2]-box[0])*(box[3]-box[1])>.005 else 0
                protected.append([max(0,box[0]-.004),max(0,box[1]-pad),min(1,box[2]+.004),min(1,box[3]+pad)])
            for block in payload['blocks']:
                if block['type'] in {'formula','table','table_cell','figure'}:
                    protected.extend(s['bbox'] for s in block['spans'] if s['page_index']==index)
            result={'index':index,'successful_translations':len(expected),'modes':{}}
            for mode in ['side_by_side','overlay','overlay_side_by_side']:
                page.locator('#view-mode').select_option(mode)
                # Pane scrolling is deliberately independent. Compare page
                # origins with both panes at the same scroll position.
                page.evaluate("['source-body','target-body'].forEach(id=>document.getElementById(id).scrollTop=0)")
                page.evaluate('document.fonts.ready')
                page.wait_for_timeout(100)
                root='#source-body' if mode=='overlay' else '#target-body'
                measured=page.evaluate(MEASURE,root)
                shown={n['id']:n for n in measured['translations']}
                missing=[key for key in expected if key not in shown]
                incomplete=[key for key,b in expected.items() if key in shown and normalized(shown[key]['text'])!=normalized(b['translation']['text'])]
                frames=[n for n in measured['translations'] if n['frame']]
                collisions=[[a['id'],b['id']] for i,a in enumerate(frames) for b in frames[i+1:] if overlaps(a['frame'],b['frame'])]
                protected_collisions=[n['id'] for n in frames if any(overlaps(n['frame'],box) for box in protected)]
                unreadable=[n['id'] for n in frames if n['height']<n['font'] or n['width']<n['font']]
                aligned=True
                if mode=='overlay_side_by_side':
                    shells=measured['shells']
                    aligned=len(shells)==2 and all(abs(shells[0][k]-shells[1][k])<2 for k in ['width','height','top'])
                data={'shown':len(expected)-len(missing),'overlays':len(frames),
                      'fallbacks':[n['id'] for n in measured['translations'] if n['fallback']],
                      'missing':missing,'incomplete':incomplete,'collisions':collisions,
                      'protected_collisions':protected_collisions,'unreadable':unreadable,'aligned':aligned}
                data['passed']=not any([missing,incomplete,collisions,protected_collisions,unreadable]) and aligned
                result['modes'][mode]=data
                if index==4 and mode in {'overlay','overlay_side_by_side'}:
                    target=page.locator(f'{root} .overlay-panel[data-block-id="blk_87617b4d3b1c0152cb09"]')
                    expect(target).to_be_visible()
                    target.scroll_into_view_if_needed()
                    page.screenshot(path=str(args.output/f'page-05-{mode}.png'))
                if index==2 and mode=='overlay_side_by_side' and data['fallbacks']:
                    card=page.locator(f'{root} .overlay-fallback').first
                    card.scroll_into_view_if_needed()
                    page.screenshot(path=str(args.output/'page-03-fallback.png'))
                    block_id=card.get_attribute('data-block-id')
                    card.locator('button').click()
                    dialog=page.locator('.full-block-dialog')
                    expect(dialog).to_be_visible()
                    report['fallback_dialog']=normalized(expected[block_id]['translation']['text']) in normalized(dialog.inner_text())
                    dialog.locator('button').first.click()
            report['pages'].append(result)
            (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"page {index+1}: translations={len(expected)} PASS={all(m['passed'] for m in result['modes'].values())}",flush=True)
            if index+1<len(payloads):page.locator('#next-page').click()
        # Revisit the exact reported paragraph at different widths and font sizes.
        for index in range(len(payloads)-2,3,-1):
            page.locator('#previous-page').click()
            expect(page.locator('#footer-note')).to_contain_text(f'内部页索引 {index} ·')
        page.locator('#view-mode').select_option('overlay')
        for width,scale in [(1745,'100'),(1440,'100'),(1024,'100'),(1440,'120')]:
            page.set_viewport_size({'width':width,'height':980})
            page.locator('#overlay-font-scale').select_option(scale)
            page.wait_for_timeout(150)
            target=page.locator('#source-body .overlay-panel[data-block-id="blk_87617b4d3b1c0152cb09"]')
            expect(target).to_be_visible()
            expect(target).to_contain_text('虽然 Self-Rubrics')
            target.scroll_into_view_if_needed()
            measure=page.evaluate(MEASURE,'#source-body')
            target_data=next(n for n in measure['translations'] if n['id']=='blk_87617b4d3b1c0152cb09')
            report['responsive'].append({'width':width,'font_scale':scale,'target':target_data,'bodyOverflow':measure['bodyOverflow']})
            page.screenshot(path=str(args.output/f'page-05-{width}-font{scale}.png'))
        page.locator('#overlay-original-toggle').click()
        report['original_toggle']=page.locator('#source-body .overlay-panel:visible,#source-body .overlay-fallbacks:visible').count()==0
        browser.close()
    report['passed']=report['original_hash_matches'] and not report['errors'] and report['original_toggle'] and report['fallback_dialog'] and all(
        mode['passed'] for p in report['pages'] for mode in p['modes'].values())
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'errors':report['errors'],'pages':len(report['pages'])},ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
