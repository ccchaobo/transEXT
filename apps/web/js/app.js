/* The reader.

   Two things in here are the product rather than the plumbing, so they are called out:

   1. ANCHORING.  A block exists once in the parsed document and appears twice on screen: as a
      box over the page image, and as a card in the translation pane.  Both carry the block id,
      and a single map from id to elements keeps them in step.  Hovering either side highlights
      the other; clicking either side selects both and scrolls the other into view.  This works
      because the block id is stable within a parse revision, and because the span arrives in
      display-normalised coordinates, so the box can be positioned with percentages without the
      browser knowing anything about rotation, cropping or DPI.

   2. HONEST STATE.  A block with no translation, a translation that failed, and a translation
      whose outcome is ambiguous are three different things, and the UI shows three different
      things.  Nothing is filled with placeholder text, and nothing is silently omitted: a
      missing translation is a visible marked card, because a blank space would read as "this
      block has no content" rather than "this block has not been translated". */

import { api, ApiError } from "./api.js";
import { fillStructuredText } from './text-layout.js';
import { createSettingsPanel } from "./settings.js";
import { createAssistantPanel } from "./assistant.js";
import { createLibrary } from "./library.js";

const state = {
  route: { name: "library" },
  translation: null,
  targetLanguage: localStorage.getItem("rr.targetLanguage") || "zh",
  document: null,
  outline: [],
  page: null,
  pageIndex: 0,
  selectedBlockId: null,
  jobs: [],
  jobsTimer: null,
  annotations: [],
  // Whether the two panes follow each other.  This is `link_scrolling` from the reading state,
  // and it is load-bearing here rather than decorative: the field used to be written on every
  // page turn and read by nothing at all, so the panes never followed each other and the reader
  // had to hunt for the matching paragraph by eye on every scroll.
  linkScrolling: true,
  // Scroll positions this app moved itself, so its own write is not mistaken for the reader
  // scrolling.  Without it the two panes trade scroll positions and neither settles.
  linkedTo: { "source-body": null, "target-body": null },
  // Set while a smooth `scrollIntoView` is in flight, whose events cannot be attributed.
  linkSuspendedUntil: 0,
  overlayPanels: [],
  overflowObserver: null,
  routeVersion: 0,
  pageVersion: 0,
  requestedPageIndex: 0,
  pollingVersion: 0,
  overlayFontScale: Number(localStorage.getItem("rr.overlayFontScale")) || 100,
  overlayOriginal: false,
  assistant: null,
  library: null,
};

const els = {
  view: document.getElementById("view"),
  banner: document.getElementById("banner"),
  badge: document.getElementById("translation-badge"),
  targetLanguage: document.getElementById("target-language"),
  fileInput: document.getElementById("file-input"),
  footer: document.getElementById("footer-note"),
  brand: document.getElementById("brand"),
};

/* ------------------------------------------------------------------ helpers */

/** Escape for interpolation into innerHTML.  Document text is untrusted input. */
function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showBanner(message, kind = "info") {
  els.banner.textContent = message;
  els.banner.className = `banner ${kind}`;
  els.banner.hidden = false;
}

function clearBanner() {
  els.banner.hidden = true;
  els.banner.textContent = "";
}

function reportError(error) {
  if (error instanceof ApiError) {
    showBanner(`${error.message}${error.code ? `（${error.code}）` : ""}`, "error");
  } else {
    showBanner(`未预期的错误：${error && error.message ? error.message : error}`, "error");
  }
  console.error(error);
}

const BLOCK_LABELS = {
  title: "标题", author: "作者", affiliation: "单位", abstract: "摘要", heading: "章节标题",
  paragraph: "正文", list_item: "列表项", caption: "图注", table: "表格",
  table_cell: "表格单元", formula: "公式", figure: "图", reference: "参考文献",
  footnote: "脚注", header: "页眉", footer: "页脚", page_number: "页码", unknown: "未识别",
};

const VERBATIM_TYPES = new Set(["formula", "table", "table_cell", "figure"]);
const OVERLAY_WARNING = "部分译文较长，已限制在原段落区域内。可在段落内滚动，或选中段落后点“查看本段”阅读完整内容。";

function blockLabel(block) {
  return BLOCK_LABELS[block.type] || block.type;
}

/* ------------------------------------------------------------------ routing */

function parseHash() {
  const hash = location.hash.replace(/^#\/?/, "");
  const parts = hash.split("/").filter(Boolean);
  if (parts[0] === "doc" && parts[1]) return { name: "reader", documentId: parts[1] };
  return { name: "library" };
}

async function route() {
  state.library?.dispose();
  state.library=null;
  if (state.assistant) state.assistant.dispose();
  state.assistant = null;
  const version = ++state.routeVersion;
  ++state.pageVersion;
  state.route = parseHash();
  document.body.classList.toggle("is-reading", state.route.name === "reader");
  document.body.classList.toggle("is-library", state.route.name === "library");
  clearBanner();
  stopJobPolling();
  if (state.overflowObserver) state.overflowObserver.disconnect();
  state.document = null;
  state.page = null;
  state.jobs = [];
  state.selectedBlockId = null;
  els.footer.textContent = "";
  if (state.route.name === "reader") {
    await openReader(state.route.documentId, version);
  } else {
    state.document = null;
    await renderLibrary();
  }
}

/* ------------------------------------------------------------------ library */

async function renderLibrary() {
  const version=state.routeVersion;
  state.library=createLibrary({container:els.view,api,onError:reportError,
    onImport:()=>els.fileInput.click(),isCurrent:()=>version===state.routeVersion});
  await state.library.load();
}

/* ------------------------------------------------------------------ reader */

async function openReader(documentId, version = state.routeVersion) {
  els.view.innerHTML = "";
  clearBanner();
  try {
    const document = await api.getDocument(documentId);
    if (version !== state.routeVersion) return;
    state.document = document;
  } catch (error) {
    if (version !== state.routeVersion) return;
    reportError(error);
    els.view.appendChild(el("p", "empty", "无法打开该文献。"));
    return;
  }
  const doc = state.document;

  const saved = await api.getReadingState(documentId).catch(() => ({ reading_state: null }));
  if (version !== state.routeVersion) return;
  state.pageIndex = Math.min(
    saved.reading_state ? saved.reading_state.page_index : 0,
    Math.max((doc.page_count || 1) - 1, 0),
  );
  state.requestedPageIndex = state.pageIndex;

  const outlinePayload = await api.outline(documentId).catch(() => ({ outline: [] }));
  if (version !== state.routeVersion) return;
  state.outline = outlinePayload.outline || [];
  state.outlineNote = outlinePayload.note || null;

  state.annotations = (await api.listAnnotations(documentId).catch(() => ({ annotations: [] }))).annotations || [];
  if (version !== state.routeVersion) return;

  const reader = el("div", "reader");
  reader.dataset.mode = (saved.reading_state && saved.reading_state.view_mode) || "side_by_side";
  reader.dataset.overlayOriginal = String(state.overlayOriginal);
  state.linkScrolling = !saved.reading_state || saved.reading_state.link_scrolling !== false;
  reader.appendChild(buildSidebar(doc, outlinePayload));

  // The toolbar sits outside both panes, and it has to.  It used to live in the translation pane's
  // head, which meant that choosing "译文覆盖" or "仅原文" - modes that hide that pane - hid the
  // control that gets you back out, and the reader was stuck in a mode with no exit.  "仅译文" had
  // the same defect with the page buttons: they were in the source pane.  Controls that act on one
  // pane belong to neither pane.
  const main = el("div", "reader-main");
  main.appendChild(buildToolbar(doc, reader.dataset.mode));
  main.appendChild(buildPanes(doc, reader.dataset.mode));
  reader.appendChild(main);
  state.assistant = createAssistantPanel({ documentId: doc.id, getPage: () => state.page,
    onSource: async source => {
      const loaded = await loadPage(source.page_index);
      if (loaded) selectBlock(source.block_id, { scroll: true, scrollOther: true });
    } });
  reader.appendChild(state.assistant.element);
  els.view.appendChild(reader);

  if (doc.active_revision_id) {
    await loadPage(state.pageIndex);
  } else {
    document.getElementById("source-body").appendChild(el("p", "empty",
      doc.parse_status === "failed" ? "解析失败，请查看左侧任务原因并重试。" :
        doc.parse_status === "cancelled" ? "解析任务已取消，当前没有可阅读的解析结果。" :
          "正在等待解析结果，完成后会自动显示。"));
    document.getElementById("translate-button").disabled = true;
  }
  if (version !== state.routeVersion) return;
  await refreshJobs();
  if (version !== state.routeVersion) return;
  if (state.jobs.some(isActiveJob) || ["pending", "running"].includes(doc.parse_status) ||
      (!doc.active_revision_id && state.jobs.some(job => job.status === "succeeded"))) {
    startJobPolling();
  }
}

function buildSidebar(doc, outlinePayload) {
  const sidebar = el("aside", "sidebar");
  sidebar.id = "reader-sidebar";

  const back = el("a", "button", "← 文献库");
  back.href = "#/";
  back.style.marginBottom = "12px";
  sidebar.appendChild(back);

  sidebar.appendChild(el("h3", null, "目录"));
  if (!state.outline.length) {
    sidebar.appendChild(el("p", "job-detail", "未识别到章节标题。"));
  } else {
    for (const entry of state.outline) {
      const button = el("button", `outline-item outline-l${Math.min(entry.level || 1, 3)}`);
      button.textContent = entry.title || "(无标题)";
      button.title = `第 ${entry.page_number} 页`;
      button.addEventListener("click", () => {
        if (entry.page_index !== state.pageIndex) {
          loadPage(entry.page_index).then(loaded => {
            if (loaded && entry.block_id) selectBlock(entry.block_id, { scroll: true });
          });
        } else {
          if(entry.block_id)selectBlock(entry.block_id, { scroll: true });
        }
      });
      if(entry.block_id)button.dataset.blockId = entry.block_id;
      sidebar.appendChild(button);
    }
  }
  if (outlinePayload.note) {
    const note = el("p", "job-detail", outlinePayload.note);
    note.style.marginTop = "8px";
    sidebar.appendChild(note);
  }

  sidebar.appendChild(el("h3", null, "详情"));
  const facts = [
    `文件：${doc.filename}`,
    `解析：${statusText(doc.parse_status)}`,
    `翻译：${statusText(doc.translate_status)}`,
    `修订：${doc.active_revision_id || "无"}`,
    `SHA-256：${String(doc.sha256).slice(0, 16)}…`,
  ];
  for (const fact of facts.slice(0, 3)) sidebar.appendChild(el("p", "job-detail", fact));
  const metadata = el("details", "metadata-details");
  metadata.appendChild(el("summary", null, "文件指纹与解析版本"));
  for (const fact of facts.slice(3)) metadata.appendChild(el("p", "job-detail", fact));
  sidebar.appendChild(metadata);

  const actions = el("div");
  actions.style.display = "grid";
  actions.style.gap = "6px";

  const original = el("a", "button", "打开原件");
  original.href = api.originalUrl(doc.id);
  original.target = "_blank";
  original.rel = "noopener";
  actions.appendChild(original);

  const exportLink = el("a", "button", "导出双语 Markdown");
  exportLink.id = "export-link";
  exportLink.href = api.exportUrl(doc.id, state.targetLanguage);
  actions.appendChild(exportLink);

  const remove = el("button", "button danger", "移入回收站");
  remove.addEventListener("click", async () => {
    if (!confirm(`将「${doc.title || doc.filename}」移入回收站？之后可以恢复，原件、译文与标注会保留。`)) return;
    try {
      await api.libraryBatch({ids:[doc.id],action:"trash"});
      location.hash = "#/";
    } catch (error) { reportError(error); }
  });
  actions.appendChild(remove);

  const jobsBox = el("div", "jobs");
  jobsBox.id = "jobs-box";
  actions.appendChild(jobsBox);

  sidebar.appendChild(actions);
  return sidebar;
}

function buildPanes(doc, viewMode) {
  const panes = el("div", "panes");

  /* -- source -- */
  const source = el("section", "pane pane-source");
  const sourceHead = el("div", "pane-head");
  sourceHead.appendChild(el("strong", null, "原文"));
  source.appendChild(sourceHead);

  const sourceBody = el("div", "pane-body");
  sourceBody.id = "source-body";
  source.appendChild(sourceBody);

  /* -- translation -- */
  const target = el("section", "pane pane-target");
  const targetHead = el("div", "pane-head");
  targetHead.appendChild(el("strong", null, "译文"));
  target.appendChild(targetHead);

  const targetBody = el("div", "pane-body");
  targetBody.id = "target-body";
  target.appendChild(targetBody);

  panes.appendChild(source);
  panes.appendChild(target);
  linkPanes(sourceBody, targetBody);
  return panes;
}

/** The controls that belong to the reader rather than to either pane.
 *
 *  Everything here stays reachable in every view mode, which is the property the previous
 *  arrangement lacked.
 */
function buildToolbar(doc, viewMode) {
  const bar = el("div", "reader-bar");

  const prev = el("button", "button", "上一页");
  prev.id = "previous-page";
  prev.disabled = !doc.active_revision_id || state.pageIndex === 0;
  prev.addEventListener("click", () => gotoPage(state.requestedPageIndex - 1));
  const next = el("button", "button", "下一页");
  next.id = "next-page";
  next.disabled = !doc.active_revision_id || state.pageIndex >= (doc.page_count || 1) - 1;
  next.addEventListener("click", () => gotoPage(state.requestedPageIndex + 1));
  const pageLabel = el("span", "bar-label", "…");
  pageLabel.id = "page-label";
  const nav = el("div", "bar-group");
  nav.appendChild(prev);
  nav.appendChild(pageLabel);
  nav.appendChild(next);
  bar.appendChild(nav);

  const sidebarToggle = el("button", "button sidebar-toggle", "目录与详情");
  sidebarToggle.id = "sidebar-toggle";
  sidebarToggle.type = "button";
  sidebarToggle.setAttribute("aria-controls", "reader-sidebar");
  sidebarToggle.setAttribute("aria-expanded", "false");
  sidebarToggle.addEventListener("click", () => {
    const reader = document.querySelector(".reader");
    const open = reader.dataset.sidebarOpen !== "true";
    reader.dataset.sidebarOpen = String(open);
    sidebarToggle.setAttribute("aria-expanded", String(open));
    sidebarToggle.textContent = open ? "收起目录与详情" : "目录与详情";
  });
  bar.appendChild(sidebarToggle);

  const mode = el("select");
  mode.id = "view-mode";
  for (const [value, label] of [
    ["side_by_side", "左右对照"], ["stacked", "上下对照"], ["overlay", "译文覆盖"],
    ["source_only", "仅原文"], ["target_only", "仅译文"],
    ["overlay_side_by_side", "原文＋覆盖译文"],
  ]) {
    const option = el("option", null, label);
    option.value = value;
    mode.appendChild(option);
  }
  mode.value = viewMode;
  mode.addEventListener("change", async () => {
    if (els.banner.textContent === OVERLAY_WARNING) clearBanner();
    const reader = document.querySelector(".reader");
    reader.dataset.mode = mode.value;
    updateScrollControl(mode.value);
    // The overlay is drawn into the source pane, so it has to be built or torn down here rather
    // than waiting for the next page load: switching into it otherwise shows an empty mode.
    if (state.page) { renderSource(state.page); renderTarget(state.page); }
    await api.putReadingState(doc.id, {
      page_index: state.pageIndex, view_mode: mode.value, zoom: 1,
      link_scrolling: state.linkScrolling,
    }).catch(() => {});
  });
  bar.appendChild(labelled("视图", mode));

  // The switch for `link_scrolling`.  It was stored and round-tripped through the API while
  // nothing read it; the preference now applies to stacked comparison. Side-by-side reading
  // always keeps the original in place while the reader scrolls or selects a translation.
  const linkLabel = el("label", "link-toggle");
  linkLabel.title = "左右对照固定原文，上下对照可按段落联动。";
  const linkBox = document.createElement("input");
  linkBox.type = "checkbox";
  linkBox.id = "link-scrolling";
  linkBox.checked = state.linkScrolling;
  linkBox.addEventListener("change", async () => {
    state.linkScrolling = linkBox.checked;
    await api.putReadingState(doc.id, {
      page_index: state.pageIndex,
      view_mode: document.querySelector(".reader").dataset.mode,
      zoom: 1,
      link_scrolling: linkBox.checked,
    }).catch(() => {});
  });
  linkLabel.appendChild(linkBox);
  const scrollLabel = el("span", null, ["side_by_side", "overlay_side_by_side"].includes(viewMode) ? "原文固定" : "联动");
  scrollLabel.id = "scroll-behavior-label";
  linkLabel.appendChild(scrollLabel);
  linkBox.disabled = ["side_by_side", "overlay_side_by_side"].includes(viewMode);
  if (linkBox.disabled) linkBox.checked = false;
  linkBox.title = "左右对照时原文保持固定；上下对照可使用联动。";
  bar.appendChild(linkLabel);

  const overlayControls = el("div", "bar-group overlay-controls");
  const fontScale = el("select");
  fontScale.id = "overlay-font-scale";
  for (const percent of [80, 100, 120, 140]) {
    const option = el("option", null, `${percent}%`);
    option.value = String(percent);
    fontScale.appendChild(option);
  }
  fontScale.value = String(state.overlayFontScale);
  fontScale.addEventListener("change", () => {
    state.overlayFontScale = Number(fontScale.value);
    localStorage.setItem("rr.overlayFontScale", fontScale.value);
    if (state.page) { renderSource(state.page); renderTarget(state.page); }
  });
  overlayControls.appendChild(labelled("译文字号", fontScale));
  const originalToggle = el("button", "button", state.overlayOriginal ? "显示译文" : "显示原文");
  originalToggle.id = "overlay-original-toggle";
  originalToggle.type = "button";
  originalToggle.setAttribute("aria-pressed", String(state.overlayOriginal));
  originalToggle.addEventListener("click", () => {
    state.overlayOriginal = !state.overlayOriginal;
    document.querySelector(".reader").dataset.overlayOriginal = String(state.overlayOriginal);
    originalToggle.textContent = state.overlayOriginal ? "显示译文" : "显示原文";
    originalToggle.setAttribute("aria-pressed", String(state.overlayOriginal));
    if (state.page && document.querySelector(".reader").dataset.mode === "overlay_side_by_side") renderTarget(state.page);
  });
  overlayControls.appendChild(originalToggle);
  const fullBlock = el("button", "button", "查看本段");
  fullBlock.id = "overlay-full-block";
  fullBlock.type = "button";
  fullBlock.disabled = true;
  fullBlock.addEventListener("click", () => showOverlayBlock(state.selectedBlockId));
  overlayControls.appendChild(fullBlock);
  bar.appendChild(overlayControls);

  // Re-translating one block.  The API has accepted `scope: "blocks"` with `force` since the
  // beginning and nothing ever called it.  It acts on the selection, so it also works in the
  // overlay and single-pane modes, where there is no card to put a button on.
  const again = el("button", "button", "重新翻译本块");
  again.id = "retranslate-block";
  again.disabled = true;
  again.title = "先点一个段落（左侧原文的框，或右侧译文的卡片），再按这里";
  again.addEventListener("click", () => retranslateBlock(doc.id, state.selectedBlockId));
  bar.appendChild(again);

  const translateButton = el("button", "button primary", "翻译全文");
  translateButton.id = "translate-button";
  translateButton.addEventListener("click", () => startTranslation(doc.id));
  bar.appendChild(translateButton);
  const assistantButton = el("button", "button", "AI 阅读助手");
  assistantButton.id = "assistant-toggle";
  assistantButton.type = "button";
  assistantButton.addEventListener("click", () => state.assistant?.toggle());
  bar.appendChild(assistantButton);

  return bar;
}

/** A labelled control, so the toolbar reads as labels rather than as a row of unlabelled widgets. */
function labelled(text, control) {
  const wrapper = el("label", "bar-field");
  wrapper.appendChild(el("span", "bar-label", text));
  wrapper.appendChild(control);
  return wrapper;
}

function updateScrollControl(mode) {
  const box = document.getElementById("link-scrolling");
  const label = document.getElementById("scroll-behavior-label");
  if (!box || !label) return;
  box.disabled = ["side_by_side", "overlay_side_by_side"].includes(mode);
  box.checked = !box.disabled && state.linkScrolling;
  label.textContent = box.disabled ? "原文固定" : "联动";
}

function showOverlayBlock(blockId) {
  const block = state.page?.blocks.find(item => item.id === blockId);
  if (!block) return;
  const dialog = document.getElementById("overlay-block-dialog");
  const content = document.getElementById("overlay-block-content");
  content.dataset.blockId = block.id;
  content.innerHTML = "";
  content.appendChild(el("h3", null, "原文"));
  content.appendChild(fillStructuredText(el('div','full-block-text'),block.text,block.metadata?.text_structure));
  content.appendChild(el("h3", null, "译文"));
  content.appendChild(fillStructuredText(el('div','full-block-text'),block.translation?.text || '尚未翻译',block.translation?.structure));
  if (block.translation?.status === "ok") {
    const edit = el("button", "button", "修正译文");
    edit.addEventListener("click", async () => {
      const replacement = prompt("修正后的译文：", block.translation.text);
      if (replacement == null || replacement === block.translation.text) return;
      try {
        await api.correctTranslation(block.translation.id, replacement);
        dialog.close();
        await loadPage(state.pageIndex);
      } catch (error) { reportError(error); }
    });
    content.appendChild(edit);
  }
  dialog.showModal();
}

/* ------------------------------------------------------------------ page load */

async function loadPage(index) {
  const doc = state.document;
  if (!doc || !doc.active_revision_id) return false;
  const total = doc.page_count || 1;
  if (index < 0 || index >= total) return false;
  const version = ++state.pageVersion;
  const routeVersion = state.routeVersion;
  const language = state.targetLanguage;
  state.requestedPageIndex = index;

  let payload;
  try {
    payload = await api.page(doc.id, index, language);
  } catch (error) {
    if (version !== state.pageVersion || routeVersion !== state.routeVersion) return false;
    state.requestedPageIndex = state.pageIndex;
    reportError(error);
    return false;
  }
  if (version !== state.pageVersion || routeVersion !== state.routeVersion || language !== state.targetLanguage) return false;
  state.page = payload;
  if (els.banner.textContent === OVERLAY_WARNING) clearBanner();
  state.pageIndex = index;
  state.selectedBlockId = null;
  const fullBlock = document.getElementById("overlay-full-block");
  if (fullBlock) fullBlock.disabled = true;
  document.getElementById("retranslate-block").disabled = true;
  document.getElementById("previous-page").disabled = index === 0;
  document.getElementById("next-page").disabled = index === total - 1;

  renderSource(payload);
  renderTarget(payload);

  const label = document.getElementById("page-label");
  if (label) {
    const printed = payload.page.printed_label ? `（印刷页码 ${payload.page.printed_label}）` : "";
    label.textContent = `第 ${payload.page.page_number} / ${total} 页${printed}`;
  }
  els.footer.textContent =
    `内部页索引 ${payload.page.index} · 显示尺寸 ${payload.page.display_width.toFixed(0)}×${payload.page.display_height.toFixed(0)}pt` +
    ` · 旋转 ${payload.page.rotation}° · 来源 ${payload.page.source_kind}`;

  api.putReadingState(doc.id, {
    page_index: index,
    view_mode: document.querySelector(".reader").dataset.mode,
    zoom: 1,
    // It said `true` here for every page turn, which quietly switched the reader's own choice of
    // "联动" back on: turning the checkbox off lasted until the next page.  The stored value is a
    // preference, not a per-page decision.
    link_scrolling: state.linkScrolling,
  }).catch(() => {});
  return true;
}

function gotoPage(index) {
  const total = state.document.page_count || 1;
  if (index < 0 || index >= total) return;
  loadPage(index);
}

/* ------------------------------------------------------------------ source pane */

function renderSource(payload, { bodyId = "source-body", withOverlay = false, anchors = true } = {}) {
  if (state.overflowObserver) state.overflowObserver.disconnect();
  state.overlayPanels = [];
  const body = document.getElementById(bodyId);
  body.innerHTML = "";
  if (anchors) state.boxes = new Map();

  const page = payload.page;

  // Page warnings come first, above the image.  A scanned page carries "no text layer on this
  // page; OCR is required" and that is the whole explanation for why there is nothing to
  // translate: without it the reader sees an empty translation pane and no reason for it.
  for (const warning of page.warnings || []) {
    const notice = el("div", "notice", warning);
    body.appendChild(notice);
  }
  if (page.ocr_applied) {
    body.appendChild(el("div", "notice",
      "这一页的文字由 OCR 识别得到，可能存在识别错误。标注与锚点按识别后的文本定位。"));
  }

  const shell = el("div", "page-shell");
  // The shell's height comes from the page's own aspect ratio, not from the image.  That way
  // the overlays are correctly positioned even if the preview is missing, and a missing preview
  // cannot silently shift every box.
  if (page.display_width > 0 && page.display_height > 0) {
    shell.style.aspectRatio = `${page.display_width} / ${page.display_height}`;
  } else {
    shell.style.aspectRatio = "1 / 1.414";
  }

  const image = el("img");
  image.alt = `第 ${page.page_number} 页原文`;
  image.loading = "eager";
  image.addEventListener("error", () => {
    image.remove();
    const missing = el("div", "missing",
      "这一页没有可显示的画面（预览尚未生成）。下方文字仍可阅读与翻译，但无法高亮原文位置。");
    missing.style.position = "absolute";
    missing.style.inset = "0";
    shell.appendChild(missing);
  });
  image.src = page.preview_url || api.pageImageUrl(payload.document_id, page.index);
  shell.appendChild(image);

  for (const block of payload.blocks) {
    // One box per span, because a block can be split across pages or across a column break and
    // a single bounding rectangle would cover unrelated text between them.
    const spans = block.spans.filter((span) => span.page_index === page.index);
    for (const span of spans) {
      const box = el("div", "block-box");
      const [x0, y0, x1, y1] = span.bbox;
      box.style.left = `${x0 * 100}%`;
      box.style.top = `${y0 * 100}%`;
      box.style.width = `${(x1 - x0) * 100}%`;
      box.style.height = `${(y1 - y0) * 100}%`;
      box.dataset.blockId = block.id;
      box.title = `${blockLabel(block)}：${(block.text || "").slice(0, 80)}`;
      if (VERBATIM_TYPES.has(block.type)) box.classList.add("is-verbatim");
      if (block.confidence && (block.confidence.level === "low" || block.confidence.level === "unknown")) {
        box.classList.add("is-uncertain");
      }
      if (block.human_edited) box.classList.add("is-uncertain");
      box.addEventListener("mouseenter", () => highlight(block.id, true));
      box.addEventListener("mouseleave", () => highlight(block.id, false));
      box.addEventListener("click", () => selectBlock(block.id, { scroll: false, scrollOther: true }));
      shell.appendChild(box);
      if (anchors) {
        if (!state.boxes.has(block.id)) state.boxes.set(block.id, []);
        state.boxes.get(block.id).push(box);
      }
      const raw = (block.raw_text || block.text || "").slice(span.text_start, span.text_end);
      if (raw) {
        const textLine = el("span", "source-text-line", raw);
        textLine.dataset.blockId = block.id;
        for (const prop of ["left", "top", "width", "height"]) textLine.style[prop] = box.style[prop];
        textLine.addEventListener("click", () => selectBlock(block.id, { scroll: false, scrollOther: true }));
        shell.appendChild(textLine);
      }
    }
  }
  body.appendChild(shell);
  if (withOverlay || document.querySelector(".reader").dataset.mode === "overlay") renderOverlay(payload, shell);
}

/* ------------------------------------------------------------------ the overlay view */

/** Where a block's text sits on the page, in the page's own normalised coordinates.
 *
 *  当前页的连续行框决定覆盖区域；跨栏或远距离片段不组成整页白底外框。
 *  长译文在此区域内滚动，完整内容也可在段落对照窗口中查看。
 */
function blockArea(block, pageIndex) {
  const boxes = (block.spans || []).filter(span => pageIndex === undefined || span.page_index === pageIndex)
    .map((span) => span.bbox).filter(Boolean);
  if (!boxes.length) return null;
  // 跨栏或被误合并的远距离片段不能用整页外框覆盖。选最大连续文本区域，
  // 其余原文片段只沿实际行框遮盖；完整块内容始终可在段落对照中读取。
  const groups = [];
  for (const box of boxes) {
    const group = groups.find(items => items.some(other => {
      const overlap = Math.min(box[2], other[2]) - Math.max(box[0], other[0]);
      const gap = Math.max(0, box[1] - other[3], other[1] - box[3]);
      return overlap > Math.min(box[2] - box[0], other[2] - other[0]) * 0.25 &&
        gap <= Math.max(box[3] - box[1], other[3] - other[1]) * 1.5;
    }));
    if (group) group.push(box);
    else groups.push([box]);
  }
  const primary = groups.sort((a, b) => b.length - a.length)[0];
  return [
    Math.min(...primary.map((b) => b[0])),
    Math.min(...primary.map((b) => b[1])),
    Math.max(...primary.map((b) => b[2])),
    Math.max(...primary.map((b) => b[3])),
  ];
}

/** Draw the translation over the source text, in place.
 *
 *  This is a reading view, not an export and not a translated file.  Filling an English paragraph
 *  with Chinese is a layout problem this does not attempt: the original is covered where the
 *  translation is bounded by its source region, with complete paragraph viewing. It must never be
 *  described as producing a same-layout document, and the export does not come from here.
 *
 *  What it is worth: the two panes can only ever be aligned block by block, because one is a page
 *  image whose height follows its aspect ratio and the other is a list of cards whose height does
 *  not.  Every scroll still costs the reader a hunt for the matching paragraph.  Putting the
 *  translation where the original is removes the correspondence problem rather than managing it.
 */
function renderOverlay(payload, shell) {
  renderFixedOverlay(payload, shell);
}

/* ------------------------------------------------------------------ translation pane */

function renderTarget(payload) {
  if (document.querySelector(".reader").dataset.mode === "overlay_side_by_side") {
    renderFlowTarget(payload);
    return;
  }
  const body = document.getElementById("target-body");
  body.innerHTML = "";
  state.cards = new Map();
  const documentId = payload.document_id;

  for (const block of payload.blocks) {
    const card = el("article", "card");
    card.dataset.blockId = block.id;
    card.addEventListener("mouseenter", () => highlight(block.id, true));
    card.addEventListener("mouseleave", () => highlight(block.id, false));
    card.addEventListener("click", () => selectBlock(block.id, {
      scroll: !["side_by_side", "overlay_side_by_side"].includes(document.querySelector(".reader").dataset.mode), scrollOther: false,
    }));

    const tags = el("div");
    tags.style.marginBottom = "4px";
    tags.appendChild(tag(blockLabel(block), null));

    if (VERBATIM_TYPES.has(block.type) || block.translatable === false || /^\d+(?:\.\d+)*\.?$/.test((block.text || "").trim())) {
      tags.appendChild(tag("原样保留，不翻译", "verbatim"));
      card.appendChild(tags);
      const pre = el("pre", "tgt", block.text);
      pre.style.fontSize = "12.5px";
      pre.style.whiteSpace = "pre-wrap";
      card.appendChild(pre);
      body.appendChild(finish(card));
      continue;
    }

    card.appendChild(tags);
    card.appendChild(fillStructuredText(el('div','src'),block.text,block.metadata?.text_structure));

    const translation = block.translation;
    if (!translation) {
      // An empty space would read as "nothing here".  A marked card says what is actually true.
      card.classList.add("state-missing");
      card.appendChild(el("p", "tgt", ""));
      card.appendChild(el("p", "note", "尚未翻译。"));
    } else if (translation.raw_status === "unknown") {
      card.classList.add("state-ambiguous");
      card.appendChild(el("p", "tgt", translation.text || ""));
      card.appendChild(el("p", "note ambiguous",
        "结果不确定：服务可能已处理，也可能没有。这里不会自动重试，以免重复计费。"));
    } else if (translation.status !== "ok") {
      card.classList.add("state-failed");
      card.appendChild(el("p", "tgt", translation.text || ""));
      card.appendChild(el("p", "note bad", `翻译失败：${translation.error_message || translation.error_code || "未知原因"}`));
    } else {
      const text = fillStructuredText(el('div','tgt'),translation.text,translation.structure);
      card.appendChild(text);
      if(translation.structure?.preserved===false)card.appendChild(el('p','note','译文缺少原分点编号，请重新翻译本段。'));
      if (translation.stale) {
        card.appendChild(el("p", "note", "原文在该译文生成后发生过变化，译文可能已过期。"));
      }
      const edit = el("button", "button", "修正");
      edit.style.marginTop = "6px";
      edit.addEventListener("click", async (event) => {
        event.stopPropagation();
        const replacement = prompt("修正后的译文：", translation.text);
        if (replacement == null || replacement === translation.text) return;
        try {
          await api.correctTranslation(translation.id, replacement);
          await loadPage(state.pageIndex);
        } catch (error) { reportError(error); }
      });
      card.appendChild(edit);

      // A machine translation that is wrong is not a reason to re-translate a whole page.  This is
      // the same action as the toolbar button, given a home on the card it applies to.
      const again = el("button", "button", "重新翻译");
      again.style.marginTop = "6px";
      again.style.marginLeft = "6px";
      again.title = "丢掉这一段已有的译文，重新向服务请求一次";
      again.addEventListener("click", (event) => {
        event.stopPropagation();
        selectBlock(block.id, { scroll: false, scrollOther: false });
        retranslateBlock(documentId, block.id);
      });
      card.appendChild(again);
    }

    if (translation) {
      tags.appendChild(tag(translation.is_human ? "人工修正" : "机器翻译", translation.is_human ? "human" : "machine"));
      if (translation.model) tags.appendChild(tag(translation.model, null));
      if (translation.stale) tags.appendChild(tag("可能过期", "verbatim"));
    }
    if (block.human_edited) tags.appendChild(tag(`OCR 修正 r${block.ocr_revision}`, "ocr"));
    if (block.confidence && block.confidence.level === "low") {
      tags.appendChild(tag(`识别置信度 ${block.confidence.score}`, "ocr"));
    }

    body.appendChild(finish(card));
  }
}

/** 固定原页坐标；只使用不与其他文字、图表相交的空白。 */
function comparisonLayout(payload) {
  const pageIndex = payload.page.index;
  const protectedAreas = (payload.page.visual_regions || []).map(box => {
    // Include neighbouring plot titles/legends in large graphic protection.
    // Inline symbols and rules get no padding that could swallow body prose.
    const pad=(box[2]-box[0])*(box[3]-box[1])>.005 ? .025 : 0;
    return [Math.max(0,box[0]-.004),Math.max(0,box[1]-pad),Math.min(1,box[2]+.004),Math.min(1,box[3]+pad)];
  });
  const occupied = payload.blocks.flatMap(block => (block.spans || [])
    .filter(span => span.page_index === pageIndex).map(span => ({id:block.id,area:span.bbox})));
  for (const block of payload.blocks) if (VERBATIM_TYPES.has(block.type)) {
    const area = blockArea(block, pageIndex);
    if (area) protectedAreas.push(area);
  }
  const inGraphic = area => protectedAreas.some(box => {
    const w = Math.max(0,Math.min(area[2],box[2])-Math.max(area[0],box[0]));
    const h = Math.max(0,Math.min(area[3],box[3])-Math.max(area[1],box[1]));
    // Even a thin overlap can erase a plotted curve. A label straddling the
    // graphic edge stays in the original, rather than becoming a white cover.
    return w>.0005 && h>.0005;
  });
  const fallbacks = [];
  // PDF font boxes can overlap the following formula even when their baselines
  // do not. Fit prose into an unobstructed rectangle instead of dropping it.
  const safeFrame = area => {
    let candidates=[area];
    for (const box of protectedAreas) candidates=candidates.flatMap(rect => {
      const w=Math.min(rect[2],box[2])-Math.max(rect[0],box[0]);
      const h=Math.min(rect[3],box[3])-Math.max(rect[1],box[1]);
      if(w<=.0005 || h<=.0005)return [rect];
      return [
        [rect[0],rect[1],rect[2],Math.min(rect[3],box[1])],
        [rect[0],Math.max(rect[1],box[3]),rect[2],rect[3]],
        [rect[0],rect[1],Math.min(rect[2],box[0]),rect[3]],
        [Math.max(rect[0],box[2]),rect[1],rect[2],rect[3]],
      ].filter(r=>r[2]-r[0]>=Math.min(.12,(area[2]-area[0])*.75) &&
        r[2]-r[0]>=(area[2]-area[0])*.75 && r[3]-r[1]>.014);
    });
    return candidates.sort((a,b)=>(b[2]-b[0])*(b[3]-b[1])-(a[2]-a[0])*(a[3]-a[1]))[0];
  };
  const entries = payload.blocks.flatMap(block => {
    const area = blockArea(block,pageIndex);
    if (!area || VERBATIM_TYPES.has(block.type) || block.translatable === false ||
        /^[\p{P}\p{S}\s\d]+$/u.test((block.text || '').trim()) ||
        /^\d+(?:\.\d+)*\.?$/.test((block.text || '').trim()) ||
        !block.translation?.text || block.translation.status !== 'ok' ||
        block.translation.text.trim() === (block.text || '').trim()) return [];
    const sourceArea=[...area];
    const barriers = [...occupied.filter(item => item.id !== block.id).map(item => item.area),...protectedAreas];
    // 仅扩展短行/标题到空白；正文保持原栏宽，不能横跨栏沟。
    if (area[2]-area[0] < .3 || ['heading','title','caption'].includes(block.type)) {
      const column = payload.blocks.filter(item => ['paragraph','abstract'].includes(item.type))
        .map(item => blockArea(item,pageIndex)).filter(box => box && Math.abs(box[0]-area[0])<.08 && box[2]-box[0]>.3)
        .sort((a,b)=>Math.abs(a[1]-area[1])-Math.abs(b[1]-area[1]))[0];
      let right = column ? column[2] : Math.min(.95,area[0]+Math.max(area[2]-area[0],.3));
      for (const box of barriers) if (box[0]>=area[2]-.001 && box[1]<area[3] && box[3]>area[1])
        right=Math.min(right,box[0]-.012);
      area[2]=Math.max(area[2],right);
    }
    let bottom = Math.min(.98,area[3]+.018);
    for (const box of barriers) if (box[1]>=area[3]-.001 && box[0]<area[2] && box[2]>area[0])
      bottom=Math.min(bottom,box[1]-.002);
    area[3]=Math.max(area[3],bottom);
    const frame=safeFrame(area);
    if(!frame) {
      fallbacks.push({block,sourceArea,reason:'protected-region'});
      return [];
    }
    return [{block,area:frame,sourceArea}];
  });
  // 扩展后的区域也必须彼此避让，避免两块同时占用同一片空白。
  for (const entry of entries) for (const other of entries) {
    if (entry===other) continue;
    if (other.area[1]>=entry.area[1] && other.area[1]<entry.area[3] &&
        other.area[0]<entry.area[2] && other.area[2]>entry.area[0])
      entry.area[3]=Math.min(entry.area[3],other.area[1]);
  }
  // A fragment embedded on the same baseline cannot carry a rectangular
  // translation without hiding its neighbour. Retain that original fragment.
  const safeEntries=entries.filter(entry => {
    const font=entry.block.metadata?.font_size || 10;
    const minHeight=Math.max(.004,font*.85/(payload.page.display_height || 842));
    if(entry.area[3]-entry.area[1]>=minHeight)return true;
    fallbacks.push({...entry,reason:'insufficient-space'});
    return false;
  });
  return {bands:[{start:0,end:1,entries:safeEntries}],entries:safeEntries,fallbacks,inGraphic};
}

function renderFlowTarget(payload) {
  if (state.overflowObserver) state.overflowObserver.disconnect();
  state.overlayPanels=[];
  state.cards=new Map();
  const body=document.getElementById('target-body');
  renderSource(payload,{bodyId:'target-body',anchors:false});
  if (state.overlayOriginal) return;
  const sheet=body.querySelector('.page-shell');
  if (!sheet) return;
  sheet.classList.add('layout-page');
  renderFixedOverlay(payload,sheet);
}

/** 两种覆盖视图使用同一布局/适配逻辑，避免模式间出现不同的修复结果。 */
function renderFixedOverlay(payload,sheet) {
  state.overlayPanels=[];
  const plan=comparisonLayout(payload);
  const missing=payload.blocks.filter(block => block.translatable && (!block.translation?.text || block.translation.status!=='ok')).length;
  // 页外状态不能改变两侧页框的起点。
  if (missing) sheet.title=`本页有 ${missing} 个段落未成功翻译，已保留原文。`;
  for (const entry of plan.entries) {
    for (const span of entry.block.spans || []) {
      if (span.page_index!==payload.page.index) continue;
      const mask=el('div','overlay-mask');
      const [l,t,r,b]=[Math.max(span.bbox[0],entry.area[0]),Math.max(span.bbox[1],entry.area[1]),
        Math.min(span.bbox[2],entry.area[2]),Math.min(span.bbox[3],entry.area[3])];
      if(r<=l || b<=t || plan.inGraphic([l,t,r,b]))continue;
      Object.assign(mask.style,{left:`${l*100}%`,top:`${t*100}%`,width:`${(r-l)*100}%`,height:`${(b-t)*100}%`});
      sheet.appendChild(mask);
    }
    const item=el('article','overlay-panel layout-block');
    item.dataset.blockId=entry.block.id;
    item.dataset.type=entry.block.type;
    item.dataset.fontPt=String(entry.block.metadata?.font_size || 10);
    item.style.fontWeight=entry.block.metadata?.bold ? '700' : '400';
    item.tabIndex=0;
    const [l,t,r,b]=entry.area;
    Object.assign(item.style,{left:`${l*100}%`,top:`${t*100}%`,width:`${(r-l)*100}%`,height:`${(b-t)*100}%`});
    item.appendChild(fillStructuredText(el('div','overlay-text'),entry.block.translation.text,entry.block.translation.structure,{fixedFrame:true}));
    item.addEventListener('click',()=>selectBlock(entry.block.id,{scroll:false,scrollOther:false}));
    item.addEventListener('dblclick',()=>showOverlayBlock(entry.block.id));
    item.addEventListener('mouseenter',()=>highlight(entry.block.id,true));
    item.addEventListener('mouseleave',()=>highlight(entry.block.id,false));
    item.addEventListener('keydown',event=>{if(event.key==='Enter'){selectBlock(entry.block.id,{scroll:false,scrollOther:false});showOverlayBlock(entry.block.id);}});
    sheet.appendChild(item);
    state.cards.set(entry.block.id,item);
    state.overlayPanels.push(item);
  }
  // Keep graph/formula pixels intact, but never silently hide an existing
  // translation. These cards live after the fixed page, preserving its geometry.
  if(plan.fallbacks.length) {
    const section=el('section','overlay-fallbacks');
    section.appendChild(el('h3','', '图文区域译文'));
    section.appendChild(el('p','note','以下内容与图表、公式重叠或原区域空间不足，完整译文在此显示。点击可核对原文。'));
    for(const entry of plan.fallbacks) {
      const card=el('article','overlay-fallback');
      card.dataset.blockId=entry.block.id;
      card.dataset.reason=entry.reason;
      card.tabIndex=0;
      card.appendChild(fillStructuredText(el('div','tgt'),entry.block.translation.text,entry.block.translation.structure));
      const open=el('button','button','核对原文与译文');
      open.addEventListener('click',()=>showOverlayBlock(entry.block.id));
      card.appendChild(open);
      card.addEventListener('click',()=>selectBlock(entry.block.id,{scroll:false,scrollOther:false}));
      card.addEventListener('keydown',event=>{if(event.key==='Enter' && event.target===card)showOverlayBlock(entry.block.id);});
      section.appendChild(card);
      state.cards.set(entry.block.id,card);
    }
    sheet.parentNode.appendChild(section);
  }
  const resize=()=>{
    if (!sheet.isConnected) return;
    for (const item of state.overlayPanels) {
      const preferred=Number(item.dataset.fontPt)*sheet.clientWidth/payload.page.display_width*state.overlayFontScale/100;
      const min=Math.max(5,preferred*.65);
      let size=preferred;
      item.style.fontSize=`${size}px`;
      item.style.lineHeight='1.3';
      // 实测 DOM 字形排版，不能用字符数估算，更不能增加页高。
      while (state.overlayFontScale===100 && size>min &&
             (item.scrollHeight>item.clientHeight+1 || item.scrollWidth>item.clientWidth+1)) {
        size=Math.max(min,size-.25);
        item.style.fontSize=`${size}px`;
        item.style.lineHeight='1.18';
      }
      item.dataset.overflowing=item.scrollHeight>item.clientHeight+1?'1':'0';
      item.title=item.dataset.overflowing==='1'?'译文超出区域，可滚动或双击阅读完整段落。':'双击阅读完整段落。';
    }
  };
  resize();
  requestAnimationFrame(resize);
  document.fonts?.ready.then(resize);
  state.overflowObserver=new ResizeObserver(resize);
  state.overflowObserver.observe(sheet);
}

function finish(card) {
  if (!state.cards) state.cards = new Map();
  state.cards.set(card.dataset.blockId, card);
  return card;
}

function tag(text, kind) {
  return el("span", `tag${kind ? ` ${kind}` : ""}`, text);
}

/* ------------------------------------------------------------------ anchoring */

function highlight(blockId, on) {
  const boxes = state.boxes ? state.boxes.get(blockId) : null;
  const card = state.cards ? state.cards.get(blockId) : null;
  if (on) {
    if (boxes) for (const box of boxes) box.classList.add("is-selected");
    if (card) card.classList.add("is-selected");
  } else if (state.selectedBlockId !== blockId) {
    // A hover must not clear a selection the reader made by clicking.
    if (boxes) for (const box of boxes) box.classList.remove("is-selected");
    if (card) card.classList.remove("is-selected");
  }
}

function selectBlock(blockId, { scroll = true, scrollOther = true } = {}) {
  const previous = state.selectedBlockId;
  state.selectedBlockId = blockId;
  const selectedBlock = state.page?.blocks.find(block => block.id === blockId);
  if (selectedBlock) state.assistant?.setBlock(selectedBlock);
  const fullBlock = document.getElementById("overlay-full-block");
  if (fullBlock) fullBlock.disabled = !blockId;
  for (const panel of state.overlayPanels) {
    panel.classList.toggle("is-selected", panel.dataset.blockId === blockId);
  }

  const again = document.getElementById("retranslate-block");
  if (again) again.disabled = !state.page?.blocks.some(block => block.id === blockId && block.translatable);

  if (previous) {
    const oldBoxes = state.boxes ? state.boxes.get(previous) : null;
    if (oldBoxes) for (const box of oldBoxes) box.classList.remove("is-selected");
    const oldCard = state.cards ? state.cards.get(previous) : null;
    if (oldCard) oldCard.classList.remove("is-selected");
  }

  const boxes = state.boxes ? state.boxes.get(blockId) : null;
  const card = state.cards ? state.cards.get(blockId) : null;
  if (boxes) for (const box of boxes) box.classList.add("is-selected");
  if (card) card.classList.add("is-selected");

  if (scrollOther && card) card.scrollIntoView({ block: "center", behavior: "smooth" });
  if (scroll && boxes && boxes.length) boxes[0].scrollIntoView({ block: "center", behavior: "smooth" });
  if (scrollOther || scroll) {
    // `scrollIntoView` does not go through the linked-scroll guard, and it is smooth, so its
    // scroll events arrive over the next several frames. A short suspension is simpler and more
    // reliable than trying to predict where it will land.
    state.linkSuspendedUntil = performance.now() + 600;
  }

  for (const item of document.querySelectorAll(".outline-item")) {
    item.classList.toggle("is-active", item.dataset.blockId === blockId);
  }
}

/* ------------------------------------------------------------------ scroll linking */

/* The two panes are aligned block by block and nothing finer, and that is not a shortcut.
 *
 * The source side is one page image whose height follows the page's aspect ratio; the target side
 * is a stack of cards whose total height has no relation to it. Equal fractions of the two scroll
 * ranges therefore point at different paragraphs, and any scheme that interpolates between block
 * positions has to invent a correspondence that does not exist. Snapping to the block nearest the
 * top of the viewport is the only mapping that survives that - and it is also exactly what the
 * reader is doing by eye when they hunt for the matching paragraph.
 */

const PANE_PAD = 14;

function paneRelTop(node, body) {
  return node.getBoundingClientRect().top - body.getBoundingClientRect().top;
}

function anchorBounds(nodes, body) {
  const list = Array.isArray(nodes) ? nodes : [nodes];
  let top = Infinity;
  let bottom = -Infinity;
  for (const node of list) {
    const at = paneRelTop(node, body);
    top = Math.min(top, at);
    bottom = Math.max(bottom, at + node.offsetHeight);
  }
  return { top, bottom };
}

function topmostBlock(anchors, body) {
  let picked = null;
  for (const [id, nodes] of anchors) {
    const list = Array.isArray(nodes) ? nodes : [nodes];
    if (!list.length) continue;
    const { top, bottom } = anchorBounds(list, body);
    // `bottom > PANE_PAD` drops blocks that have already scrolled off the top; of the rest, the
    // one whose top is highest is the one the reader is looking at.
    if (bottom > PANE_PAD + 2 && (picked === null || top < picked.top)) picked = { id, top };
  }
  return picked ? picked.id : null;
}

function scrollPaneToBlock(body, anchors, blockId) {
  const nodes = anchors.get(blockId);
  if (!nodes) return false;
  const { top } = anchorBounds(nodes, body);
  // Browsers clamp scrollTop to the available range. Record that actual destination,
  // otherwise the resulting event looks like a user scroll and snaps the other pane back.
  const target = Math.min(Math.max(0, body.scrollTop + top - PANE_PAD),
    Math.max(0, body.scrollHeight - body.clientHeight));
  // Record the position this code is about to set. The scroll event it causes arrives on the
  // *other* pane's listener, which would otherwise read it as the reader scrolling and hand the
  // scroll straight back - the two panes would trade positions and neither would settle.
  state.linkedTo[body.id] = target;
  if (Math.abs(target - body.scrollTop) < 2) return false;
  body.scrollTop = target;
  return true;
}

function linkPanes(sourceBody, targetBody) {
  const pairs = [
    [sourceBody, targetBody, () => state.boxes, () => state.cards],
    [targetBody, sourceBody, () => state.cards, () => state.boxes],
  ];
  for (const [from, to, fromAnchors, toAnchors] of pairs) {
    from.addEventListener("scroll", () => {
      const echo = state.linkedTo[from.id];
      state.linkedTo[from.id] = null;
      if (echo !== null && Math.abs(from.scrollTop - echo) < 2) return;
      if (!state.linkScrolling) return;
      if (["side_by_side", "overlay_side_by_side"].includes(document.querySelector(".reader")?.dataset.mode)) return;
      if (performance.now() < (state.linkSuspendedUntil || 0)) return;
      const blockId = topmostBlock(fromAnchors(), from);
      if (blockId) scrollPaneToBlock(to, toAnchors(), blockId);
    }, { passive: true });
  }
}

/* ------------------------------------------------------------------ translation jobs */


async function startTranslation(documentId) {
  const version = state.routeVersion;
  const button = document.getElementById("translate-button");
  button.disabled = true;
  clearBanner();
  try {
    const plan = await api.startTranslation(documentId, {
      scope: "document", target_language: state.targetLanguage,
    });
    if (version !== state.routeVersion) return;
    showBanner(
      `已排队：${plan.planned_blocks} 块待处理，其中 ${plan.already_cached} 块已缓存无需重复发送` +
      `（${plan.skipped_non_translatable} 块不可翻译）。`, "info");
    await refreshJobs();
    if (version === state.routeVersion) startJobPolling();
  } catch (error) {
    if (version === state.routeVersion) reportError(error);
  } finally {
    button.disabled = false;
  }
}

/** Translate one block again, replacing whatever is stored for it.
 *
 *  `force` is what makes this different from asking for the document: without it the plan reuses
 *  the stored translation and nothing is sent, which is exactly what a reader who is pressing
 *  this button does not want.  The old translation stays readable until the new one lands, so a
 *  failure here leaves the page no worse than it was.
 */
async function retranslateBlock(documentId, blockId) {
  const version = state.routeVersion;
  if (!blockId) {
    showBanner("先点一个段落（左侧原文的框，或右侧译文的卡片），再按“重新翻译本块”。", "warn");
    return;
  }
  const button = document.getElementById("retranslate-block");
  if (button) button.disabled = true;
  clearBanner();
  try {
    const plan = await api.startTranslation(documentId, {
      scope: "blocks", block_ids: [blockId], target_language: state.targetLanguage, force: true,
    });
    if (version !== state.routeVersion) return;
    showBanner(`已排队重新翻译这一段（${plan.would_be_sent} 块将发送）。`, "info");
    await refreshJobs();
    if (version === state.routeVersion) startJobPolling();
  } catch (error) {
    if (version === state.routeVersion) reportError(error);
  } finally {
    if (button) button.disabled = false;
  }
}

async function refreshJobs() {
  const box = document.getElementById("jobs-box");
  if (!box || !state.document) return;
  const documentId = state.document.id;
  const version = state.routeVersion;
  let payload;
  try {
    payload = await api.listJobs(documentId);
  } catch { return false; }
  if (version !== state.routeVersion || state.document?.id !== documentId) return;
  state.jobs = payload.jobs || [];
  box.innerHTML = "";
  box.appendChild(el("h3", null, "任务"));
  if (!state.jobs.length) {
    box.appendChild(el("p", "job-detail", "暂无任务。"));
    return true;
  }
  for (const job of state.jobs.slice(0, 6)) box.appendChild(jobRow(job));
  return true;
}

function jobRow(job) {
  const row = el("div");
  const line = el("div", "job-row");
  const running = job.status === "running" || job.status === "queued";
  if (running) line.appendChild(el("span", "spinner"));
  line.appendChild(el("span", `job-status ${job.status}`, statusText(job.status)));
  line.appendChild(el("span", "spacer", `${({parse:"解析",translate:"翻译",ocr:"文字识别",convert:"转换",export:"导出"})[job.type] || job.type} · ${job.done_units}/${job.total_units}`));
  row.appendChild(line);

  if (job.result && job.result.translated != null) {
    row.appendChild(el("p", "job-detail",
      `已译 ${job.result.translated}，失败 ${job.result.failed}，不确定 ${job.result.unknown}，复用 ${job.result.reused}`));
  }
  if (job.error_message) row.appendChild(el("p", "job-detail", shortError(job.error_message)));

  if (running) {
    const cancel = el("button", "button", "取消");
    cancel.addEventListener("click", async () => {
      await api.cancelJob(job.id).catch(reportError);
      await refreshJobs();
    });
    row.appendChild(cancel);
  }
  if (job.status === "failed" || job.status === "partially_succeeded") {
    const retry = el("button", "button", "重试失败项");
    retry.addEventListener("click", async () => {
      await api.retryJob(job.id).catch(reportError);
      await refreshJobs();
      startJobPolling();
    });
    row.appendChild(retry);
  }
  return row;
}

function statusText(status) {
  return {
    pending: "待处理", queued: "排队中", running: "进行中", succeeded: "完成",
    partially_succeeded: "部分完成", failed: "失败", cancelled: "已取消",
  }[status] || status;
}

function shortError(message) {
  const text = String(message);
  return text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

function isActiveJob(job) {
  return job.status === "running" || job.status === "queued";
}

function startJobPolling() {
  stopJobPolling();
  const version = state.routeVersion;
  const pollingVersion = state.pollingVersion;
  async function poll() {
    if (version !== state.routeVersion || pollingVersion !== state.pollingVersion || !state.document) return;
    const previous = new Map(state.jobs.map(job => [job.id, job.status]));
    const refreshed = await refreshJobs();
    if (version !== state.routeVersion || pollingVersion !== state.pollingVersion || !state.document) return;
    if (!refreshed) {
      state.jobsTimer = setTimeout(poll, 1200);
      return;
    }
    const settled = state.jobs.filter(job => !isActiveJob(job) &&
      ["queued", "running"].includes(previous.get(job.id)));
    // A short job may finish before the first refresh, so also reconcile document metadata.
    let latest;
    try { latest = await api.getDocument(state.document.id); } catch { /* Retry on next tick. */ }
    if (version !== state.routeVersion || pollingVersion !== state.pollingVersion || !state.document) return;
    const changed = latest && ["active_revision_id", "parse_status", "translate_status"]
      .some(key => latest[key] !== state.document[key]);
    if (settled.length || changed) {
      await route();
      if (state.routeVersion !== version + 1) return;
      const failed = settled.find(job => ["failed", "partially_succeeded"].includes(job.status));
      if (failed) showBanner(`任务${statusText(failed.status)}：${shortError(failed.error_message || "请查看任务详情")}`,
        failed.status === "failed" ? "error" : "info");
      return;
    }
    if (state.jobs.some(isActiveJob)) state.jobsTimer = setTimeout(poll, 1200);
    else {
      stopJobPolling();
      // Includes cached/immediately completed translation jobs.
      await loadPage(state.pageIndex);
    }
  }
  state.jobsTimer = setTimeout(poll, 1200);
}

function stopJobPolling() {
  ++state.pollingVersion;
  if (state.jobsTimer) { clearTimeout(state.jobsTimer); state.jobsTimer = null; }
}

/* ------------------------------------------------------------------ chrome */

function setTranslationBadge(configuration) {
  const badge = els.badge;
  if (configuration.configured) {
    badge.className = "badge badge-ok";
    badge.textContent = `翻译：已配置（${configuration.model || configuration.provider}）`;
    badge.title = `端点 ${configuration.base_url}${configuration.base_url_is_default ? "（默认值，已实测可达）" : ""}`;
  } else {
    badge.className = "badge badge-warn";
    badge.textContent = "翻译：未配置";
    badge.title = "点“翻译设置”填入服务地址、模型和 API Key。不需要重启服务，阅读、锚定与导出本来就不受影响。";
  }
}

/** Re-read the configuration and repaint the badge.  Called after the settings panel saves, so
 *  the topbar cannot keep saying "未配置" while the service is configured. */
async function refreshTranslationStatus() {
  try {
    const configuration = await api.translationConfiguration();
    state.translation = configuration;
    setTranslationBadge(configuration);
  } catch {
    els.badge.className = "badge badge-unknown";
    els.badge.textContent = "翻译：无法确认";
  }
}

async function boot() {
  els.fileInput.closest?.(".file-button")?.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); els.fileInput.click(); }
  });
  document.getElementById("overlay-block-close").addEventListener("click", () =>
    document.getElementById("overlay-block-dialog").close());
  els.targetLanguage.value = state.targetLanguage;
  els.targetLanguage.addEventListener("change", async () => {
    state.targetLanguage = els.targetLanguage.value;
    localStorage.setItem("rr.targetLanguage", state.targetLanguage);
    if (state.route.name === "reader" && state.document) {
      const exportLink = document.getElementById("export-link");
      if (exportLink) exportLink.href = api.exportUrl(state.document.id, state.targetLanguage);
      await loadPage(state.requestedPageIndex);
    }
  });

  els.fileInput.addEventListener("change", async () => {
    const files=Array.from(els.fileInput.files || []);
    if (!files.length) return;
    const library=state.library;
    const inLibrary=state.route.name==='library';
    const collectionId=library?.activeCollection();
    clearBanner();
    const imported=[], failures=[];
    els.fileInput.disabled=true;
    for (let index=0;index<files.length;index++) {
      const file=files[index];
      showBanner(`正在导入 ${index+1}/${files.length}：${file.name}…`,"info");
      try {
        const result=await api.upload(file);
        imported.push(result);
        if (collectionId) await api.libraryBatch({ids:[result.document.id],action:'add_collection',collection_id:collectionId});
      } catch (error) { failures.push(`${file.name}：${error.message}`); }
    }
    els.fileInput.value='';els.fileInput.disabled=false;
    if (!inLibrary && files.length===1 && imported.length===1) {
      location.hash=`#/doc/${imported[0].document.id}`;
    } else {
      if (state.route.name==='library') await state.library?.load();
      else location.hash='#/';
      showBanner(`已导入 ${imported.length} 篇，复用或恢复 ${imported.filter(r=>r.duplicate).length} 篇。${failures.length ? '失败：'+failures.join('；') : '解析任务会在后台继续。'}`,failures.length?'error':'info');
    }
  });

  try {
    const configuration = await api.translationConfiguration();
    state.translation = configuration;
    setTranslationBadge(configuration);
    if (!configuration.configured) {
      els.banner.hidden = true;
    }
  } catch {
    els.badge.className = "badge badge-unknown";
    els.badge.textContent = "翻译：无法确认";
  }

  // The settings panel owns the translation configuration.  Saving there must repaint the
  // topbar, or the reader would configure the service and still be told it is 未配置.
  createSettingsPanel({ onChanged: refreshTranslationStatus });

  window.addEventListener("hashchange", () => { route(); });
  await route();
}

boot();
