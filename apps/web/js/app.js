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
  state.route = parseHash();
  stopJobPolling();
  if (state.route.name === "reader") {
    await openReader(state.route.documentId);
  } else {
    state.document = null;
    await renderLibrary();
  }
}

/* ------------------------------------------------------------------ library */

async function renderLibrary() {
  els.view.innerHTML = "";
  const wrap = el("div", "library");
  wrap.appendChild(el("h1", null, "文献库"));
  wrap.appendChild(el("p", "lead",
    "原件不可变：导入的文件按内容哈希去重，永不改写。解析、翻译与标注都与不可变的原件分开保存。"));

  const list = el("div", "doc-list");
  wrap.appendChild(list);
  els.view.appendChild(wrap);

  let payload;
  try {
    payload = await api.listDocuments();
  } catch (error) {
    reportError(error);
    list.appendChild(el("p", "empty", "无法读取文献库。"));
    return;
  }
  if (!payload.items.length) {
    list.appendChild(el("p", "empty", "还没有文献。点击右上角「导入文献」开始。"));
    return;
  }
  for (const doc of payload.items) {
    list.appendChild(documentCard(doc));
  }
}

function documentCard(doc) {
  const card = el("a", "doc-card");
  card.href = `#/doc/${doc.id}`;
  card.appendChild(el("h2", null, doc.title || doc.filename));

  const meta = el("div", "doc-meta");
  const bits = [
    `${doc.page_count ?? "?"} 页`,
    `${doc.block_count} 块`,
    `${doc.translated_block_count} 已译`,
    `解析：${doc.parse_status}`,
    `${(doc.size_bytes / 1024).toFixed(0)} KB`,
  ];
  if (doc.warning_count) bits.push(`${doc.warning_count} 条警告`);
  for (const bit of bits) meta.appendChild(el("span", null, bit));
  card.appendChild(meta);

  const ratio = doc.block_count ? doc.translated_block_count / doc.block_count : 0;
  const progress = el("div", "progress");
  const bar = el("span");
  bar.style.width = `${Math.round(ratio * 100)}%`;
  progress.appendChild(bar);
  card.appendChild(progress);
  return card;
}

/* ------------------------------------------------------------------ reader */

async function openReader(documentId) {
  els.view.innerHTML = "";
  clearBanner();
  try {
    state.document = await api.getDocument(documentId);
  } catch (error) {
    reportError(error);
    els.view.appendChild(el("p", "empty", "无法打开该文献。"));
    return;
  }
  const doc = state.document;

  const saved = await api.getReadingState(documentId).catch(() => ({ reading_state: null }));
  state.pageIndex = Math.min(
    saved.reading_state ? saved.reading_state.page_index : 0,
    Math.max((doc.page_count || 1) - 1, 0),
  );

  const outlinePayload = await api.outline(documentId).catch(() => ({ outline: [] }));
  state.outline = outlinePayload.outline || [];
  state.outlineNote = outlinePayload.note || null;

  state.annotations = (await api.listAnnotations(documentId).catch(() => ({ annotations: [] }))).annotations || [];

  const reader = el("div", "reader");
  reader.dataset.mode = (saved.reading_state && saved.reading_state.view_mode) || "side_by_side";
  reader.appendChild(buildSidebar(doc, outlinePayload));
  reader.appendChild(buildPanes(doc));
  els.view.appendChild(reader);

  await loadPage(state.pageIndex);
  await refreshJobs();
}

function buildSidebar(doc, outlinePayload) {
  const sidebar = el("aside", "sidebar");

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
          loadPage(entry.page_index).then(() => selectBlock(entry.block_id, { scroll: true }));
        } else {
          selectBlock(entry.block_id, { scroll: true });
        }
      });
      button.dataset.blockId = entry.block_id;
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
    `解析：${doc.parse_status}`,
    `翻译：${doc.translate_status}`,
    `修订：${doc.active_revision_id || "无"}`,
    `SHA-256：${String(doc.sha256).slice(0, 16)}…`,
  ];
  for (const fact of facts) sidebar.appendChild(el("p", "job-detail", fact));

  const actions = el("div");
  actions.style.display = "grid";
  actions.style.gap = "6px";

  const original = el("a", "button", "打开原件");
  original.href = api.originalUrl(doc.id);
  original.target = "_blank";
  original.rel = "noopener";
  actions.appendChild(original);

  const exportLink = el("a", "button", "导出双语 Markdown");
  exportLink.href = api.exportUrl(doc.id, state.targetLanguage);
  actions.appendChild(exportLink);

  const remove = el("button", "button danger", "删除文献");
  remove.addEventListener("click", async () => {
    if (!confirm(`删除「${doc.title || doc.filename}」？原件、解析、翻译与标注都会一并删除，无法撤销。`)) return;
    try {
      await api.deleteDocument(doc.id);
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

function buildPanes(doc) {
  const panes = el("div", "panes");

  /* -- source -- */
  const source = el("section", "pane pane-source");
  const sourceHead = el("div", "pane-head");
  sourceHead.appendChild(el("strong", null, "原文"));
  const sourceSpacer = el("span", "spacer");
  sourceHead.appendChild(sourceSpacer);
  const pageLabel = el("span", null, "…");
  pageLabel.id = "page-label";
  sourceHead.appendChild(pageLabel);
  source.appendChild(sourceHead);

  const sourceBody = el("div", "pane-body");
  sourceBody.id = "source-body";
  source.appendChild(sourceBody);

  /* -- translation -- */
  const target = el("section", "pane pane-target");
  const targetHead = el("div", "pane-head");
  targetHead.appendChild(el("strong", null, "译文"));
  targetHead.appendChild(el("span", "spacer"));

  const prev = el("button", "button", "上一页");
  prev.addEventListener("click", () => gotoPage(state.pageIndex - 1));
  const next = el("button", "button", "下一页");
  next.addEventListener("click", () => gotoPage(state.pageIndex + 1));
  targetHead.appendChild(prev);
  targetHead.appendChild(next);

  const mode = el("select");
  for (const [value, label] of [
    ["side_by_side", "左右对照"], ["stacked", "上下对照"],
    ["source_only", "仅原文"], ["target_only", "仅译文"],
  ]) {
    const option = el("option", null, label);
    option.value = value;
    mode.appendChild(option);
  }
  mode.addEventListener("change", async () => {
    const reader = document.querySelector(".reader");
    reader.dataset.mode = mode.value;
    await api.putReadingState(doc.id, {
      page_index: state.pageIndex, view_mode: mode.value, zoom: 1, link_scrolling: true,
    }).catch(() => {});
  });
  targetHead.appendChild(mode);

  const translateButton = el("button", "button primary", "翻译全文");
  translateButton.id = "translate-button";
  translateButton.addEventListener("click", () => startTranslation(doc.id));
  targetHead.appendChild(translateButton);

  target.appendChild(targetHead);
  const targetBody = el("div", "pane-body");
  targetBody.id = "target-body";
  target.appendChild(targetBody);

  panes.appendChild(source);
  panes.appendChild(target);
  return panes;
}

/* ------------------------------------------------------------------ page load */

async function loadPage(index) {
  const doc = state.document;
  if (!doc) return;
  const total = doc.page_count || 1;
  if (index < 0 || index >= total) return;

  let payload;
  try {
    payload = await api.page(doc.id, index, state.targetLanguage);
  } catch (error) {
    reportError(error);
    return;
  }
  state.page = payload;
  state.pageIndex = index;
  state.selectedBlockId = null;

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
    link_scrolling: true,
  }).catch(() => {});
}

function gotoPage(index) {
  const total = state.document.page_count || 1;
  if (index < 0 || index >= total) return;
  loadPage(index);
}

/* ------------------------------------------------------------------ source pane */

function renderSource(payload) {
  const body = document.getElementById("source-body");
  body.innerHTML = "";
  state.boxes = new Map();

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
      if (!state.boxes.has(block.id)) state.boxes.set(block.id, []);
      state.boxes.get(block.id).push(box);
    }
  }
  body.appendChild(shell);
}

/* ------------------------------------------------------------------ translation pane */

function renderTarget(payload) {
  const body = document.getElementById("target-body");
  body.innerHTML = "";
  state.cards = new Map();

  for (const block of payload.blocks) {
    const card = el("article", "card");
    card.dataset.blockId = block.id;
    card.addEventListener("mouseenter", () => highlight(block.id, true));
    card.addEventListener("mouseleave", () => highlight(block.id, false));
    card.addEventListener("click", () => selectBlock(block.id, { scroll: false, scrollOther: true }));

    const tags = el("div");
    tags.style.marginBottom = "4px";
    tags.appendChild(tag(blockLabel(block), null));

    if (block.type === "title") {
      card.appendChild(el("h4", null, block.text));
      body.appendChild(finish(card));
      continue;
    }
    if (VERBATIM_TYPES.has(block.type)) {
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
    card.appendChild(el("p", "src", block.text));

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
      const text = el("p", "tgt");
      text.textContent = translation.text;
      card.appendChild(text);
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

  for (const item of document.querySelectorAll(".outline-item")) {
    item.classList.toggle("is-active", item.dataset.blockId === blockId);
  }
}

/* ------------------------------------------------------------------ translation jobs */

async function startTranslation(documentId) {
  const button = document.getElementById("translate-button");
  button.disabled = true;
  clearBanner();
  try {
    const plan = await api.startTranslation(documentId, {
      scope: "document", target_language: state.targetLanguage,
    });
    showBanner(
      `已排队：${plan.planned_blocks} 块待处理，其中 ${plan.already_cached} 块已缓存无需重复发送` +
      `（${plan.skipped_non_translatable} 块不可翻译）。`, "info");
    await refreshJobs();
    startJobPolling();
  } catch (error) {
    reportError(error);
  } finally {
    button.disabled = false;
  }
}

async function refreshJobs() {
  const box = document.getElementById("jobs-box");
  if (!box || !state.document) return;
  let payload;
  try {
    payload = await api.listJobs(state.document.id);
  } catch { return; }
  state.jobs = payload.jobs || [];
  box.innerHTML = "";
  box.appendChild(el("h3", null, "任务"));
  if (!state.jobs.length) {
    box.appendChild(el("p", "job-detail", "暂无任务。"));
    return;
  }
  for (const job of state.jobs.slice(0, 6)) box.appendChild(jobRow(job));
}

function jobRow(job) {
  const row = el("div");
  const line = el("div", "job-row");
  const running = job.status === "running" || job.status === "queued";
  if (running) line.appendChild(el("span", "spinner"));
  line.appendChild(el("span", `job-status ${job.status}`, statusText(job.status)));
  line.appendChild(el("span", "spacer", `${job.type} · ${job.done_units}/${job.total_units}`));
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
    queued: "排队中", running: "进行中", succeeded: "完成",
    partially_succeeded: "部分完成", failed: "失败", cancelled: "已取消",
  }[status] || status;
}

function shortError(message) {
  const text = String(message);
  return text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

function startJobPolling() {
  stopJobPolling();
  state.jobsTimer = setInterval(async () => {
    const active = state.jobs.some((job) => job.status === "running" || job.status === "queued");
    await refreshJobs();
    const stillActive = state.jobs.some((job) => job.status === "running" || job.status === "queued");
    if (active && !stillActive) {
      // A job just settled: reload the page so new translations appear, and say what happened
      // rather than silently redrawing.
      const finished = state.jobs[0];
      if (finished) {
        if (finished.status === "partially_succeeded") {
          showBanner(
            `翻译部分完成：${finished.error_message || "部分块未成功"}。未成功的块已单独标记，未用占位文本填充。`,
            "info");
        } else if (finished.status === "failed") {
          showBanner(`翻译失败：${shortError(finished.error_message || "")}`, "error");
        }
      }
      stopJobPolling();
      await loadPage(state.pageIndex);
    }
  }, 1200);
}

function stopJobPolling() {
  if (state.jobsTimer) { clearInterval(state.jobsTimer); state.jobsTimer = null; }
}

/* ------------------------------------------------------------------ chrome */

function setTranslationBadge(configuration) {
  const badge = els.badge;
  if (configuration.configured) {
    badge.className = "badge badge-ok";
    badge.textContent = `翻译：已配置（${configuration.model || configuration.provider}）`;
    badge.title = `端点 ${configuration.base_url}${configuration.base_url_is_default ? "（默认值，未经验证）" : ""}`;
  } else {
    badge.className = "badge badge-warn";
    badge.textContent = "翻译：未配置";
    badge.title = "设置 RR_TRANSLATION_API_KEY 后重启服务即可启用翻译。阅读、锚定与导出不受影响。";
  }
}

async function boot() {
  els.targetLanguage.value = state.targetLanguage;
  els.targetLanguage.addEventListener("change", async () => {
    state.targetLanguage = els.targetLanguage.value;
    localStorage.setItem("rr.targetLanguage", state.targetLanguage);
    if (state.route.name === "reader") await loadPage(state.pageIndex);
  });

  els.fileInput.addEventListener("change", async () => {
    const file = els.fileInput.files[0];
    if (!file) return;
    clearBanner();
    showBanner(`正在导入 ${file.name}…`, "info");
    try {
      const result = await api.upload(file);
      if (result.duplicate) {
        showBanner("这份文件之前已导入过（内容哈希相同），已直接打开原有记录。", "info");
      } else {
        showBanner("导入完成，解析任务已排队。解析由后台 worker 执行。", "info");
      }
      location.hash = `#/doc/${result.document.id}`;
      if (location.hash === `#/doc/${result.document.id}`) await route();
    } catch (error) {
      reportError(error);
    } finally {
      els.fileInput.value = "";
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

  window.addEventListener("hashchange", () => { route(); });
  await route();
}

boot();
