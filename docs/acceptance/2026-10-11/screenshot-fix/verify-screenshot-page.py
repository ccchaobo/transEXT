import sys,json,os
from pathlib import Path
sys.path.insert(0,str(Path("var/_browser_tools").resolve()))
import httpx
from playwright.sync_api import sync_playwright,expect
out=Path("docs/acceptance/2026-10-11/screenshot-fix");out.mkdir(parents=True,exist_ok=True)
url="http://127.0.0.1:8765"; docid="doc_255875e98bb443fbb29a"
checks=[]
def check(name,ok,detail=None):
    checks.append(dict(name=name,status="PASS" if ok else "FAIL",detail=detail))
    print(name,ok,detail)
with httpx.Client(trust_env=False) as client:
    payload=client.get(f"{url}/api/v1/documents/{docid}/pages/3?target_language=zh").json()
    numbers=[b for b in payload["blocks"] if b["text"] in ["2","2.1","2.2","4"]]
    check("真实第4页编号不可翻译",len(numbers)==4 and all(not b["translatable"] for b in numbers))
    allpages=[client.get(f"{url}/api/v1/documents/{docid}/pages/{i}?target_language=zh").json() for i in range(10)]
    regions=[dict(index=i,regions=p["page"]["visual_regions"],protected_text=sum(not b["translatable"] for b in p["blocks"])) for i,p in enumerate(allpages)]
    (out/"page-regions.json").write_text(json.dumps(regions,ensure_ascii=False,indent=2),encoding="utf-8")
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel="msedge",headless=True)
    context=browser.new_context(viewport={"width":1920,"height":1000},device_scale_factor=1)
    current=[3]
    def guard(route):
        req=route.request
        if req.method not in ["GET","HEAD"]:
            route.fulfill(status=200,content_type="application/json",body="{}")
        elif req.url.endswith("/reading-state"):
            route.fulfill(status=200,content_type="application/json",body=json.dumps({"reading_state":{"page_index":current[0],"view_mode":"overlay_side_by_side","zoom":1}}))
        else:route.continue_()
    context.route("**/api/**",guard)
    context.add_init_script("document.addEventListener('DOMContentLoaded',()=>document.body.style.zoom='1.1')")
    page=context.new_page();errors=[];page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto(f"{url}/#/doc/{docid}")
    expect(page.locator(".layout-block").first).to_be_visible()
    page.wait_for_timeout(500)
    ids=page.locator(".layout-block").evaluate_all("nodes=>nodes.map(el=>el.dataset.blockId)")
    check("旧编号错误译文未被绘制",all(b["id"] not in ids for b in numbers))
    check("竖排提示语消失",not page.locator("#target-body").inner_text().__contains__("请提供需要翻译"))
    geometry=page.locator(".layout-block").evaluate_all("nodes=>nodes.map(el=>({width:el.clientWidth,height:el.clientHeight,scroll:el.scrollHeight,overflow:getComputedStyle(el).overflowY,text:el.innerText.slice(0,20)}))")
    check("真实第4页译文无极窄区域及内部滚动",all(g["width"]>100 and g["scroll"]<=g["height"]+2 and g["overflow"]=="visible" for g in geometry),geometry)
    heading=page.locator('[data-block-id="blk_dcee17b24d8f583af70e"].layout-block')
    delta=heading.evaluate("el=>{const band=el.closest('.layout-band');return el.getBoundingClientRect().top-band.getBoundingClientRect().top}")
    check("真实标题保持原位没有额外上边距",abs(delta)<2,delta)
    page.screenshot(path=str(out/"page4-110percent.png"))
    graphic_index=max(range(10),key=lambda i:sum((r[2]-r[0])*(r[3]-r[1]) for r in allpages[i]["page"]["visual_regions"]))
    current[0]=graphic_index;page.reload();expect(page.locator(".layout-band").first).to_be_visible();page.wait_for_timeout(500)
    cuts=page.locator(".layout-band").evaluate_all("nodes=>nodes.map(el=>[Number(el.dataset.sourceStart),Number(el.dataset.sourceEnd)])")
    check("真实图形页画面连续完整保留",cuts[0][0]==0 and cuts[-1][1]==1 and all(abs(cuts[i][1]-cuts[i+1][0])<.00001 for i in range(len(cuts)-1)))
    check("真实图形页的图未切断",all(any(lo<=r[1]+.0001 and hi>=r[3]-.0001 for lo,hi in cuts) for r in allpages[graphic_index]["page"]["visual_regions"]),{"page_number":graphic_index+1})
    protected=[b for b in allpages[graphic_index]["blocks"] if not b["translatable"]]
    overlay_ids=page.locator(".layout-block").evaluate_all("nodes=>nodes.map(el=>el.dataset.blockId)")
    check("真实图内文字未被译文覆盖",all(b["id"] not in overlay_ids for b in protected),{"protected_count":len(protected)})
    check("图外图注仍可翻译",any(b["type"]=="caption" and b["translatable"] for b in allpages[graphic_index]["blocks"]))
    page.screenshot(path=str(out/"graphics-page.png"))
    check("浏览器无运行异常",not errors,errors)
    browser.close()
(out/"report.json").write_text(json.dumps({"checks":checks,"read_only":True,"zoom":"CSS 110%", "external_model_requests":0},ensure_ascii=False,indent=2),encoding="utf-8")
raise SystemExit(any(c["status"]=="FAIL" for c in checks))
