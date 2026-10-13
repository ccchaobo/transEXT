import { api } from './api.js';
import { icon, createActionMenu, closeActionMenu } from './ui.js';
import { renderAnswer } from './assistant-markdown.js';

// Dialogue stays in this page session and never changes originals or translations.
const conversations = new Map();
const effortNames = { auto:'模型默认', low:'低', medium:'标准', high:'深入' };
const prompts = ['概括本文的研究问题','解释本文的方法','总结主要结论与局限'];
function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function button(label, action, className='button') {
  const item=node('button',className,label); item.type='button'; item.addEventListener('click',action); return item;
}
function iconButton(label,name,action) {
  const item=button('',action,'button icon-button'); item.appendChild(icon(name)); item.title=label; item.setAttribute('aria-label',label); return item;
}

export function createAssistantPanel({ documentId, documentTitle='当前文献', getPage, onSource }) {
  let selected='', blockId=null, busy=false, alive=true, controller=null, effortSupported=false, renderFrame=0;
  let followOutput=true, model='已配置的模型', contextReady=false;
  const history=conversations.get(documentId)||[]; conversations.set(documentId,history);
  const panel=node('aside','assistant-panel'); panel.id='assistant-panel'; panel.hidden=true;
  const head=node('div','assistant-head');
  const heading=node('div','assistant-heading'); heading.append(node('strong',null,'文献助手'),node('span',null,'与你的文献对话'));
  const headActions=node('div','assistant-head-actions');
  const settingsDialog=node('dialog','settings-dialog assistant-settings-dialog');
  settingsDialog.setAttribute('aria-label','对话设置'); settingsDialog.appendChild(node('h2',null,'对话设置'));
  const settingsIntro=node('p','settings-intro','每次提问都使用完整文献文本。以下设置只调整回答方式。'); settingsDialog.appendChild(settingsIntro);
  const scope=node('select'); scope.id='assistant-scope'; scope.setAttribute('aria-label','回答依据');
  for (const [value,label] of [['document','仅依据文献'],['general','允许通用知识补充']]) { const option=node('option',null,label); option.value=value; scope.appendChild(option); }
  const effort=node('select'); effort.id='assistant-effort'; effort.setAttribute('aria-label','思考强度');
  for (const value of ['auto','low','medium','high']) { const option=node('option',null,effortNames[value]); option.value=value; effort.appendChild(option); }
  effort.value='auto'; effort.disabled=true;
  const effortHint=node('p','settings-help','正在检查模型支持情况…');
  for (const [label,control] of [['回答依据',scope],['思考强度',effort]]) { const wrap=node('label','library-field'); wrap.append(node('span',null,label),control); settingsDialog.appendChild(wrap); }
  settingsDialog.appendChild(effortHint);
  const settingsFooter=node('div','settings-actions'); settingsFooter.appendChild(button('完成',()=>settingsDialog.close(),'button primary')); settingsDialog.appendChild(settingsFooter);
  const options=createActionMenu({label:'对话选项',items:()=>[
    {label:'新对话',icon:'plus',disabled:busy,onSelect:newConversation},
    {label:'对话设置',icon:'settings',disabled:busy,onSelect:()=>settingsDialog.showModal()},
  ]});
  const close=iconButton('关闭文献助手','close',()=>{controller?.abort(); closeActionMenu(panel); panel.hidden=true;});
  headActions.append(options.button,close); head.append(heading,headActions); panel.appendChild(head);

  const documentChip=node('div','assistant-document');
  const documentCopy=node('div'); const documentName=node('strong',null,documentTitle); documentName.title=documentTitle;
  const coverage=node('span','assistant-document-status','正在接入全文文本…');
  documentCopy.append(documentName,coverage); documentChip.append(icon('file'),documentCopy); panel.appendChild(documentChip);
  function coverageText(value) { return `完整全文文本 · ${value.document_pages || value.pages?.length || 0} 页 · ${value.source_count} 个文字块`; }
  function updateCoverage(value) {
    if (!value?.strategy) return;
    if(value.strategy!=='full_document'||value.complete!==true)throw new Error('后端尚未提供完整全文文本，请更新服务。');
    coverage.textContent=`全文文本 · ${value.document_pages || value.pages?.length || 0} 页`;
    documentChip.title=coverageText(value)+'；包含标题、正文、参考文献等所有可读文字。图片仅提供可提取文字和图注。';
  }
  api.assistantContext(documentId).then(value=>{if(alive){updateCoverage(value);contextReady=true;updateComposer();}}).catch(error=>{if(alive){contextReady=false;coverage.textContent=error.code==='http_404'?'后端缺少全文接口，请更新服务。':error.message;documentChip.title=coverage.textContent;updateComposer();}});
  const conversation=node('div','assistant-conversation'); const log=node('div','assistant-log');
  log.setAttribute('role','log'); log.setAttribute('aria-label','文献对话记录'); log.setAttribute('aria-live','polite');
  const latest=button('↓ 最新回答',()=>{followOutput=true;log.scrollTop=log.scrollHeight;latest.hidden=true;},'button assistant-latest'); latest.hidden=true;
  conversation.append(log,latest); panel.appendChild(conversation);
  function scrollToLatest(force=false) { if (force||followOutput) { log.scrollTop=log.scrollHeight;latest.hidden=true; } }
  log.addEventListener('scroll',()=>{followOutput=log.scrollHeight-log.scrollTop-log.clientHeight<70; latest.hidden=followOutput;});

  const welcome=node('div','assistant-welcome');
  welcome.append(node('div','assistant-welcome-mark','✦'),node('h3',null,'一起读懂这篇文献'),node('p',null,'研究问题、方法与结论，都从全文出发。'));
  const textarea=node('textarea','assistant-question'); textarea.id='assistant-question'; textarea.rows=3; textarea.maxLength=2000;
  textarea.placeholder='向这篇文献提问…'; textarea.setAttribute('aria-label','向 AI 提问');
  const context=node('div','assistant-context assistant-focus'); context.hidden=true;
  const quote=node('span','assistant-focus-text'); const clear=iconButton('移除关注内容','close',clearFocus); context.append(quote,clear);
  for (const prompt of prompts) {
    const suggestion=button(prompt,()=>{clearFocus();intent.value='question';textarea.value=prompt;updateComposer();textarea.focus();},'button assistant-suggestion');
    suggestion.setAttribute('aria-label',prompt); welcome.appendChild(suggestion);
  }
  const composer=node('div','assistant-composer'); const tools=node('div','assistant-composer-tools');
  const intent=node('select'); intent.id='assistant-kind'; intent.setAttribute('aria-label','提问方式');
  for (const [value,label] of [['question','提问'],['term','解释术语'],['formula','解释公式']]) { const option=node('option',null,label); option.value=value; intent.appendChild(option); }
  const modelLabel=node('span','assistant-model-label','连接已配置服务');
  const send=iconButton('发送问题','send',()=>ask(intent.value)); send.id='assistant-send'; send.classList.add('primary');
  const stop=iconButton('停止回答','stop',()=>controller?.abort()); stop.id='assistant-stop'; stop.hidden=true;
  const sendActions=node('div','assistant-send-actions'); sendActions.append(send,stop); tools.append(intent,modelLabel,sendActions);
  composer.append(context,textarea,tools); panel.appendChild(composer);
  panel.appendChild(node('p','assistant-notice','每次发送全文文本 · 使用已配置服务，可能计费'));
  panel.appendChild(settingsDialog);
  api.assistantCapabilities().then(value=>{
    if(!alive)return; effortSupported=value.effort_supported; effort.disabled=!effortSupported||busy;
    effort.value=effortSupported?'low':'auto'; model=value.model||'未配置模型'; modelLabel.textContent=model; modelLabel.title=model;
    effortHint.textContent=effortSupported?'实际支持与耗时由模型决定；显示公开依据摘要，不展示内部思维链。':'此服务未确认支持强度设置，使用模型默认；显示公开依据摘要。';
  }).catch(()=>{if(alive){modelLabel.textContent='模型状态未确认';effortHint.textContent='使用模型默认设置。';}});

  function addSummary(answer,text) {
    const detail=node('details','assistant-summary');detail.append(node('summary',null,'回答依据摘要'),node('p','assistant-summary-text',text));answer.appendChild(detail);return detail;
  }
  function sourceDetails(answer,result) {
    const sources=result.citations?.length?result.citations:result.sources||[];
    if(!sources.length)return;
    const detail=node('details','assistant-sources'); const caption=result.citations?.length?'引用来源':'全文来源';
    detail.appendChild(node('summary',null,`${caption} · ${sources.length}`)); const list=node('div','assistant-source-list'); detail.appendChild(list);
    let displayed=0;
    const more=button('显示更多来源',drawSources,'button assistant-source-more');
    function drawSources() {
      more.remove(); const end=Math.min(displayed+25,sources.length);
      for(;displayed<end;displayed++) { const source=sources[displayed];
        const item=button(`[${source.id}] 第 ${source.page_number} 页 · ${source.text.slice(0,45)}`,()=>onSource(source),'button assistant-source');item.title=source.text;list.appendChild(item);
      }
      if(displayed<sources.length)list.appendChild(more);
    }
    detail.addEventListener('toggle',()=>{if(detail.open&&!displayed)drawSources();}); answer.appendChild(detail);
  }
  function appendAnswer(result) {
    const answer=node('article','assistant-answer'); const head=node('div','assistant-answer-head'); head.appendChild(node('span',null,'✦ 文献助手'));
    const copy=iconButton('复制回答','copy',async()=>{try{await navigator.clipboard.writeText(result.answer);copy.title='已复制';copy.setAttribute('aria-label','已复制');}catch{copy.title='复制失败，请选择文字复制';}}); head.appendChild(copy); answer.appendChild(head);
    const text=node('div','assistant-answer-text assistant-markdown');renderAnswer(text,result.answer,{sources:result.sources||[],onSource}); answer.appendChild(text);
    if(result.summary)addSummary(answer,result.summary);
    for(const warning of result.warnings||[])answer.appendChild(node('p','note bad',warning));
    sourceDetails(answer,result);
    if(result.context_summary?.strategy)answer.appendChild(node('p','assistant-context assistant-coverage',coverageText(result.context_summary)));
    if(result.metrics)answer.appendChild(node('p','assistant-context assistant-metrics',`用时 ${(result.metrics.elapsed_ms/1000).toFixed(1)}s · 请求强度：${effortNames[result.reasoning_effort]||'模型默认'}`));
    log.appendChild(answer);scrollToLatest();
  }
  function clearFocus() { selected='';blockId=null;context.hidden=true;quote.textContent='';updateComposer(); }
  function updateComposer() { send.disabled=busy||!contextReady||!(textarea.value.trim()||selected); textarea.readOnly=busy; intent.disabled=busy;options.button.disabled=busy; }
  function newConversation() { if(busy)return;history.length=0;textarea.value='';intent.value='question';clearFocus();log.replaceChildren(welcome);followOutput=true;latest.hidden=true;textarea.focus(); }
  for(const entry of history) { log.appendChild(node('p','assistant-user',entry.question));appendAnswer(entry.result); }
  if(!history.length)log.appendChild(welcome);
  updateComposer();
  async function ask(kind='question') {
    if(busy||!alive||!contextReady)return;
    const question=textarea.value.trim()||(selected?({question:'请结合全文解释关注内容在文中的含义。',term:'请结合全文解释关注的术语。',formula:'请结合全文解释关注的公式。'}[kind]):'');
    if(!question){textarea.focus();return;}
    welcome.remove();busy=true;followOutput=true;updateComposer();effort.disabled=true;send.hidden=true;stop.hidden=false;stop.disabled=false;
    const requestEffort=effort.value;
    log.appendChild(node('p','assistant-user',question));
    const streaming=node('article','assistant-answer assistant-streaming');
    const progress=node('div','assistant-progress'); progress.setAttribute('role','status');
    const phase=node('span','assistant-phase','正在接入完整文献');const timer=node('span','assistant-elapsed','0.0s');progress.append(phase,timer);streaming.appendChild(progress);
    const publicSummary=addSummary(streaming,'');publicSummary.hidden=true;
    const liveText=node('div','assistant-answer-text assistant-markdown');streaming.appendChild(liveText);log.appendChild(streaming);scrollToLatest(true);
    let answerText='',summaryText='',metadata=null;
    function paint() { renderFrame=0;if(alive){renderAnswer(liveText,answerText,{sources:metadata?.sources||[],onSource});scrollToLatest();} }
    const started=performance.now();const tick=setInterval(()=>{timer.textContent=`${((performance.now()-started)/1000).toFixed(1)}s`;},250);
    controller=new AbortController();
    try {
      // Verify the live server's complete-text contract before any model request.
      updateCoverage(await api.assistantContext(documentId));
      const page=getPage();
      const result=await api.streamAssistant(documentId,{question,kind,selected_text:selected,block_id:blockId,page_index:page?.page.index||0,
        context_scope:'document',scope:scope.value,reasoning_effort:requestEffort,include_summary:true,
        history:history.filter(entry=>!entry.result.incomplete).slice(-4).flatMap(entry=>[{role:'user',content:entry.question},{role:'assistant',content:entry.result.answer.slice(0,6000)}])
      },{signal:controller.signal,onEvent:(kind,value)=>{
        if(!alive)return;
        if(kind==='meta'){metadata=value;updateCoverage(value.context_summary);modelLabel.textContent=value.model;modelLabel.title=value.model;}
        if(kind==='phase')phase.textContent=({connecting:'连接模型',thinking:'模型思考中',summarizing:'整理回答依据',answering:'正在回答'})[value.phase]||'处理中';
        if(kind==='summary'){summaryText+=value.text;publicSummary.hidden=false;publicSummary.querySelector('p').textContent=summaryText;}
        if(kind==='delta'){answerText+=value.text;if(!renderFrame)renderFrame=requestAnimationFrame(paint);}
        scrollToLatest();
      }});
      if(!alive)return;
      if(renderFrame){cancelAnimationFrame(renderFrame);renderFrame=0;}
      history.push({question,result});if(history.length>30)history.shift();streaming.remove();appendAnswer(result);textarea.value='';
    } catch(error) {
      if(alive){
        if(renderFrame){cancelAnimationFrame(renderFrame);renderFrame=0;}paint();
        phase.textContent=error.name==='AbortError'?'已停止':'回答中断';streaming.classList.remove('assistant-streaming');
        const message=error.name==='AbortError'?'已停止接收，已有内容保留；服务可能已处理本次请求。':error.message||'AI 请求失败。';
        streaming.appendChild(node('p','note bad',message));
        if(answerText)history.push({question,result:{...metadata,answer:answerText,summary:summaryText,incomplete:true,warnings:[message]}});
      }
    } finally {clearInterval(tick);controller=null;busy=false;stop.disabled=true;stop.hidden=true;send.hidden=false;effort.disabled=!effortSupported;updateComposer();scrollToLatest();}
  }
  textarea.addEventListener('input',updateComposer);
  textarea.addEventListener('keydown',event=>{if(!event.isComposing&&event.key==='Enter'&&!event.shiftKey){event.preventDefault();ask(intent.value);}});
  function setBlock(block) {
    if(!block||busy||window.getSelection()?.toString().trim())return;
    selected=block.text.slice(0,6000);blockId=block.id;quote.textContent=`关注：${selected.slice(0,160)}`;context.hidden=!selected;updateComposer();
  }
  function captureSelection() {
    if(busy)return;
    const selection=window.getSelection(),text=selection?.toString().trim();if(!text||text.length>6000)return;
    const origin=selection.anchorNode?.nodeType===1?selection.anchorNode:selection.anchorNode?.parentElement;
    if(!origin||origin.closest('#assistant-panel')||!origin.closest('.reader,#overlay-block-dialog'))return;
    selected=text;blockId=origin.closest('[data-block-id]')?.dataset.blockId||null;quote.textContent=`关注：${text.slice(0,160)}`;context.hidden=false;panel.hidden=false;updateComposer();
  }
  document.addEventListener('mouseup',captureSelection);
  return {element:panel,toggle:()=>{panel.hidden=!panel.hidden;if(!panel.hidden)requestAnimationFrame(()=>scrollToLatest(true));},setBlock,
    dispose(){alive=false;controller?.abort();closeActionMenu(panel);if(renderFrame)cancelAnimationFrame(renderFrame);if(settingsDialog.open)settingsDialog.close();document.removeEventListener('mouseup',captureSelection);}};
}
