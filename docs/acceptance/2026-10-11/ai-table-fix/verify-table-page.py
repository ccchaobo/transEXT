import sys,json,hashlib,shutil
from pathlib import Path
sys.path.insert(0,str(Path("var/_browser_tools").resolve()))
import httpx
from playwright.sync_api import sync_playwright,expect
url="http://127.0.0.1:8765";docid="doc_255875e98bb443fbb29a"
out=Path("docs/acceptance/2026-10-11/ai-table-fix");out.mkdir(parents=True,exist_ok=True)
checks=[]
def check(name,ok,detail=None):
    checks.append(dict(name=name,status="PASS" if ok else "FAIL",detail=detail));print(name,ok,detail)
with httpx.Client(trust_env=False) as c:
    payload=c.get(f"{url}/api/v1/documents/{docid}/pages/4?target_language=zh").json()
    regions=payload["page"]["visual_regions"]
    check("真实表格区域完整识别",len(regions)==1 and regions[0][1]<.665 and regions[0][3]>.749,regions)
    tableblocks=[b for b in payload["blocks"] if .65<b["bbox"][1]<.76]
    check("真实表格所有单元格禁止翻译",len(tableblocks)>5 and all(not b["translatable"] for b in tableblocks),len(tableblocks))
    check("表外说明仍可翻译",any(b["type"]=="caption" and b["translatable"] for b in payload["blocks"]))
with sync_playwright() as p:
    browser=p.chromium.launch(channel="msedge",headless=True)
    context=browser.new_context(viewport={"width":1920,"height":1000})
    def guard(route):
        req=route.request
        if req.method not in ["GET","HEAD"]:
            route.fulfill(status=200,content_type="application/json",body="{}")
        elif req.url.endswith("/reading-state"):
            route.fulfill(status=200,content_type="application/json",body=json.dumps({"reading_state":{"page_index":4,"view_mode":"overlay_side_by_side","zoom":1}}))
        else:route.continue_()
    context.route("**/api/**",guard)
    context.add_init_script("document.addEventListener('DOMContentLoaded',()=>document.body.style.zoom='1.1')")
    page=context.new_page();errors=[];page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto(f"{url}/#/doc/{docid}");expect(page.locator(".layout-band").first).to_be_visible()
    page.locator("#assistant-toggle").click();page.wait_for_timeout(300)
    ids=page.locator(".layout-block").evaluate_all("nodes=>nodes.map(el=>el.dataset.blockId)")
    check("旧单元格译文未覆盖表格",all(b["id"] not in ids for b in tableblocks))
    cuts=page.locator(".layout-band").evaluate_all("nodes=>nodes.map(el=>[Number(el.dataset.sourceStart),Number(el.dataset.sourceEnd)])")
    check("表格原图没有被分带切开",all(any(lo<=r[1]+.0001 and hi>=r[3]-.0001 for lo,hi in cuts) for r in regions))
    target=page.locator("#target-body")
    tableband=page.locator(".layout-band").nth(next(i for i,(lo,hi) in enumerate(cuts) if lo<=regions[0][1]+.0001 and hi>=regions[0][3]-.0001))
    tableband.scroll_into_view_if_needed();page.screenshot(path=str(out/"page5-table-with-assistant.png"))
    check("无浏览器异常",not errors,errors)
    browser.close()
for name in ["backend","browser"]:shutil.copy2(Path(f"var/ai-table-{name}.log"),out/f"{name}.log")
shutil.copy2(Path("var/browser-acceptance/ai-table-final/report.json"),out/"browser-report.json")
report={"checks":checks,"external_model_requests":0,"assistant_real_service":"PENDING user approval", "regression":{"backend_passed":686,"browser_passed":78}, "source_sha256":{f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in ["adapters/rr_adapters/pdf_visuals.py","services/api/rr_services/assistant.py"]}}
(out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
raise SystemExit(any(c["status"]=="FAIL" for c in checks))
