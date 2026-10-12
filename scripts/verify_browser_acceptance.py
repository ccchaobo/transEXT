"""真实浏览器验收；隔离数据库，测试译文仅为布局样例，不调用外部翻译服务。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "var" / "_browser_tools"))
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select
from document_core.models import BlockType
from rr_services import Settings, open_runtime
from rr_storage import Block, Database, Translation
from rr_worker import Worker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="msedge")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--web-root", type=Path, help="验收隔离前端副本，不改变产品源代码")
    args = parser.parse_args()
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    output = (args.output or ROOT / "var" / "browser-acceptance" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=True)
    results, errors = [], []

    def check(name, ok, detail=None):
        results.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail or ''}", flush=True)

    env = dict(os.environ)
    for key in list(env):
        if key.startswith("RR_TRANSLATION_") or key == "RESEARCH_READER_DB_URL":
            env.pop(key)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    report = {"browser_channel": args.channel, "checks": results, "page_errors": errors,
              "translation_fixture": "合成界面样例，不验证翻译质量", "screenshots": []}
    with tempfile.TemporaryDirectory(prefix="rr-browser-", dir=ROOT / "var", ignore_cleanup_errors=True) as tmp:
        data = Path(tmp).resolve()
        env.update(RR_DATA_DIR=str(data), RR_PORT=str(port), RR_ENV_FILE=str(data / "no-env-file"))
        log = (output / "server.log").open("w", encoding="utf-8")
        command = [sys.executable, "-m", "rr_api"]
        if args.web_root:
            command = [sys.executable, "-c", "import sys; from pathlib import Path; import rr_api.app as app; app.WEB_ROOT=Path(sys.argv[1]); app.main()", str(args.web_root.resolve())]
        server = subprocess.Popen(command, cwd=ROOT, env=env,
                                  stdout=log, stderr=subprocess.STDOUT)
        db = None
        try:
            import httpx
            with httpx.Client(trust_env=False) as client:
                for attempt in range(100):
                    try:
                        if client.get(url + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if server.poll() is not None:
                        raise RuntimeError("隔离 API 启动失败，查看 server.log")
                    time.sleep(.1)
                else:
                    raise RuntimeError("隔离 API 启动超时")
            settings = Settings(data_dir=data)
            db = Database(settings.database_url)
            runtime = open_runtime(settings, database=db)
            worker = Worker(settings, runtime.workspace, runtime.queue, database=db)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel=args.channel, headless=True)
                report["browser_version"] = browser.version
                context = browser.new_context(viewport={"width": 1440, "height": 900},
                                              locale="zh-CN", accept_downloads=True)
                page = context.new_page()
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.set_default_timeout(12000)

                def shot(name):
                    page.screenshot(path=str(output / f"{name}.png"))
                    report["screenshots"].append(f"{name}.png")

                def visible_controls():
                    return page.locator("#view-mode").evaluate("""el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;
                    }""")

                page.goto(url)
                expect(page.locator(".library")).to_be_visible()
                expect(page.locator(".library .empty")).to_contain_text("还没有文献")
                check("空文献库不显示无效加载更多按钮", not page.locator(".library > .button").is_visible())
                check("空文献库显示导入入口", page.locator(".file-button").is_visible())
                shot("01-library")
                with page.expect_response(lambda response: response.url.endswith("/api/v1/documents") and response.request.method == "POST") as upload:
                    with page.expect_file_chooser() as chooser:
                        page.locator(".file-button").press("Enter")
                    chooser.value.set_files(str(ROOT / "benchmarks/corpus/files/pdf_b_two_column.pdf"))
                check("导入入口可通过键盘打开文件选择", True)
                document_id = upload.value.json()["document"]["id"]
                expect(page.locator("#source-body")).to_contain_text("正在等待解析结果")
                check("导入后显示解析等待状态", page.locator("#translate-button").is_disabled())
                shot("02-import-waiting")
                page.reload()
                expect(page.locator("#source-body")).to_contain_text("正在等待解析结果")
                check("重开排队文献后继续等待", True)
                assert worker.serve(poll_seconds=0, max_jobs=1) == 1
                expect(page.locator(".block-box").first).to_be_visible()
                expect(page.locator("#page-label")).to_contain_text("/ 2 页")
                check("真实 worker 解析完成后自动显示正文和目录", page.locator(".outline-item").count() > 0)

                with db.session() as session:
                    blocks = list(session.scalars(select(Block).where(Block.document_id == document_id).order_by(Block.order_index)))
                    for index, block in enumerate(blocks):
                        if not BlockType(block.type).is_translatable:
                            continue
                        text = f"【界面验收样例 {index + 1}】本段用于检查论文阅读中的文字排版、原文定位和双向对照，不代表真实翻译结果。" * 2
                        session.add(Translation(id=f"ui_fixture_{index}", document_id=document_id,
                            block_id=block.id, revision_id=block.revision_id, source_hash=block.text_hash,
                            provider="ui-layout-fixture", model="synthetic-ui-only", cache_key=hashlib.sha256(block.id.encode()).hexdigest(),
                            text=text, status="ok", origin="human"))
                    long_block = next(block.id for block in blocks if block.type == "paragraph" and block.page_index == 0)
                page.reload()
                expect(page.locator(".card .tgt").first).to_contain_text("界面验收样例")
                page.wait_for_function("Array.from(document.images).every(im => im.complete && im.naturalWidth > 0)")
                shot("03-desktop-side-by-side")
                dimensions = page.evaluate("""() => ({
                    viewport: [innerWidth, innerHeight], body: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
                    source: [document.querySelector('#source-body').clientHeight, document.querySelector('#source-body').scrollHeight],
                    target: [document.querySelector('#target-body').clientHeight, document.querySelector('#target-body').scrollHeight]
                })""")
                check("桌面阅读区域受视口约束", dimensions["body"][1] <= 902, dimensions)
                check("译文面板独立滚动", dimensions["target"][1] > dimensions["target"][0], dimensions["target"])
                if page.locator("#link-scrolling").is_enabled():
                    page.locator("#link-scrolling").uncheck()
                target = page.locator("#target-body")
                target.evaluate("el => el.scrollTop = el.scrollHeight")
                page.wait_for_timeout(150)
                check("末尾译文可滚动到可视区域", target.locator(".card").last.evaluate("""el => {
                    const r=el.getBoundingClientRect(), p=el.parentElement.getBoundingClientRect();
                    return r.bottom <= p.bottom + 2 && r.bottom <= innerHeight;
                }"""))
                target.evaluate("el => el.scrollTop = 0")
                for mode in ["overlay", "source_only", "target_only", "stacked", "side_by_side"]:
                    page.locator("#view-mode").select_option(mode)
                    page.wait_for_timeout(150)
                    check(f"视图 {mode} 切换控件始终可达", visible_controls())
                    if mode == "overlay":
                        actual = page.locator(".reader").get_attribute("data-mode")
                        check("覆盖视图长译文保持覆盖并提示完整阅读入口", actual == "overlay" and "段落内滚动" in page.locator("#banner").inner_text(), actual)
                        check("覆盖视图确实绘制了译文", page.locator(".overlay-panel").count() > 1)
                        page.locator(".overlay-panel").last.click()
                        check("覆盖选中段落置于上层", page.locator(".overlay-panel.is-selected").evaluate("el => getComputedStyle(el).zIndex") == "10")
                        page.locator("#overlay-original-toggle").click()
                        check("覆盖视图一键显示原文", page.locator(".overlay-panel").last.is_visible() is False)
                        page.locator("#overlay-original-toggle").click()
                        check("覆盖视图一键恢复译文", page.locator(".overlay-panel").last.is_visible())
                        before_font = page.locator(".overlay-panel").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
                        page.locator("#overlay-font-scale").select_option("120")
                        page.wait_for_timeout(150)
                        check("覆盖译文字号可调整", page.locator("#overlay-font-scale").input_value() == "120")
                        check("手动放大字号实际改变文字大小", page.locator(".overlay-panel").first.evaluate(
                            "el => parseFloat(getComputedStyle(el).fontSize)") > before_font)
                        page.locator("#overlay-font-scale").select_option("100")
                    shot(f"04-mode-{mode}")
                with db.session() as session:
                    fixture_rows = list(session.scalars(select(Translation).join(Block, Translation.block_id == Block.id)
                        .where(Block.document_id == document_id, Block.page_index == 0)))
                    original_texts = {row.id: row.text for row in fixture_rows}
                    for row in fixture_rows:
                        row.text = ""
                    fixture_rows[0].text = "【短译文验收】论文标题"
                page.reload()
                expect(page.locator(".card .tgt").first).to_contain_text("短译文验收")
                page.locator("#view-mode").select_option("overlay")
                page.wait_for_timeout(200)
                check("没有遮挡的短译文可保留覆盖视图", page.locator(".reader").get_attribute("data-mode") == "overlay")
                shot("04-mode-overlay-safe")
                with db.session() as session:
                    for identity, text in original_texts.items():
                        session.get(Translation, identity).text = text
                page.locator("#view-mode").select_option("side_by_side")
                page.reload()
                expect(page.locator(".card .tgt").first).to_contain_text("界面验收样例")
                page.locator("#view-mode").select_option("overlay_side_by_side")
                expect(page.locator("#target-body .overlay-panel").first).to_be_visible()
                check("新对照视图左侧保持原文右侧覆盖译文", page.locator("#source-body .overlay-panel").count() == 0 and
                      page.locator("#target-body .overlay-panel").count() > 0)
                page.reload()
                expect(page.locator("#target-body .overlay-panel").first).to_be_visible()
                check("新对照视图保存后可重新打开", page.locator("#view-mode").input_value() == "overlay_side_by_side")
                page.locator("#overlay-original-toggle").click()
                check("独立排版译文可切换为完整原文", page.locator("#target-body .page-shell img").is_visible())
                page.locator("#overlay-original-toggle").click()
                check("独立排版可恢复译文", page.locator("#target-body .flow-translation").first.is_visible())
                shot("09-overlay-comparison")
                sent_questions = []
                reference_page = context.request.get(f"{url}/api/v1/documents/{document_id}/pages/1").json()
                reference_block = reference_page["blocks"][0]
                source_fixture = {"id":"S1", "page_index":1, "page_number":2,
                    "block_id":reference_block["id"], "text":reference_block["text"]}
                def answer_fixture(route):
                    sent_questions.append(route.request.post_data_json)
                    value={"answer":"【AI 界面验收样例】这是模拟服务回答 [S1]。", "model":"ui-fixture",
                        "scope":"document", "sources":[source_fixture], "citations":[source_fixture], "warnings":[]}
                    frames=[('meta',value),('delta',{'text':value['answer']}),('done',value)]
                    route.fulfill(content_type='text/event-stream',body=''.join(f'event: {kind}\ndata: {json.dumps(data,ensure_ascii=False)}\n\n' for kind,data in frames))
                page.route("**/assistant/stream", answer_fixture)
                report["ai_ui_fixture"] = "AI 界面响应为模拟；真实文献检索和服务协议另由集成测试验证"
                page.locator("#assistant-toggle").click()
                expect(page.locator("#assistant-panel")).to_be_visible()
                page.get_by_role("button", name="解释本文的方法", exact=True).click()
                check("AI 快捷问题仅填入输入框，不自动发送", page.locator("#assistant-question").input_value() == "解释本文的方法" and not sent_questions)
                page.locator("#assistant-question").fill("本文的方法是什么？")
                page.locator("#assistant-send").click()
                expect(page.locator(".assistant-answer-text").first).to_contain_text("界面验收样例")
                check("AI 问答界面显示回答与来源", page.locator(".assistant-source").count() == 1)
                page.locator("#source-body .source-text-line").first.evaluate("""el => {
                    const range=document.createRange(); range.selectNodeContents(el);
                    const selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
                    document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                }""")
                expect(page.locator(".assistant-context").filter(has_text="已划词")).to_be_visible()
                page.get_by_role("button", name="解释术语", exact=True).click()
                expect(page.locator(".assistant-answer-text")).to_have_count(2)
                check("原文划词可发送术语解释及选中上下文", sent_questions[-1]["kind"] == "term" and
                      bool(sent_questions[-1]["selected_text"]) and bool(sent_questions[-1]["block_id"]))
                page.get_by_role("button", name="清除选区", exact=True).click()
                page.evaluate("window.getSelection().removeAllRanges()")
                formula_id = next(block.id for block in blocks if block.type == "formula" and block.page_index == 0)
                page.locator(f'#source-body .source-text-line[data-block-id="{formula_id}"]').first.click()
                page.get_by_role("button", name="解释公式", exact=True).click()
                expect(page.locator(".assistant-answer-text")).to_have_count(3)
                check("公式解释请求携带实际公式上下文", sent_questions[-1]["kind"] == "formula" and
                    sent_questions[-1]["block_id"] == formula_id and bool(sent_questions[-1]["selected_text"]))
                shot("10-ai-assistant")
                page.locator(".assistant-source").first.click()
                expect(page.locator("#page-label")).to_contain_text("第 2 / 2 页")
                check("AI 来源可跳转到对应页段落", True)
                page.locator("#assistant-panel").get_by_role("button", name="关闭", exact=True).click()
                page.evaluate("window.getSelection().removeAllRanges()")
                page.unroute("**/assistant/stream", answer_fixture)
                page.locator("#previous-page").click()
                expect(page.locator("#page-label")).to_contain_text("第 1 / 2 页")
                page.locator("#view-mode").select_option("side_by_side")
                page.locator("#next-page").click()
                expect(page.locator("#page-label")).to_contain_text("第 2 / 2 页")
                check("下一页与页尾禁用状态", page.locator("#next-page").is_disabled())
                page.locator("#previous-page").click()
                expect(page.locator("#page-label")).to_contain_text("第 1 / 2 页")
                check("第一页禁用上一页", page.locator("#previous-page").is_disabled())
                page.locator("#target-language").select_option("ja")
                expect(page.locator("#export-link")).to_have_attribute("href", f"/api/v1/documents/{document_id}/export.md?target_language=ja")
                check("目标语言切换同步更新导出", True)
                page.locator("#target-language").select_option("zh")
                expect(page.locator(".card .tgt").first).to_contain_text("界面验收样例")
                with page.expect_download() as exported:
                    page.locator("#export-link").click()
                exported.value.save_as(str(output / "bilingual.md"))
                check("实际下载双语 Markdown", "界面验收样例" in (output / "bilingual.md").read_text(encoding="utf-8"))

                card = page.locator(".card").filter(has=page.locator(".tgt").filter(has_text="界面验收样例")).first
                block_id = card.get_attribute("data-block-id")
                card.click()
                check("点击译文同步选中原文位置", page.locator(f'.block-box.is-selected[data-block-id="{block_id}"]').count() > 0)
                source = page.locator("#source-body")
                source.evaluate("el => el.scrollTop = el.scrollHeight")
                previous_scroll = source.evaluate("el => el.scrollTop")
                card.click()
                page.wait_for_timeout(700)
                check("左右对照点击译文时原文位置不变", abs(source.evaluate("el => el.scrollTop") - previous_scroll) < 2)
                check("左右对照明确显示原文固定", page.locator("#link-scrolling").is_disabled() and
                      page.locator("#scroll-behavior-label").inner_text() == "原文固定")
                left_before = source.bounding_box()
                target.evaluate("el => el.scrollTop = el.scrollHeight")
                page.wait_for_timeout(700)
                check("左右对照滚动译文时原文滚动位置不变", abs(source.evaluate("el => el.scrollTop") - previous_scroll) < 2)
                check("左右对照原文面板悬浮位置不变", source.bounding_box() == left_before)
                page.locator("#view-mode").select_option("stacked")
                source.evaluate("el => el.scrollTop = el.scrollHeight")
                stacked_scroll = source.evaluate("el => el.scrollTop")
                card.click()
                page.wait_for_timeout(700)
                check("上下对照仍可点击译文定位原文", source.evaluate("el => el.scrollTop") < stacked_scroll)
                page.locator("#link-scrolling").check()
                page.wait_for_timeout(700)
                end_scroll = target.evaluate("el => {el.scrollTop = el.scrollHeight; return el.scrollTop;}")
                page.wait_for_timeout(700)
                final_scroll = target.evaluate("el => el.scrollTop")
                check("联动滚动到页尾不会回弹", abs(end_scroll - final_scroll) < 2,
                      {"requested": end_scroll, "actual": final_scroll})
                page.locator("#link-scrolling").uncheck()
                page.locator("#view-mode").select_option("side_by_side")
                page.once("dialog", lambda dialog: dialog.accept("【验收人工修正】修正后的标题译文。"))
                card.get_by_role("button", name="修正", exact=True).click()
                expect(page.locator(".card .tgt").first).to_contain_text("验收人工修正")
                check("实际提交译文修正并刷新显示", True)
                page.locator("#settings-button").click()
                expect(page.locator("#setting-base_url")).to_be_visible()
                shot("05-settings-desktop")
                check("翻译设置可打开且密钥留空", page.locator("#setting-api_key").input_value() == "")
                page.locator("#settings-close").click()

                with db.session() as session:
                    row = session.scalar(select(Translation).where(Translation.block_id == long_block))
                    row.text = "【超长译文验收】长段落应完整可读，滚动后仍可找到原文对应位置。" * 180
                page.reload()
                expect(page.locator(f'.card[data-block-id="{long_block}"] .tgt')).to_contain_text("超长译文验收")
                if page.locator("#link-scrolling").is_enabled():
                    page.locator("#link-scrolling").uncheck()
                long_card = page.locator(f'.card[data-block-id="{long_block}"]')
                long_card.locator(".tgt").evaluate("el => el.scrollIntoView({block:'end'})")
                check("超长译文末尾仍在可视区域", long_card.locator(".tgt").evaluate("el => el.getBoundingClientRect().bottom <= innerHeight"))
                shot("06-long-translation")
                page.locator("#view-mode").select_option("overlay_side_by_side")
                flow_long = page.locator(f'.flow-block[data-block-id="{long_block}"]')
                layout = flow_long.evaluate("""el => ({height:el.clientHeight, scroll:el.scrollHeight,
                    overflow:getComputedStyle(el).overflow, position:getComputedStyle(el).position,
                    sheet:el.closest('.layout-page').scrollHeight, text:el.querySelector('.flow-translation').textContent.length})""")
                check("新对照超长段落自然展开且没有内滚条", layout["overflow"] == "visible" and
                    layout["position"] == "absolute" and layout["scroll"] <= layout["height"] + 2 and layout["text"] > 4000, layout)
                check("译文页面可以长于左侧原页", layout["sheet"] > page.locator("#source-body .page-shell").evaluate("el => el.clientHeight"))
                flow_long.locator(".flow-translation").evaluate("el => el.scrollIntoView({block:'end'})")
                check("自然展开译文末尾可通过整侧滚动读到", flow_long.locator(".flow-translation").evaluate(
                    "el => el.getBoundingClientRect().bottom <= document.querySelector('#target-body').getBoundingClientRect().bottom + 2"))
                shot("12-flow-long-comparison")
                page.locator("#view-mode").select_option("overlay")
                page.wait_for_timeout(250)
                check("超长译文不会自动退出覆盖", page.locator(".reader").get_attribute("data-mode") == "overlay")
                long_panel = page.locator(f'.overlay-panel[data-block-id="{long_block}"]')
                bounds = long_panel.evaluate("""el => ({height:el.clientHeight, scroll:el.scrollHeight,
                    sourceHeight:Number(el.dataset.originalHeight) * el.parentElement.clientHeight,
                    background:getComputedStyle(el).backgroundColor})""")
                check("超长译文限制在原文高度内且背景不透明", abs(bounds["height"] - bounds["sourceHeight"]) < 2 and
                    bounds["scroll"] > bounds["height"] and bounds["background"] == "rgb(255, 255, 255)", bounds)
                long_panel.click()
                page.locator("#overlay-full-block").click()
                expect(page.locator("#overlay-block-dialog")).to_be_visible()
                check("覆盖模式完整段落可读", len(page.locator("#overlay-block-content .full-block-text").last.inner_text()) > 4000)
                check("完整段落关闭按钮始终可达", page.locator("#overlay-block-close").evaluate(
                    "el => el.getBoundingClientRect().top >= 0 && el.getBoundingClientRect().bottom <= innerHeight"))
                shot("06-overlay-full-paragraph")
                page.locator("#overlay-block-close").click()
                page.reload()
                expect(page.locator(".overlay-panel").first).to_be_visible()
                page.wait_for_timeout(250)
                check("重新打开仍保留覆盖模式", page.locator("#view-mode").input_value() == "overlay")
                shot("06-long-overlay")
                page.locator("#next-page").click()
                expect(page.locator("#page-label")).to_contain_text("第 2 / 2 页")
                check("覆盖视图翻页仍保留覆盖", page.locator("#view-mode").input_value() == "overlay")
                page.locator("#previous-page").click()
                expect(page.locator("#page-label")).to_contain_text("第 1 / 2 页")
                page.locator("#view-mode").select_option("side_by_side")

                for width, height in [(1024, 768), (768, 1024), (390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate("window.scrollTo(0,0)")
                    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
                    check(f"{width}px 视口没有横向溢出", overflow <= 2, overflow)
                    check(f"{width}px 视口视图控件可达", visible_controls())
                    if width <= 900:
                        page.locator("#sidebar-toggle").click()
                        expect(page.locator("#reader-sidebar")).to_be_visible()
                    check(f"{width}px 视口可返回文献库并导出", page.get_by_role("link", name="← 文献库").is_visible() and page.locator("#export-link").is_visible())
                    if width <= 900:
                        shot(f"07-responsive-menu-{width}")
                        with page.expect_download() as menu_download:
                            page.locator("#export-link").click()
                        check(f"{width}px 视口实际使用侧栏导出", menu_download.value.suggested_filename.endswith(".md"))
                        page.locator("#sidebar-toggle").click()
                    shot(f"07-responsive-{width}")
                page.locator("#settings-button").click()
                expect(page.locator("#settings-dialog")).to_be_visible()
                shot("08-settings-mobile")
                check("窄屏设置面板关闭按钮可达", page.locator("#settings-close").evaluate("el => el.getBoundingClientRect().bottom <= innerHeight"))
                page.locator("#settings-close").click()
                page.locator("#assistant-toggle").click()
                expect(page.locator("#assistant-panel")).to_be_visible()
                check("窄屏 AI 助手没有横向溢出", page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"))
                page.locator("#assistant-question").fill("手机宽度下的测试问题")
                check("窄屏 AI 输入可操作", page.locator("#assistant-question").input_value() == "手机宽度下的测试问题")
                check("窄屏 AI 发送按钮处于视口内", page.locator("#assistant-send").evaluate(
                    "el => el.getBoundingClientRect().bottom <= innerHeight && el.getBoundingClientRect().top >= 0"))
                shot("11-ai-mobile")
                original_badge = page.locator("#translation-badge").inner_text()
                page.locator("#translation-badge").evaluate("el => el.textContent = '翻译：已配置（' + 'long-model-name-'.repeat(16) + '）'")
                check("长模型名称不破坏窄屏布局", page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"))
                page.locator("#translation-badge").evaluate("(el,text) => el.textContent = text",original_badge)
                page.locator("#brand").click()
                expect(page.locator(".doc-card").first).to_be_visible()
                check("文献库有文件类型标识和中文状态", page.locator(".doc-card").first.get_attribute("data-format") == "PDF" and
                    "解析：完成" in page.locator(".doc-card .doc-meta").first.inner_text())
                shot("13-library-populated")
                # 图形保留验收：自编双栏 PDF，同时包含栅格图片与矢量图。
                import io
                from reportlab.pdfgen import canvas
                from reportlab.lib.utils import ImageReader
                from PIL import Image, ImageDraw
                graph_pdf = data / "graphics-fixture.pdf"
                raster = Image.new("RGB",(200,120),"white")
                draw = ImageDraw.Draw(raster)
                draw.rectangle((15,15,185,105),fill=(230,60,90))
                stream=io.BytesIO(); raster.save(stream,format="PNG"); stream.seek(0)
                pdf=canvas.Canvas(str(graph_pdf),pagesize=(600,800))
                pdf.setFont("Helvetica-Bold",16); pdf.drawString(50,770,"Graphics-preserving research reader")
                pdf.setFont("Helvetica",10)
                for line in range(11):
                    pdf.drawString(50,730-line*14,"Left column evidence and analysis.")
                    pdf.drawString(330,730-line*14,"Right column evidence and analysis.")
                pdf.setFillColorRGB(.82,.92,1); pdf.rect(330,380,210,150,fill=1,stroke=0)
                pdf.setStrokeColorRGB(0,.2,.8); pdf.line(340,390,530,390); pdf.line(340,390,340,515)
                pdf.setFillColorRGB(0,0,0); pdf.drawString(350,450,"Vector chart labels")
                pdf.drawString(330,350,"Figure 1. Vector chart")
                pdf.drawImage(ImageReader(stream),60,180,width=200,height=120)
                pdf.drawString(50,150,"Figure 2. Raster image")
                pdf.save()
                with page.expect_response(lambda response: response.url.endswith("/api/v1/documents") and response.request.method=="POST") as graph_upload:
                    page.locator("#file-input").set_input_files(str(graph_pdf))
                graph_id=graph_upload.value.json()["document"]["id"]
                expect(page.locator("#source-body")).to_contain_text("正在等待解析结果")
                assert worker.serve(poll_seconds=0,max_jobs=1)==1
                expect(page.locator(".block-box").first).to_be_visible()
                with db.session() as session:
                    graph_blocks=list(session.scalars(select(Block).where(Block.document_id==graph_id).order_by(Block.order_index)))
                    for index,block in enumerate(graph_blocks):
                        if BlockType(block.type).is_translatable:
                            session.add(Translation(id=f"graph_fixture_{index}",document_id=graph_id,
                                block_id=block.id,revision_id=block.revision_id,source_hash=block.text_hash,
                                provider="ui-fixture",model="graphics-layout-fixture",cache_key="graph_"+block.id,
                                text=("保留双栏和图形的长译文，用于验证自然扩展页面而不删除图片。"*120 if block.type=="paragraph" else "图形版式验收"),status="ok"))
                page.set_viewport_size({"width":1440,"height":900})
                page.reload(); expect(page.locator(".block-box").first).to_be_visible()
                page.locator("#view-mode").select_option("overlay_side_by_side")
                expect(page.locator(".layout-band").first).to_be_visible()
                graph_payload=context.request.get(f"{url}/api/v1/documents/{graph_id}/pages/0").json()
                regions=graph_payload["page"]["visual_regions"]
                check("真实 PDF 同时检出栅格图片与矢量图形",len(regions)>=2,regions)
                cuts=page.locator(".layout-band").evaluate_all("nodes => nodes.map(el => [Number(el.dataset.sourceStart),Number(el.dataset.sourceEnd)])")
                check("原页分带覆盖从顶部到底部的全部画面",cuts[0][0]==0 and cuts[-1][1]==1 and
                    all(abs(cuts[i][1]-cuts[i+1][0])<.00001 for i in range(len(cuts)-1)))
                check("图片与矢量图不会被分页切开",all(any(start<=box[1]+.0001 and end>=box[3]-.0001 for start,end in cuts) for box in regions))
                color_counts={"vector_blue":0,"raster_red":0}
                for index,(start,end) in enumerate(cuts):
                    if not any(start<=box[1]+.0001 and end>=box[3]-.0001 for box in regions):
                        continue
                    target_band=page.locator(".layout-band-original").nth(index)
                    target_band.scroll_into_view_if_needed()
                    captured=target_band.screenshot()
                    pixels=Image.open(io.BytesIO(captured)).convert("RGB")
                    color_counts["vector_blue"]+=sum(190<r<225 and 225<g<245 and b>248 for r,g,b in pixels.getdata())
                    color_counts["raster_red"]+=sum(r>180 and g<110 and b<150 for r,g,b in pixels.getdata())
                check("右侧实际画面仍包含矢量图蓝色区域",color_counts["vector_blue"]>1000,color_counts)
                check("右侧实际画面仍包含栅格图片红色区域",color_counts["raster_red"]>1000,color_counts)
                check("译文保持原来的左右栏位",page.locator(".layout-block").evaluate_all(
                    "nodes => nodes.some(el=>parseFloat(el.style.left)<40) && nodes.some(el=>parseFloat(el.style.left)>50)"))
                shot("14-graphics-preserved")
                browser.close()
        except Exception as exc:
            check("验收脚本执行", False, f"{type(exc).__name__}: {exc}")
        finally:
            if db:
                db.dispose()
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            log.close()
            check("浏览器无未捕获脚本错误", not errors, errors)
            report["passed"] = sum(row["status"] == "PASS" for row in results)
            report["failed"] = sum(row["status"] == "FAIL" for row in results)
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT {output}: {report['passed']} PASS, {report['failed']} FAIL", flush=True)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
