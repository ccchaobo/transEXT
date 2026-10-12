/* Native Zotero is the bibliographic authority; this UI makes operations explicit. */
const n=(tag,cls,text)=>{const value=document.createElement(tag);if(cls)value.className=cls;if(text!==undefined)value.textContent=text;return value;};

export function openZotero({api,onError,onLocalChange,localDocument=null}){
  const dialog=n('dialog','zotero-dialog');dialog.setAttribute('aria-label','Zotero 文献库');
  const head=n('div','zotero-head');head.appendChild(n('h2',null,'Zotero 文献库'));
  const message=n('p','zotero-message','正在检查本机 Zotero…');
  const toolbar=n('div','zotero-toolbar');
  const library=n('select');library.setAttribute('aria-label','Zotero 目标文献库');
  const search=n('input');search.type='search';search.placeholder='搜索 Zotero 标题、作者、DOI';search.setAttribute('aria-label','搜索 Zotero');
  const list=n('div','zotero-list'),info=n('section','zotero-info');
  const main=n('div','zotero-main');main.append(list,info);
  let status=null,items=[],selected=new Set(),current=null,version=0,total=0,poll=null,timer=null;
  function button(label,fn,kind=''){
    const b=n('button',`button ${kind}`,label);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;try{await fn();}catch(error){message.textContent=error.message;onError(error);}finally{b.disabled=false;}});return b;
  }
  const close=button('关闭',()=>dialog.close());head.appendChild(close);
  const pull=button('从 Zotero 同步',()=>refresh(true),'primary');
  const capture=button('网页 / DOI 抓取',()=>captureDialog());
  const dup=button('查找重复项',()=>duplicates());
  const cloud=button('Zotero 云同步',()=>native('sync'));
  const exportBib=n('a','button','导出 BibTeX');exportBib.href='#';
  const exportRis=n('a','button','导出 RIS');exportRis.href='#';
  toolbar.append(library,pull,capture,dup,cloud,exportBib,exportRis);
  const searchbar=n('div','zotero-searchbar');searchbar.append(search,n('span','library-muted','书目条目可没有 PDF；选中附件后才复制到阅读器。'));
  dialog.append(head,message,toolbar,searchbar,main);
  document.body.appendChild(dialog);dialog.showModal();
  dialog.addEventListener('close',()=>{clearTimeout(poll);clearTimeout(timer);version++;dialog.remove();});
  const lib=()=>Number(library.value || 0);
  function exports(){const keys=selected.size?[...selected]:current?[current.key]:[];
    for(const [anchor,format] of [[exportBib,'bibtex'],[exportRis,'ris']]){anchor.href=keys.length?api.zoteroExportUrl(keys,format,'apa',lib()):'#';anchor.setAttribute('aria-disabled',String(!keys.length));}}
  for(const link of [exportBib,exportRis])link.addEventListener('click',event=>{if(link.getAttribute('aria-disabled')==='true')event.preventDefault();});
  async function refresh(sync=false){
    const id=++version;dialog.setAttribute('aria-busy','true');
    try{
      status=await api.zoteroStatus();if(!dialog.isConnected||id!==version)return;
      if(!status.connected){message.textContent=status.message;list.replaceChildren();return;}
      const previous=library.value;library.replaceChildren();
      for(const entry of status.libraries){const option=n('option',null,entry.name);option.value=entry.id;library.appendChild(option);}
      library.value=status.libraries.some(l=>String(l.id)===previous)?previous:String(status.user_library_id);
      capture.disabled=cloud.disabled=!status.can_write;
      message.textContent=status.can_write?'已连接 Zotero；书目在 Zotero 管理，原件副本、译文和标注保存在本机阅读器。':status.message || 'Zotero 为只读连接；写操作尚未启用。';
      if(sync){const result=await api.zoteroPull(lib());if(!dialog.isConnected||id!==version)return;message.textContent=`已同步本机目录：${result.cached_items} 个记录，更新 ${result.updated_documents} 篇本地文献，${result.conflicts.length} 个冲突需核对。云端同步由 Zotero 负责。`;if(result.conflicts.length)message.textContent+=' 冲突内容未覆盖。';}
      const payload=await api.zoteroItems({library_id:lib(),q:search.value,limit:100});if(!dialog.isConnected||id!==version)return;
      items=payload.items;total=payload.total;current=items.find(i=>i.key===current?.key) || items[0] || null;drawList();drawInfo();exports();
    }finally{if(dialog.isConnected&&id===version)dialog.removeAttribute('aria-busy');}
  }
  library.addEventListener('change',()=>{selected.clear();current=null;refresh(true).catch(onError);});
  search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>refresh().catch(onError),250);});
  function drawList(){
    list.replaceChildren(n('p','library-muted',`${total} 篇书目条目`));
    for(const item of items){const row=n('article','zotero-row');row.classList.toggle('active',current?.key===item.key);
      const check=n('input');check.type='checkbox';check.checked=selected.has(item.key);check.setAttribute('aria-label',`选择 Zotero ${item.bibliography.title}`);check.addEventListener('change',()=>{if(check.checked)selected.add(item.key);else selected.delete(item.key);exports();});
      const text=n('div');text.append(n('strong',null,item.bibliography.title || '未命名条目'),n('span','library-muted',`${item.bibliography.authors.join('；') || '作者待补充'} · ${item.bibliography.year || '年份待补充'} · ${item.attachments.filter(a=>a.pdf).length} 个 PDF`));
      row.append(check,text);row.addEventListener('click',e=>{if(e.target===check)return;current=item;drawList();drawInfo();exports();});list.appendChild(row);
    }
    if(!items.length)list.appendChild(n('p','library-empty','没有匹配条目。可调整搜索，或在 Zotero/Connector 添加文献后同步。'));
    if(items.length<total)list.appendChild(button('加载更多',async()=>{const p=await api.zoteroItems({library_id:lib(),q:search.value,offset:items.length,limit:100});items.push(...p.items);drawList();}));
  }
  function drawInfo(){
    info.replaceChildren();if(!current)return;
    const meta=current.bibliography;info.append(n('h3',null,meta.title),n('p','library-muted',`${meta.authors.join('；')}\n${meta.year || ''} · ${meta.publication}\nDOI：${meta.doi || '—'}`));
    if(meta.abstract)info.appendChild(n('p','zotero-abstract',meta.abstract));
    const actions=n('div','zotero-item-actions');
    const locate=n('a','button','在 Zotero 中定位');locate.href=current.select_url;actions.appendChild(locate);
    const cite=button('在 Word 中引用',()=>native('word',current.key),'primary');cite.disabled=!status.can_write;actions.appendChild(cite);
    const refs=n('a','button','生成参考文献（APA）');refs.href=api.zoteroExportUrl([current.key],'bib','apa',lib());actions.appendChild(refs);
    if(localDocument){const chosen=current;actions.appendChild(button('关联此书目到本地文献',async()=>{
      if(!confirm(`确认「${localDocument.bibliography.title}」与「${chosen.bibliography.title}」是同一文献？采用所选 Zotero 书目信息，本地原件、笔记、译文和标注保留。`))return;
      await api.zoteroAssociate({document_id:localDocument.id,library_id:lib(),key:chosen.key,expected_fingerprint:chosen.fingerprint,confirm:true});
      await onLocalChange?.();dialog.close();}));}
    info.appendChild(actions);
    info.appendChild(n('h4',null,'PDF 附件与对照阅读'));
    for(const documentId of current.document_ids){const link=n('a','button','打开已导入的对照阅读');link.href=`#/doc/${documentId}`;link.addEventListener('click',()=>dialog.close());info.appendChild(link);}
    for(const attachment of current.attachments.filter(a=>a.pdf))info.appendChild(button(`导入阅读：${attachment.title || 'PDF'}`,async()=>{message.textContent='正在复制选中的 PDF，Zotero 原件会保留…';const result=await api.zoteroImportPdf(lib(),attachment.key);message.textContent=result.duplicate?'已复用原有 PDF 记录，书目关联和原件仍保留。':'PDF 已复制到阅读器，解析在后台继续。';await onLocalChange?.();await refresh();}));
    if(!current.attachments.some(a=>a.pdf))info.appendChild(n('p','library-muted','此条目暂无 PDF。可在 Zotero 添加或下载附件，再同步到这里。'));
    info.appendChild(n('p','library-muted','引用使用 Zotero 的原生 Word 插件与 CSL 样式，复制普通文本不会被称为“已插入动态引用”。'));
  }
  async function native(command,key=''){
    const text=command==='word'?`将以「${current?.bibliography.title}」打开当前 Word 文档光标位置的 Zotero 原生引用流程，请先确认目标文档与光标位置。继续？`:'将启动 Zotero 自带云同步，同步范围由你的 Zotero 账户设置决定。继续？';
    if(!confirm(text))return;
    const result=await api.zoteroNative(command,{library_id:lib(),key,confirm:true});message.textContent=result.message || '已启动原生流程，尚未确认完成。';
    if(result.task){const task=result.task;const update=async()=>{if(!dialog.isConnected)return;try{const r=await api.zoteroNative('task',{task});message.textContent=r.message || '原生流程仍在进行，请在 Zotero 或 Word 中确认。';if(r.status==='running')poll=setTimeout(update,1500);}catch(e){message.textContent=e.message;}};poll=setTimeout(update,1500);}
  }
  function subdialog(title){const d=n('dialog','settings-dialog zotero-subdialog');d.appendChild(n('h2',null,title));dialog.appendChild(d);d.addEventListener('close',()=>d.remove());return d;}
  function captureDialog(){
    const d=subdialog('网页 / DOI 抓取');const source=n('input');source.placeholder='公开网页地址或 10.… DOI';source.setAttribute('aria-label','网页地址或 DOI');
    const preview=n('div','zotero-capture-preview');let captured=[];
    const save=button('确认保存到所选 Zotero 文献库',async()=>{if(!captured.length)return;if(!confirm(`将预览的 ${captured.length} 条文献保存到「${library.selectedOptions[0].textContent}」？`))return;await api.zoteroCaptureSave({library_id:lib(),items:captured,confirm:true});d.close();await refresh(true);},'primary');save.disabled=true;
    const find=button('抓取并预览',async()=>{preview.replaceChildren(n('p',null,'正在识别书目信息，尚未保存…'));const result=await api.zoteroCapturePreview(source.value);captured=result.items;preview.replaceChildren(n('p','library-muted',`识别来源：${result.translator}；请核对标题、作者和年份。`));for(const item of captured)preview.append(n('strong',null,item.title),n('p',null,`${(item.creators || []).map(c=>[c.firstName,c.lastName].filter(Boolean).join(' ') || c.name).join('；')} · ${item.date || ''}`));save.disabled=!captured.length;});
    d.append(source,find,preview,save,button('取消',()=>d.close()));d.showModal();source.focus();
  }
  async function duplicates(){
    const result=await api.zoteroDuplicates(lib());const d=subdialog('重复条目候选');d.appendChild(n('p','library-muted',result.message));
    if(!result.groups.length)d.appendChild(n('p',null,'未找到 DOI 相同或标题、年份、首位作者一致的候选项。'));
    for(const group of result.groups){const block=n('section','zotero-duplicate');const choice=n('select');choice.setAttribute('aria-label','保留的主条目');for(const item of group.items){const option=n('option',null,`${item.title} · ${item.key}`);option.value=item.key;choice.appendChild(option);}block.appendChild(choice);for(const item of group.items)block.appendChild(n('p',null,`${item.title} · ${item.key}`));const merge=button('确认主条目并合并',async()=>{if(!confirm('将所列候选合并到选定的主条目？请核对它们确为同一文献。Zotero 将执行原生合并。'))return;const keys=group.items.map(i=>i.key);await api.zoteroMerge({library_id:lib(),master_key:choice.value,other_keys:keys.filter(k=>k!==choice.value),fingerprints:Object.fromEntries(group.items.map(i=>[i.key,i.fingerprint])),confirm:true});block.remove();await refresh(true);});merge.disabled=!status.can_write;block.appendChild(merge);d.appendChild(block);}
    d.appendChild(button('关闭',()=>d.close()));d.showModal();
  }
  refresh(true).catch(error=>{message.textContent=error.message;onError(error);});
  return dialog;
}

export async function openZoteroWrite({api,item,onError,onLocalChange}){
  const status=await api.zoteroStatus();
  if(!status.can_write)throw new Error(status.message || 'Zotero 写入连接尚未启用，请启动 Zotero 并检查连接组件。');
  const snapshot=await api.zoteroBinding(item.id);
  const dialog=n('dialog','settings-dialog zotero-subdialog');dialog.setAttribute('aria-label','Zotero 关联与写回');
  dialog.append(n('h2',null,'Zotero 关联与写回'),n('p',null,item.bibliography.title));
  const library=n('select');library.setAttribute('aria-label','写入目标文献库');
  for(const entry of status.libraries.filter(l=>l.editable)){const option=n('option',null,entry.name);option.value=entry.id;library.appendChild(option);}
  library.value=String(snapshot.binding?.library_id || status.user_library_id);library.disabled=!!snapshot.binding;
  const message=n('p','zotero-message',snapshot.conflict?'两边均有修改，请比较后明确选择采用哪一版。':'书目写回只更新改过的字段；笔记、阅读状态和译文保存在阅读器。');
  const fields=['title','authors','year','publication','doi','url','abstract'];
  if(snapshot.remote){
    const table=n('table','zotero-compare');const head=n('tr');for(const label of ['字段','本地信息','Zotero 信息'])head.appendChild(n('th',null,label));table.appendChild(head);
    const names={title:'标题',authors:'作者',year:'年份',publication:'出版来源',doi:'DOI',url:'链接',abstract:'摘要'};
    for(const key of fields){if(JSON.stringify(snapshot.local[key])===JSON.stringify(snapshot.remote[key]))continue;const row=n('tr');for(const text of [names[key],snapshot.local[key],snapshot.remote[key]])row.appendChild(n('td',null,Array.isArray(text)?text.join('；'):String(text ?? '')));table.appendChild(row);}
    dialog.appendChild(table);
  }
  const label=n('label');const pdf=n('input');pdf.type='checkbox';pdf.disabled=item.format!=='pdf';label.append(pdf,document.createTextNode(item.format==='pdf'?'同时复制 PDF 到 Zotero（已有相同附件会复用）':'此原件不是 PDF，仅写入书目信息'));
  const actions=n('div','zotero-item-actions');
  const action=(name,fn)=>{const b=n('button','button',name);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;try{await fn();}catch(e){message.textContent=e.message;onError(e);}finally{b.disabled=false;}});actions.appendChild(b);return b;};
  if(!snapshot.binding)action('关联 Zotero 中已有文献',()=>{dialog.close();openZotero({api,onError,onLocalChange,localDocument:item});});
  if(snapshot.binding&&!snapshot.deleted)action('采用 Zotero 版本',async()=>{
    if(!confirm('采用展示的 Zotero 书目信息？本地笔记、译文与标注保留。'))return;
    await api.zoteroResolve({document_id:item.id,expected_fingerprint:snapshot.expected_fingerprint,confirm:true});await onLocalChange();dialog.close();});
  const save=action(snapshot.binding?'将本地修改写回 Zotero':'新增 Zotero 书目',async()=>{
    const target=library.selectedOptions[0];if(!target)throw new Error('当前没有可写的目标文献库');
    if(!confirm(`确认把「${item.bibliography.title}」${snapshot.binding?'的本地修改写回':'新增到'} Zotero「${target.textContent}」${pdf.checked?'，并复制 PDF 附件':''}？`))return;
    await api.zoteroPush({document_id:item.id,library_id:Number(library.value),confirm:true,include_pdf:pdf.checked,expected_fingerprint:snapshot.expected_fingerprint});
    await onLocalChange();dialog.close();});
  save.disabled=!!snapshot.deleted;
  if(snapshot.deleted)message.textContent='关联条目已在 Zotero 删除，未写回。请先在 Zotero 核对或恢复条目。';
  action('关闭',()=>dialog.close());dialog.append(library,message,label,actions);document.body.appendChild(dialog);
  dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
}
