import { api } from "./api.js";

// 仅保存在本次页面会话中，不将问题或回答写入本机文献及翻译缓存。
const conversations = new Map();
function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

export function createAssistantPanel({ documentId, getPage, onSource }) {
  const panel = node("aside", "assistant-panel");
  panel.id = "assistant-panel";
  panel.hidden = true;
  const head = node("div", "assistant-head");
  head.appendChild(node("strong", null, "AI 阅读助手"));
  const close = node("button", "button", "关闭");
  close.type = "button";
  close.addEventListener("click", () => { controller?.abort();panel.hidden = true; });
  head.appendChild(close);
  panel.appendChild(head);
  panel.appendChild(node("p", "assistant-notice",
    "复用翻译服务。发送问题、划词和最多 8 段相关原文，可能计费；AI 回答请结合来源核对。"));
  const log = node("div", "assistant-log");
  log.setAttribute("aria-live", "polite");
  panel.appendChild(log);
  const context = node("p", "assistant-context", "可在原文、译文或完整段落中划词，也可点击段落或公式后提问。");
  panel.appendChild(context);
  const actions = node("div", "assistant-actions");
  const kinds = [["question", "划词提问"], ["term", "解释术语"], ["formula", "解释公式"]];
  let selected = "", blockId = null, busy = false, alive = true,controller=null;
  const history = conversations.get(documentId) || [];
  conversations.set(documentId, history);
  const textarea = node("textarea", "assistant-question");
  textarea.id = "assistant-question";
  textarea.rows = 3;
  textarea.maxLength = 2000;
  textarea.placeholder = "例如：本文的方法与基线有什么区别？";
  textarea.setAttribute("aria-label", "向 AI 提问");
  const scope = node("select");
  scope.id = "assistant-scope";
  scope.setAttribute("aria-label", "回答依据");
  for (const [value, label] of [["document", "依据当前文献"], ["general", "文献＋通用知识"]]) {
    const option = node("option", null, label);
    option.value = value;
    scope.appendChild(option);
  }
  const contextScope=node('select');contextScope.id='assistant-context-scope';contextScope.setAttribute('aria-label','取文范围');
  for(const [value,label] of [['auto','范围：自动判断'],['document','范围：整篇文献'],['selection','范围：所选内容']]){
    const option=node('option',null,label);option.value=value;contextScope.appendChild(option);
  }
  contextScope.value='auto';
  function coverageText(value){
    if(!value)return '';
    const names={overview:'全文概括',document:'全文检索',selection:'所选内容',related:'相关内容检索'};
    return `${names[value.strategy] || '文献检索'} · 已提供 ${value.body_selected} 段正文 · 覆盖 ${value.pages?.length || 0} 页（原文节选）`;
  }
  const effort=node('select');effort.id='assistant-effort';effort.setAttribute('aria-label','思考强度');
  const effortNames={auto:'模型默认',low:'低',medium:'中',high:'高'};
  for(const value of ['auto','low','medium','high']){const option=node('option',null,`思考强度：${effortNames[value]}`);option.value=value;effort.appendChild(option);}
  effort.value='low';effort.disabled=true;
  const effortHint=node('p','assistant-context','正在检查模型的思考强度设置…');
  api.assistantCapabilities().then(value=>{if(!alive)return;effort.disabled=!value.effort_supported;
    if(!value.effort_supported)effort.value='auto';effortHint.textContent=value.effort_supported?'强度为本次请求的设置，实际支持与耗时由模型决定。':'此服务未确认支持强度设置，使用模型默认。';
  }).catch(()=>{effort.value='auto';effortHint.textContent='未确认强度设置，使用模型默认。';});
  function addSummary(answer,text){
    const details=node('details','assistant-summary');details.appendChild(node('summary',null,'回答依据摘要'));
    details.appendChild(node('p','assistant-summary-text',text));answer.appendChild(details);return details;
  }
  function appendAnswer(result) {
    const answer = node("article", "assistant-answer");
    answer.appendChild(node("p", "assistant-answer-text", result.answer));
    if(result.summary)addSummary(answer,result.summary);
    for (const warning of result.warnings || []) answer.appendChild(node("p", "note bad", warning));
    const sources = result.citations?.length ? result.citations : result.sources || [];
    answer.appendChild(node("p", "assistant-context", result.citations?.length ? "回答引用的段落" : "供核对的上下文段落（模型未标注引用）"));
    for (const source of sources) {
      const button = node("button", "button assistant-source", `[${source.id}] 第 ${source.page_number} 页 · ${source.text.slice(0, 45)}`);
      button.title = source.text;
      button.addEventListener("click", () => onSource(source));
      answer.appendChild(button);
    }
    answer.appendChild(node("p", "assistant-context", `模型：${result.model} · ${result.scope === "document" ? "文献依据" : "允许通用解释"}`));
    if(result.context_summary?.strategy)answer.appendChild(node('p','assistant-context assistant-coverage',coverageText(result.context_summary)));
    if(result.metrics){const reasoning=result.usage?.completion_tokens_details?.reasoning_tokens;
      answer.appendChild(node('p','assistant-context',`用时 ${(result.metrics.elapsed_ms/1000).toFixed(1)}s · 请求强度：${effortNames[result.reasoning_effort] || '模型默认'}${Number.isFinite(reasoning)?` · 思考用量：${reasoning} tokens`:''}`));}
    log.appendChild(answer);
    log.scrollTop = log.scrollHeight;
  }
  for (const entry of history) {
    log.appendChild(node("p", "assistant-user", entry.question));
    appendAnswer(entry.result);
  }
  const welcome = node("div", "assistant-welcome");
  if (!history.length) {
    welcome.appendChild(node("h3", null, "从文献中寻找答案"));
    welcome.appendChild(node("p", null, "围绕研究问题、方法和结论提问；划词后也可以解释术语或公式。"));
    for (const prompt of ["概括本文的研究问题", "解释本文的方法", "总结主要结论与局限"]) {
      const suggestion = node("button", "button assistant-suggestion", prompt);
      suggestion.type = "button";
      suggestion.addEventListener("click", () => { textarea.value = prompt; textarea.focus(); });
      welcome.appendChild(suggestion);
    }
    log.appendChild(welcome);
  }
  async function ask(kind = "question") {
    if (busy || !alive) return;
    let question = textarea.value.trim();
    if (kind === "question" && selected && !question) question = "请结合本文解释所选文字，并说明它在文中的含义。";
    if (kind === "term") question = question || "请解释所选术语在本文中的含义。";
    if (kind === "formula") question = question || "请解释所选公式中的符号、前提和用途。";
    if (!question) { textarea.focus(); return; }
    welcome.remove();
    busy = true;
    send.disabled = true;
    for (const button of actions.querySelectorAll("button")) button.disabled = true;
    const waiting = node("p", "assistant-context", "AI 正在回答…");
    log.appendChild(node("p", "assistant-user", question));
    log.appendChild(waiting);
    const streaming=node('article','assistant-answer assistant-streaming');
    const progress=node('div','assistant-progress');progress.setAttribute('role','status');
    const phase=node('span','assistant-phase','连接模型');const timer=node('span','assistant-elapsed','0.0s');
    const strength=node('span','assistant-strength',`请求强度：${effortNames[effort.value]}`);strength.dataset.effort=effort.value;
    progress.append(phase,timer,strength);streaming.appendChild(progress);
    const coverage=node('p','assistant-context assistant-coverage','正在提取本次回答依据…');streaming.appendChild(coverage);
    const summary=addSummary(streaming,'');summary.hidden=true;
    const liveText=node('p','assistant-answer-text','');streaming.appendChild(liveText);log.appendChild(streaming);
    let answerText='',summaryText='',metadata=null;const started=performance.now();
    const tick=setInterval(()=>{timer.textContent=`${((performance.now()-started)/1000).toFixed(1)}s`;},250);
    controller=new AbortController();stop.disabled=false;
    log.scrollTop = log.scrollHeight;
    try {
      const page = getPage();
      const result = await api.streamAssistant(documentId, { question, kind, selected_text: selected,
        block_id: blockId, page_index: page?.page.index || 0, scope: scope.value,
        context_scope:contextScope.value,
        reasoning_effort:effort.value,include_summary:true,
        history: history.filter(entry=>!entry.result.incomplete).slice(-4).flatMap(entry => [{ role: "user", content: entry.question },
          { role: "assistant", content: entry.result.answer.slice(0, 6000) }]) },{signal:controller.signal,onEvent:(kind,value)=>{
            if(!alive)return;
            if(kind==='meta'){metadata=value;waiting.remove();coverage.textContent=coverageText(value.context_summary) || '已提取本次来源。';}
            if(kind==='phase')phase.textContent=({connecting:'连接模型',thinking:'模型思考中',summarizing:'生成依据摘要',answering:'回答输出中'})[value.phase] || '处理中';
            if(kind==='summary'){summaryText+=value.text;summary.hidden=false;summary.querySelector('p').textContent=summaryText;}
            if(kind==='delta'){answerText+=value.text;liveText.textContent=answerText;}
            log.scrollTop=log.scrollHeight;
          }});
      if (!alive) return;
      history.push({ question, result });
      if (history.length > 30) history.shift();
      streaming.remove();appendAnswer(result);
      textarea.value = "";
    } catch (error) {
      if(alive){
        phase.textContent=error.name==='AbortError'?'已停止接收':'回答中断';
        streaming.classList.remove('assistant-streaming');
        const message=error.name==='AbortError'?'已停止接收，已有内容保留；服务可能仍已处理本次请求。':error.message || 'AI 请求失败，请稍后重试。';
        streaming.appendChild(node('p','note bad',message));
        if(answerText)history.push({question,result:{...metadata,answer:answerText,summary:summaryText,incomplete:true,warnings:[message]}});
      }
    } finally {
      clearInterval(tick);stop.disabled=true;controller=null;
      waiting.remove();
      busy = false;
      send.disabled = false;
      for (const button of actions.querySelectorAll("button")) button.disabled = false;
    }
  }
  for (const [kind, label] of kinds) {
    const button = node("button", "button", label);
    button.type = "button";
    button.addEventListener("click", () => ask(kind));
    actions.appendChild(button);
  }
  const clearSelection = node("button", "button", "清除选区");
  clearSelection.type = "button";
  clearSelection.addEventListener("click", () => {
    selected = ""; blockId = null; context.textContent = "未限定段落，按当前文献检索相关内容。";
  });
  actions.appendChild(clearSelection);
  panel.appendChild(actions);
  panel.appendChild(textarea);
  const foot = node("div", "assistant-actions");
  foot.appendChild(scope);
  foot.appendChild(contextScope);
  foot.appendChild(effort);
  const send = node("button", "button primary", "发送问题");
  send.id = "assistant-send";
  send.type = "button";
  send.addEventListener("click", () => ask());
  foot.appendChild(send);
  const stop=node('button','button','停止');stop.type='button';stop.id='assistant-stop';stop.disabled=true;stop.addEventListener('click',()=>controller?.abort());foot.appendChild(stop);
  panel.appendChild(foot);
  panel.appendChild(effortHint);
  textarea.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); ask(); }
  });
  function setBlock(block) {
    if (!block || busy) return;
    if (window.getSelection()?.toString().trim()) return;
    blockId = block.id;
    selected = block.text.slice(0, 6000);
    context.textContent = `当前段落：${selected.slice(0, 160)}`;
  }
  function captureSelection() {
    const selection = window.getSelection();
    const text = selection?.toString().trim();
    if (!text || text.length > 6000) return;
    const origin = selection.anchorNode?.nodeType === 1 ? selection.anchorNode : selection.anchorNode?.parentElement;
    if (!origin || origin.closest("#assistant-panel")) return;
    if (!origin.closest(".reader, #overlay-block-dialog")) return;
    selected = text;
    blockId = origin.closest("[data-block-id]")?.dataset.blockId || null;
    context.textContent = `已划词：${text.slice(0, 160)}`;
    panel.hidden = false;
  }
  document.addEventListener("mouseup", captureSelection);
  return { element: panel, toggle: () => { panel.hidden = !panel.hidden; }, setBlock,
    dispose: () => { alive = false;controller?.abort();document.removeEventListener("mouseup", captureSelection); } };
}
