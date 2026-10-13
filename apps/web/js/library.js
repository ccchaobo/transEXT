/* A file-backed research library: organisation never moves original files. */
import { openZotero, openZoteroWrite } from './zotero.js';
import { icon, createActionMenu, closeActionMenu, confirmAction } from './ui.js';
const READ_LABELS={to_read:'未读',reading:'阅读中',read:'已读'};
const TYPES={article:'期刊论文',book:'书籍',thesis:'学位论文',report:'报告',other:'其他'};
const STAGES={pending:'待处理',running:'处理中',succeeded:'完成',partially_succeeded:'部分完成',failed:'失败',cancelled:'已取消',skipped:'跳过'};
const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const option=(value,label)=>{const n=node('option',null,label);n.value=value;return n;};

export function createLibrary({container,api,onError,onImport,isCurrent=()=>true}) {
  const state={view:'all',collection:'',tag:'',q:'',sort:'added_desc',items:[],overview:null,
    total:0,selected:new Set(),active:null,dirty:false,request:0,disposed:false,lastIndex:0};
  let searchTimer=null,refreshTimer=null,form=null;
  const root=node('div','library-manager');
  const sidebar=node('aside','library-nav');sidebar.setAttribute('aria-label','文献分类与筛选');
  const center=node('section','library-center');
  const details=node('aside','library-details');details.setAttribute('aria-label','文献详情');
  root.append(sidebar,center,details);container.replaceChildren(root);
  const toolbar=node('div','library-toolbar');
  const heading=node('h1',null,'我的文献');
  const subtitle=node('span','library-total');toolbar.append(heading,subtitle);
  const add=button('导入文献',()=>{if(leave())onImport();},'primary');add.id='library-import';toolbar.appendChild(add);
  toolbar.appendChild(button('Zotero 文献库',()=>{if(leave())openZotero({api,onError,onLocalChange:()=>load()});}));
  const filters=node('div','library-filters');
  const search=node('input','library-search');search.type='search';search.placeholder='搜索标题、作者、来源、DOI、标签';search.setAttribute('aria-label','搜索文献');
  const sort=node('select');sort.setAttribute('aria-label','文献排序');
  for(const [value,label] of [['added_desc','最近添加'],['title','标题'],['year_desc','年份降序']])sort.appendChild(option(value,label));
  const tagIndicator=button('清除标签筛选',()=>navigate({tag:''}));tagIndicator.hidden=true;
  filters.append(search,sort,tagIndicator);
  const batchbar=node('div','library-batch');
  const tableWrap=node('div','library-table-wrap');
  const table=node('table','library-table');
  const thead=node('thead');const header=node('tr');
  const allCell=node('th');const all=node('input');all.type='checkbox';all.setAttribute('aria-label','选择当前已加载文献');
  allCell.appendChild(all);header.appendChild(allCell);
  for(const label of ['收藏','标题 / 文件','作者','年份','阅读状态','操作'])header.appendChild(node('th',null,label));
  thead.appendChild(header);const tbody=node('tbody');table.append(thead,tbody);tableWrap.appendChild(table);
  const empty=node('div','library-empty');empty.hidden=true;tableWrap.appendChild(empty);
  const more=button('加载更多',()=>load(true));more.id='library-more';
  const bottom=node('div','library-bottom');bottom.appendChild(more);
  center.append(toolbar,filters,batchbar,tableWrap,bottom);
  all.addEventListener('change',()=>{for(const item of state.items){if(all.checked)state.selected.add(item.id);else state.selected.delete(item.id);}drawRows();});
  search.addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{if(leave()){state.q=search.value;load();}},250);});
  sort.addEventListener('change',()=>{if(leave()){state.sort=sort.value;load();}});

  function button(text,action,kind='') {
    const b=node('button',`button ${kind}`,text);b.type='button';
    b.addEventListener('click',async()=>{b.disabled=true;try{await action();}catch(error){onError(error);}finally{b.disabled=false;}});return b;
  }
  function live(){return !state.disposed && isCurrent();}
  function leave(){if(state.dirty && !confirm('文献信息尚未保存，是否放弃本次修改？'))return false;state.dirty=false;return true;}
  async function navigate(changes){if(!leave())return;Object.assign(state,changes);state.selected.clear();state.active=null;await load();}
  function nav(label,count,changes,selected){
    const b=button('',()=>navigate(changes));b.className=`library-nav-item${selected?' active':''}`;
    b.appendChild(icon(changes.collection ? 'folder' : ({all:'library',unfiled:'folder',starred:'star',to_read:'clock',reading:'book',read:'check',trash:'trash'}[changes.view])));
    if(selected)b.setAttribute('aria-current','page');
    b.append(node('span',null,label),node('span','library-count',String(count ?? 0)));return b;
  }
  function drawSidebar(){
    const overview=state.overview;if(!overview)return;
    closeActionMenu(root);
    sidebar.replaceChildren(node('h2',null,'文献库'));
    for(const [view,label] of [['all','全部文献'],['unfiled','未分类'],['starred','我的收藏'],['to_read','未读'],['reading','阅读中'],['read','已读'],['trash','回收站']])
      sidebar.appendChild(nav(label,overview.counts[view],{view,collection:'',tag:''},state.view===view&&!state.collection));
    const title=node('div','library-nav-heading');const create=button('＋',()=>collectionDialog());create.setAttribute('aria-label','新建分类');create.title='新建分类';title.append(node('h2',null,'分类'),create);sidebar.appendChild(title);
    for(const c of overview.collections){
      const row=node('div',`library-collection-row${state.collection===c.id?' active':''}`);row.dataset.collectionId=c.id;
      row.appendChild(nav(c.name,c.count,{view:'all',collection:c.id,tag:''},state.collection===c.id));
      const menu=createActionMenu({label:`分类「${c.name}」的更多操作`,onError,items:[
        {label:'重命名',icon:'edit',onSelect:()=>{if(leave())collectionDialog(c);}},
        {separator:true},
        {label:'删除分类',icon:'trash',danger:true,onSelect:async()=>{
          if(!leave()||!await confirmAction({title:'删除分类',message:`删除「${c.name}」后，文献、译文和标注会保留，只移除分类关系。`,confirmLabel:'删除分类'}))return;
          await api.deleteCollection(c.id);
          if(state.collection===c.id)await navigate({view:'all',collection:''});else await load();
        }},
      ]});row.appendChild(menu.button);menu.bindContext(row);sidebar.appendChild(row);
    }
    sidebar.appendChild(node('h2',null,'标签'));
    const tags=node('div','library-tags');
    for(const tag of overview.tags){const b=button(`${tag.name} · ${tag.count}`,()=>navigate({tag:state.tag===tag.name?'':tag.name}));b.classList.toggle('active',state.tag===tag.name);tags.appendChild(b);}
    if(!overview.tags.length)tags.appendChild(node('p','library-muted','在详情中添加标签后，可在这里筛选。'));
    sidebar.appendChild(tags);
  }

  async function load(append=false){
    clearTimeout(refreshTimer);
    const version=++state.request;
    const offset=append?state.items.length:0;
    more.disabled=true;root.setAttribute('aria-busy','true');
    try{
      const params={view:state.view,sort:state.sort,offset,limit:100};
      if(state.q)params.q=state.q;if(state.collection)params.collection_id=state.collection;if(state.tag)params.tag=state.tag;
      const [overview,payload]=await Promise.all([api.libraryOverview(),api.libraryItems(params)]);
      if(!live()||version!==state.request)return;
      state.overview=overview;state.total=payload.total;state.items=append?[...state.items,...payload.items]:payload.items;
      const visible=new Set(state.items.map(item=>item.id));
      for(const id of state.selected)if(!visible.has(id))state.selected.delete(id);
      if(!visible.has(state.active))state.active=state.items[0]?.id || null;
      const c=overview.collections.find(c=>c.id===state.collection);
      heading.textContent=c?c.name:({trash:'回收站',unfiled:'未分类',starred:'我的收藏',to_read:'未读文献',reading:'阅读中',read:'已读文献'}[state.view] || '我的文献');
      subtitle.textContent=`${state.total} 篇`;
      tagIndicator.hidden=!state.tag;tagIndicator.textContent=`标签：${state.tag} ×`;
      drawSidebar();drawRows();if(!state.dirty)drawDetails();
      // Pending parsing becomes visible without clearing an in-progress edit.
      if(state.items.some(item=>['pending','running'].includes(item.parse_status)))refreshTimer=setTimeout(()=>{if(live()&&!state.dirty)load();},2500);
    }catch(error){if(live()&&version===state.request){onError(error);empty.hidden=state.items.length>0;empty.textContent='加载失败，请点击重试。';empty.appendChild(button('重试',()=>load()));}}
    finally{if(live()&&version===state.request){more.disabled=false;root.removeAttribute('aria-busy');}}
  }

  function drawRows(){
    tbody.replaceChildren();
    const ids=state.items.map(item=>item.id);all.checked=ids.length>0&&ids.every(id=>state.selected.has(id));all.indeterminate=!all.checked&&ids.some(id=>state.selected.has(id));
    for(const [index,item] of state.items.entries()){
      const tr=node('tr');tr.dataset.id=item.id;tr.classList.toggle('active',state.active===item.id);tr.classList.toggle('selected',state.selected.has(item.id));
      tr.addEventListener('click',event=>{if(event.target.closest('button,input,a'))return;if(state.active!==item.id&&!leave())return;state.active=item.id;drawRows();drawDetails();});
      tr.addEventListener('dblclick',event=>{if(!item.deleted_at&&!event.target.closest('button,input')&&leave())location.hash=`#/doc/${item.id}`;});
      const checkcell=node('td');const check=node('input');check.type='checkbox';check.checked=state.selected.has(item.id);check.setAttribute('aria-label',`选择 ${item.bibliography.title}`);
      check.addEventListener('click',event=>{if(event.shiftKey){for(let n=Math.min(index,state.lastIndex);n<=Math.max(index,state.lastIndex);n++){if(check.checked)state.selected.add(ids[n]);else state.selected.delete(ids[n]);}}else{if(check.checked)state.selected.add(item.id);else state.selected.delete(item.id);}state.lastIndex=index;drawRows();});checkcell.appendChild(check);tr.appendChild(checkcell);
      const starcell=node('td');const star=button(item.bibliography.starred?'★':'☆',()=>perform(item.bibliography.starred?'unstar':'star',[item.id]));star.className='library-star';star.title=item.bibliography.starred?'取消收藏':'收藏';star.disabled=!!item.deleted_at;star.setAttribute('aria-label',star.title);starcell.appendChild(star);tr.appendChild(starcell);
      const titlecell=node('td','library-title-cell');const copy=node('div','library-title-copy');copy.append(node('strong',null,item.bibliography.title || item.filename),node('span','library-file',item.filename));const titlewrap=node('div','library-title-wrap');titlewrap.append(icon('file'),copy);titlecell.appendChild(titlewrap);titlecell.title=`${item.bibliography.title}\n${item.filename}`;tr.appendChild(titlecell);
      const authors=node('td',null,item.bibliography.authors.join('；') || '—');authors.title=item.bibliography.authors.join('；');tr.appendChild(authors);
      const readcell=node('td');readcell.appendChild(node('span',`read-status read-status-${item.bibliography.read_status}`,READ_LABELS[item.bibliography.read_status]));
      tr.append(node('td',null,String(item.bibliography.year || '—')),readcell);
      const actioncell=node('td','library-row-actions');const menu=createActionMenu({label:`文献「${item.bibliography.title || item.filename}」的更多操作`,onError,items:()=>itemActions(item)});
      actioncell.appendChild(menu.button);tr.appendChild(actioncell);menu.bindContext(tr);tbody.appendChild(tr);
    }
    empty.hidden=state.items.length>0;empty.textContent=state.view==='trash'?'回收站为空。移入回收站的文献可在这里恢复。':state.q||state.collection||state.tag?'没有匹配的文献。可调整筛选或导入新文献。':'文献库为空。点击“导入文献”添加 PDF，支持多选和拖放。';
    more.hidden=state.items.length>=state.total;
    drawBatchbar();
  }

  function itemActions(item){
    if(item.deleted_at)return [
      {label:'恢复文献',icon:'arrow',onSelect:()=>perform('restore',[item.id])},
      {separator:true},
      {label:'永久删除',icon:'trash',danger:true,onSelect:()=>perform('permanent_delete',[item.id])},
    ];
    return [
      {label:'打开阅读',icon:'book',onSelect:()=>{if(leave())location.hash=`#/doc/${item.id}`;}},
      {label:'打开原件',icon:'file',href:item.original_url,newTab:true},
      {separator:true},
      {label:item.bibliography.starred?'取消收藏':'收藏',icon:'star',onSelect:()=>perform(item.bibliography.starred?'unstar':'star',[item.id])},
      {label:'加入分类',icon:'folder',onSelect:()=>{if(leave())chooseCollection([item.id]);}},
      {label:'添加标签',icon:'tag',onSelect:()=>{if(leave())tagsDialog([item.id]);}},
      {label:'标为已读',icon:'check',onSelect:()=>perform('mark_read',[item.id])},
      ...(state.collection?[{label:'从此分类移除',icon:'folder',onSelect:()=>perform('remove_collection',[item.id],{collection_id:state.collection})}]:[]),
      {separator:true},
      {label:'移入回收站',icon:'trash',danger:true,onSelect:()=>perform('trash',[item.id])},
    ];
  }

  function drawBatchbar(){
    batchbar.replaceChildren(node('span',null,state.selected.size?`已选 ${state.selected.size} 篇`:'勾选文献可批量整理；双击标题进入阅读'));
    if(!state.selected.size)return;
    const ids=Array.from(state.selected);
    if(state.view==='trash')batchbar.append(button('恢复',()=>perform('restore',ids)),button('永久删除',()=>perform('permanent_delete',ids),'danger'));
    else{
      batchbar.append(button('加入分类',()=>chooseCollection(ids)),button('添加标签',()=>tagsDialog(ids)),button('标为已读',()=>perform('mark_read',ids)),button('收藏',()=>perform('star',ids)),button('移入回收站',()=>perform('trash',ids),'danger'));
      if(state.collection)batchbar.appendChild(button('从此分类移除',()=>perform('remove_collection',ids,{collection_id:state.collection})));
    }
  }
  async function perform(action,ids,extra={}){
    if(!leave())return;
    if(action==='permanent_delete'&&!await confirmAction({title:'永久删除文献',message:`永久删除所选 ${ids.length} 篇文献？原件、译文和标注将被删除，无法恢复。`,confirmLabel:'永久删除'}))return;
    root.dataset.mutating='true';
    try{
      for(let start=0;start<ids.length;start+=200)
        await api.libraryBatch({ids:ids.slice(start,start+200),action,...extra,confirm:action==='permanent_delete'});
      state.selected.clear();await load();
    }finally{delete root.dataset.mutating;}
  }

  function drawDetails(){
    details.replaceChildren();form=null;
    const item=state.items.find(item=>item.id===state.active);
    if(!item){details.appendChild(node('p','library-detail-empty','选择一篇文献，查看信息、标签、分类和笔记。'));return;}
    const meta=item.bibliography;
    const header=node('div','library-detail-head');header.append(node('h2',null,'文献详情'));
    header.appendChild(button('收起',()=>{root.classList.add('details-collapsed');}));details.appendChild(header);
    const reopen=document.getElementById('library-show-details');if(reopen)reopen.remove();
    const show=button('显示详情',()=>root.classList.remove('details-collapsed'));show.id='library-show-details';toolbar.appendChild(show);
    const actions=node('div','library-item-actions');
    if(item.deleted_at){actions.append(button('恢复文献',()=>perform('restore',[item.id]),'primary'),button('永久删除',()=>perform('permanent_delete',[item.id]),'danger'));
      details.append(actions,node('p','library-muted','此文献在回收站中。原件、译文、标注与分类均保留，恢复后可继续阅读。'));
    }else{
      actions.append(button('打开阅读',()=>{if(leave())location.hash=`#/doc/${item.id}`;},'primary'));
      const original=node('a','button','打开原件');original.href=item.original_url;original.target='_blank';original.rel='noopener noreferrer';actions.appendChild(original);
      actions.appendChild(button('移入回收站',()=>perform('trash',[item.id]),'danger'));details.appendChild(actions);
      details.appendChild(button('Zotero 关联与写回',async()=>{if(leave())await openZoteroWrite({api,item,onError,onLocalChange:()=>load()});}));
    }
    const fileinfo=node('p','library-file-info',`${item.filename}\n${item.format.toUpperCase()} · ${item.page_count ?? '—'} 页 · ${(item.size_bytes/1024).toFixed(0)} KB\n解析：${STAGES[item.parse_status] || item.parse_status} · 翻译：${STAGES[item.translate_status] || item.translate_status}`);details.appendChild(fileinfo);
    form=node('form','library-form');details.appendChild(form);
    const inputs={};
    function field(key,label,value,type='text'){
      const wrap=node('label','library-field');wrap.appendChild(node('span',null,label));
      const input=node(type==='textarea'?'textarea':'input');input.name=key;input.value=value??'';
      if(type==='textarea')input.rows=key==='notes'?5:2;else input.type=type;
      input.disabled=!!item.deleted_at;wrap.appendChild(input);form.appendChild(wrap);inputs[key]=input;return input;
    }
    field('title','标题',meta.title);
    inputs.title.required=true;inputs.title.maxLength=512;
    field('authors','作者（每行一位）',meta.authors.join('\n'),'textarea');
    const year=field('year','年份',meta.year,'number');year.min='1';year.max='9999';
    field('publication','期刊 / 来源',meta.publication);
    field('doi','DOI',meta.doi);
    const url=field('url','来源链接',meta.url,'url');url.placeholder='https://…';
    const typeWrap=node('label','library-field');typeWrap.appendChild(node('span',null,'文献类型'));const type=node('select');type.name='item_type';type.disabled=!!item.deleted_at;
    for(const [key,label] of Object.entries(TYPES))type.appendChild(option(key,label));type.value=meta.item_type;typeWrap.appendChild(type);form.appendChild(typeWrap);inputs.item_type=type;
    const statusWrap=node('label','library-field');statusWrap.appendChild(node('span',null,'阅读状态'));const status=node('select');status.name='read_status';status.disabled=!!item.deleted_at;
    for(const [key,label] of Object.entries(READ_LABELS))status.appendChild(option(key,label));status.value=meta.read_status;statusWrap.appendChild(status);form.appendChild(statusWrap);inputs.read_status=status;
    field('tags','标签（逗号分隔）',item.labels.join('，'));
    const collectionTitle=node('fieldset','library-memberships');collectionTitle.appendChild(node('legend',null,'所属分类（可多选）'));
    const memberships=[];
    for(const c of state.overview.collections){const label=node('label');const check=node('input');check.type='checkbox';check.value=c.id;check.checked=item.collection_ids.includes(c.id);check.disabled=!!item.deleted_at;label.append(check,node('span',null,c.name));collectionTitle.appendChild(label);memberships.push(check);}
    if(!memberships.length)collectionTitle.appendChild(node('span','library-muted','先在左侧创建分类。'));form.appendChild(collectionTitle);
    field('abstract','摘要',meta.abstract,'textarea');field('notes','笔记',meta.notes,'textarea');
    const save=node('button','button primary','保存信息');save.type='submit';save.id='library-save';save.disabled=true;
    const message=node('span','library-save-message');const savebar=node('div','library-savebar');savebar.append(save,message);form.appendChild(savebar);
    form.addEventListener('input',()=>{state.dirty=true;save.disabled=false;message.textContent='未保存';});
    form.addEventListener('submit',async event=>{
      event.preventDefault();save.disabled=true;
      try{
        const bibliography={...meta};
        for(const key of ['title','publication','doi','url','abstract','notes','item_type','read_status'])bibliography[key]=inputs[key].value.trim();
        bibliography.authors=inputs.authors.value.split(/[\n;；]/).map(s=>s.trim()).filter(Boolean);
        bibliography.year=inputs.year.value?Number(inputs.year.value):null;
        const tags=inputs.tags.value.split(/[,，;；]/).map(s=>s.trim()).filter(Boolean);
        await api.editLibraryItem(item.id,{bibliography,tags,collection_ids:memberships.filter(c=>c.checked).map(c=>c.value)});
        state.dirty=false;await load();const saved=details.querySelector('.library-save-message');if(saved)saved.textContent='已保存';
      }catch(error){onError(error);message.textContent='保存失败，修改仍保留';save.disabled=false;}
    });
  }

  function dialog(title){
    const d=node('dialog','settings-dialog library-dialog');d.appendChild(node('h2',null,title));
    const f=node('form');f.method='dialog';d.appendChild(f);root.appendChild(d);
    d.addEventListener('close',()=>d.remove());return {d,f};
  }
  function collectionDialog(c=null,ids=[]){
    const {d,f}=dialog(c?'重命名分类':'新建分类');const input=node('input');input.name='name';input.required=true;input.maxLength=100;input.placeholder='例如：脑影像研究';input.value=c?.name || '';input.setAttribute('aria-label','分类名称');
    const save=button('保存',async()=>{if(!input.reportValidity()||!input.value.trim())return;const result=c?await api.renameCollection(c.id,input.value.trim()):await api.createCollection(input.value.trim());if(!c&&ids.length)await api.libraryBatch({ids,action:'add_collection',collection_id:result.id});d.close();if(!c){state.collection=result.id;state.view='all';state.tag='';}await load();},'primary');f.append(input,save,button('取消',()=>d.close()));f.addEventListener('submit',e=>{e.preventDefault();save.click();});d.showModal();input.focus();
  }
  function chooseCollection(ids){
    if(!state.overview.collections.length){collectionDialog(null,ids);return;}
    const {d,f}=dialog('加入分类');const select=node('select');select.setAttribute('aria-label','目标分类');
    for(const c of state.overview.collections)select.appendChild(option(c.id,c.name));f.append(select,button('确定',async()=>{await perform('add_collection',ids,{collection_id:select.value});d.close();},'primary'),button('取消',()=>d.close()));d.showModal();
  }
  function tagsDialog(ids){
    const {d,f}=dialog('批量添加标签');const input=node('input');input.placeholder='标签之间用逗号分隔';input.setAttribute('aria-label','批量标签');
    f.append(input,button('确定',async()=>{const tags=input.value.split(/[,，;；]/).map(s=>s.trim()).filter(Boolean);if(!tags.length)return;await perform('add_tags',ids,{tags});d.close();},'primary'),button('取消',()=>d.close()));d.showModal();input.focus();
  }

  root.addEventListener('dragover',event=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();root.classList.add('is-drop-target');}});
  root.addEventListener('dragleave',event=>{if(!root.contains(event.relatedTarget))root.classList.remove('is-drop-target');});
  root.addEventListener('drop',event=>{event.preventDefault();root.classList.remove('is-drop-target');if(!leave())return;const input=document.getElementById('file-input');if(event.dataTransfer?.files.length){input.files=event.dataTransfer.files;input.dispatchEvent(new Event('change'));}});
  return {load,activeCollection:()=>state.collection || null,dispose(){state.disposed=true;closeActionMenu(root);clearTimeout(searchTimer);clearTimeout(refreshTimer);root.querySelectorAll('dialog').forEach(d=>d.remove());},state};
}
