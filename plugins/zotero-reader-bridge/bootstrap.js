/* Zotero 9 bridge. All changes use the public Zotero JS API, never its SQLite file.
   The companion installer pairs a secret with the same-machine reader backend.
   No token is sent to browser JS, and no endpoint binds a new public server. */
const RR_PREFIX='/research-reader';
const RR_ROUTES=[];
const RR_TASKS=new Map();

function canonical(value){
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));
  return value;
}
function digest(value){
  const bytes=new TextEncoder().encode(JSON.stringify(canonical(value)));
  const hash=Cc['@mozilla.org/security/hash;1'].createInstance(Ci.nsICryptoHash);
  hash.init(hash.SHA256);hash.update(bytes,bytes.length);
  return [...hash.finish(false)].map(char=>char.charCodeAt(0).toString(16).padStart(2,'0')).join('');
}
function instance(){return Zotero.Users.getLocalUserKey();}
function row(item){
  const data=item.toJSON();
  return {key:item.key,library_id:item.libraryID,data,fingerprint:digest(data),deleted:item.deleted,
    is_attachment:item.isAttachment(),parent_key:item.parentItemKey || null};
}
function lib(id){
  const library=Zotero.Libraries.get(Number(id || Zotero.Libraries.userLibraryID));
  if(!library)throw new Error('文献库不存在');return library;
}
function selectedItem(id,key){
  if(!/^[A-Z0-9]{8}$/.test(key || ''))throw new Error('文献标识无效');
  const item=Zotero.Items.getByLibraryAndKey(lib(id).libraryID,key);
  if(!item)throw new Error('文献不存在');return item;
}
function register(path,fn,publicStatus=false){
  const url=RR_PREFIX+path;
  const Endpoint=function(){};
  Endpoint.prototype={supportedMethods:['POST'],supportedDataTypes:['application/json'],permitBookmarklet:false,
    async init(request){
      try{
        const headers=request.headers || {};
        const token=Object.entries(headers).find(([key])=>key.toLowerCase()==='x-research-reader-key')?.[1];
        const expected=Zotero.Prefs.get('researchReader.token');
        if(!publicStatus&&(!expected||token!==expected))return [403,'application/json',JSON.stringify({error:'连接授权不匹配',code:'PAIRING_REQUIRED'})];
        const result=await fn(request.data || {});
        if(publicStatus)result.authorized=!!expected&&token===expected;
        return [200,'application/json',JSON.stringify(result)];
      }catch(error){
        Zotero.logError(error);
        return [error.rrConflict?409:400,'application/json',JSON.stringify({error:String(error.message || error).slice(0,500),code:error.rrConflict?'CONFLICT':'ZOTERO_ERROR'})];
      }
    }};
  Zotero.Server.Endpoints[url]=Endpoint;RR_ROUTES.push(url);
}
function conflict(){const e=new Error('文献信息已变化，请重新同步并核对，未覆盖任何内容。');e.rrConflict=true;throw e;}
async function allItems(id){return Zotero.Items.getAll(lib(id).libraryID,false,true);}

async function startup(){
  register('/status',async()=>({bridge_version:'0.1.1',zotero_version:Zotero.version,instance:instance(),
    libraries:Zotero.Libraries.getAll().filter(l=>l.libraryType!=='feed').map(l=>({id:l.libraryID,name:l.name,type:l.libraryType,editable:l.editable,group_id:l.groupID || null})),
    user_library_id:Zotero.Libraries.userLibraryID,cloud_configured:!!Zotero.Users.getCurrentUserID(),
    paired:!!Zotero.Prefs.get('researchReader.token')}),true);
  register('/inventory',async data=>{
    const library=lib(data.library_id);
    const items=await allItems(library.libraryID);
    const collections=await Zotero.Collections.getByLibrary(library.libraryID);
    return {instance:instance(),library_id:library.libraryID,items:items.map(row),
      collections:collections.map(c=>({key:c.key,name:c.name,parent_key:c.parentKey || null}))};
  });
  register('/file',async data=>{
    const item=selectedItem(data.library_id,data.key);
    if(!item.isAttachment()||item.attachmentContentType!=='application/pdf')throw new Error('请选择 PDF 附件');
    const path=await item.getFilePathAsync();
    if(!path||!await IOUtils.exists(path))throw new Error('PDF 附件尚未下载到本机，请先在 Zotero 打开附件');
    return {path,item:row(item)};
  });
  register('/update',async data=>{
    const library=lib(data.library_id);
    if(!library.editable)throw new Error('此文献库不可编辑');
    const item=selectedItem(library.libraryID,data.key);
    if(item.deleted)throw new Error('此文献已在 Zotero 回收站中');
    if(!data.expected_fingerprint||digest(item.toJSON())!==data.expected_fingerprint)conflict();
    const allowed=['title','date','DOI','url','publicationTitle','publisher','university','institution','abstractNote','creators'];
    const next=item.toJSON();
    for(const key of allowed)if(Object.hasOwn(data.fields || {},key))next[key]=data.fields[key];
    await Zotero.DB.executeTransaction(async()=>{await item.fromJSON(next);await item.save();});
    return row(item);
  });
  register('/save',async data=>{
    const library=lib(data.library_id);
    if(!library.editable)throw new Error('此文献库不可编辑');
    const outputs=[];
    const existing=(await allItems(library.libraryID)).filter(i=>!i.deleted&&!i.isAttachment()&&!i.isNote());
    const normalizeDOI=value=>String(value || '').replace(/^(https?:\/\/(dx\.)?doi\.org\/|doi\s*:\s*)/i,'').trim().toLowerCase();
    const operation=data.operation_id?'urn:research-reader:'+digest([library.libraryID,data.operation_id]):null;
    await Zotero.DB.executeTransaction(async()=>{
      for(const value of (data.items || []).slice(0,50)){
        if(operation){
          const replay=existing.find(i=>[].concat(i.getRelations()['zotero:researchReaderImport'] || []).includes(operation));
          if(replay){outputs.push(row(replay));continue;}
        }
        const code=normalizeDOI(value.DOI);
        const duplicate=code&&existing.find(i=>normalizeDOI(i.toJSON().DOI)===code);
        if(duplicate){outputs.push({...row(duplicate),reused:true});continue;}
        const item=new Zotero.Item(value.itemType || 'journalArticle');item.libraryID=library.libraryID;
        const clean={...value};for(const key of ['key','version','attachments','notes','relations','collections','deleted','parentItem'])delete clean[key];
        await item.fromJSON(clean);
        if(operation)item.addRelation('zotero:researchReaderImport',operation);
        await item.save();existing.push(item);outputs.push(row(item));
      }
    });
    return {items:outputs,library_id:library.libraryID};
  });
  register('/attach',async data=>{
    const parent=selectedItem(data.library_id,data.key);
    if(!parent.library.editable||!parent.library.filesEditable)throw new Error('此文献库不可保存附件');
    if(typeof data.path!=='string'||!data.path.toLowerCase().endsWith('.pdf')||!await IOUtils.exists(data.path))throw new Error('PDF 文件不存在');
    const md5=await Zotero.Utilities.Internal.md5Async(data.path);
    for(const id of parent.getAttachments()){
      const item=Zotero.Items.get(id),path=await item.getFilePathAsync();
      if(item.attachmentContentType==='application/pdf'&&path&&await Zotero.Utilities.Internal.md5Async(path)===md5)return {...row(item),reused:true};
    }
    const attachment=await Zotero.Attachments.importFromFile({file:data.path,parentItemID:parent.id,libraryID:parent.libraryID});
    return row(attachment);
  });
  register('/capture',async data=>{
    let translate;
    if(data.identifier){
      translate=new Zotero.Translate.Search();translate.setIdentifier(data.identifier);
    }else{
      const url=new URL(data.url);
      if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw new Error('网页地址无效');
      const response=await Zotero.HTTP.request('GET',url.href,{timeout:30000});
      const parsed=new DOMParser().parseFromString(response.responseText,'text/html');
      const document=Zotero.HTTP.wrapDocument(parsed,url.href);
      translate=new Zotero.Translate.Web();translate.setDocument(document);
    }
    const translators=await translate.getTranslators();
    if(!translators.length)throw new Error('此网页未识别到文献，请在浏览器用 Zotero Connector 保存后再同步。');
    translate.setTranslator(translators[0]);
    translate.setHandler('select',(translator,choices,callback)=>callback(choices));
    const items=await translate.translate({libraryID:false,attachments:false});
    return {items:items.slice(0,50),translator:translators[0].label,preview:true};
  });
  register('/merge',async data=>{
    const master=selectedItem(data.library_id,data.master_key);
    const others=(data.other_keys || []).map(key=>selectedItem(data.library_id,key));
    if(!master.library.editable||master.deleted||!others.length)throw new Error('请选择可合并的文献');
    for(const item of [master,...others]){
      if(item.itemTypeID!==master.itemTypeID||item.id===master.id&&others.includes(item))throw new Error('不同类型或同一条目不能合并');
      if(digest(item.toJSON())!==data.fingerprints?.[item.key])conflict();
    }
    await Zotero.Items.merge(master,others);
    return {master:row(master),merged_keys:others.map(item=>item.key)};
  });
  register('/locate',async data=>{
    const item=selectedItem(data.library_id,data.key);const pane=Zotero.getActiveZoteroPane();
    await pane.selectItem(item.id);return {selected:item.key};
  });
  register('/word',async data=>{
    if(!data.confirm)throw new Error('请确认要在当前 Word 文档的光标位置插入引用');
    const item=selectedItem(data.library_id,data.key);
    await Zotero.getActiveZoteroPane().selectItem(item.id);
    let document;
    try{document=await Zotero.Integration.getApplication('WinWord','addEditCitation').getActiveDocument();}
    catch(e){throw new Error('请先打开目标 Word 文档，并把光标放在引用位置。');}
    const taskKey=Services.uuid.generateUUID().toString();
    RR_TASKS.set(taskKey,{status:'running',kind:'word',key:item.key});
    Zotero.Integration.execCommand('WinWord','addEditCitation',undefined,1).then(()=>{
      RR_TASKS.set(taskKey,{status:'finished',kind:'word',message:'原生引用流程已结束，请在 Word 核对引用。'});
    }).catch(error=>RR_TASKS.set(taskKey,{status:'failed',kind:'word',message:String(error.message || error)}));
    return {task:taskKey,status:'running',message:'已交给 Zotero 原生 Word 引用流程，请在引用窗口确认文献。'};
  });
  register('/sync',async data=>{
    if(!data.confirm)throw new Error('请确认启动 Zotero 云同步');
    if(!Zotero.Users.getCurrentUserID())throw new Error('请先在 Zotero 设置中登录同步账户');
    const task=Services.uuid.generateUUID().toString();RR_TASKS.set(task,{status:'running',kind:'sync'});
    Zotero.Sync.Runner.sync().then(()=>RR_TASKS.set(task,{status:'finished',kind:'sync',message:'Zotero 同步流程已结束，请在 Zotero 核对同步状态。'}))
      .catch(error=>RR_TASKS.set(task,{status:'failed',kind:'sync',message:String(error.message || error)}));
    return {task,status:'running'};
  });
  register('/task',async data=>RR_TASKS.get(data.task) || {status:'unknown',message:'此操作状态不可用，可能已重启 Zotero，请在 Zotero 或 Word 核对。'});
}
function shutdown(){for(const path of RR_ROUTES)delete Zotero.Server.Endpoints[path];RR_ROUTES.length=0;RR_TASKS.clear();}
function install(){}
function uninstall(){}
