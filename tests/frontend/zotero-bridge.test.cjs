const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');

async function bridge(version='10.0.6'){
  const items=new Map(),calls=[],endpoints={};let count=0;
  const files=new Map(),path=require('node:path').posix;
  const library={libraryID:1,name:'Isolated',editable:true,filesEditable:true,libraryType:'user'};
  class Item{
    constructor(type='journalArticle'){this.data={itemType:type};this.libraryID=1;this.library=library;this.deleted=false;this.itemTypeID=type;this.parentItemKey=null;this.id=++count;this.key='TEST'+String(count).padStart(4,'0');}
    toJSON(){return structuredClone(this.data);}
    async fromJSON(value){this.data=structuredClone(value);}
    async save(){items.set(this.key,this);calls.push(['save',this.key]);}
    isAttachment(){return this.data.itemType==='attachment';}isNote(){return this.data.itemType==='note';}
    async getFilePathAsync(){return this.path;}
    getAttachments(){return [...items.values()].filter(i=>i.parentItemKey===this.key).map(i=>i.id);}
    getRelations(){return this.data.relations || {};}
    addRelation(predicate,value){this.data.relations={...this.getRelations(),[predicate]:value};}
  }
  const context=vm.createContext({TextEncoder,URL,structuredClone,Uint8Array,btoa,atob,
    PathUtils:{join:path.join,filename:path.basename},
    IOUtils:{exists:async p=>files.has(p),stat:async p=>({size:files.get(p).length,lastModified:1}),
      read:async(p,opts)=>files.get(p).slice(opts.offset,opts.offset+opts.maxBytes),
      write:async(p,bytes,opts)=>files.set(p,Buffer.concat([opts?.mode==='append'?files.get(p):Buffer.alloc(0),Buffer.from(bytes)])),
      remove:async p=>files.delete(p)},
    Cc:{'@mozilla.org/security/hash;1':{createInstance:()=>({SHA256:1,init(){},update(bytes){this.value=crypto.createHash('sha256').update(bytes).digest('latin1');},finish(){return this.value;}})}},Ci:{nsICryptoHash:{}},
    Services:{uuid:{generateUUID:()=>({toString:()=>crypto.randomUUID()})}},
    Zotero:{version,Server:{Endpoints:endpoints},Prefs:{get:()=> 'paired-secret'},Users:{getLocalUserKey:()=> 'isolated',getCurrentUserID:()=>1},
      getTempDirectory:()=>({path:'/temp'}),Utilities:{Internal:{md5Async:async p=>crypto.createHash('md5').update(files.get(p)).digest('hex')}},
      Attachments:{importFromFile:async({file,parentItemID})=>{
        const parent=[...items.values()].find(i=>i.id===parentItemID),item=new Item('attachment');
        item.parentItemKey=parent.key;item.attachmentContentType='application/pdf';item.path='/storage/'+item.key+'.pdf';files.set(item.path,Buffer.from(files.get(file)));
        await item.save();calls.push(['attach',item.key]);return item;
      }},
      Libraries:{get:()=>library,getAll:()=>[library],userLibraryID:1},Collections:{getByLibrary:async()=>[]},
      Items:{getAll:async()=>[...items.values()],getByLibraryAndKey:(id,key)=>items.get(key),get:id=>[...items.values()].find(i=>i.id===id),merge:async(master,others)=>calls.push(['merge',master.key,others.map(x=>x.key)])},
      DB:{executeTransaction:async fn=>fn()},Item,logError(){},
      getActiveZoteroPane:()=>({selectItem:async()=>{}}),Integration:{getApplication:()=>({getActiveDocument:async()=>{throw Error('No Word');}}),execCommand:async()=>calls.push(['word'])},
      Sync:{Runner:{sync:async()=>calls.push(['sync'])}}}});
  vm.runInContext(fs.readFileSync('plugins/zotero-reader-bridge/bootstrap.js','utf8'),context);
  await context.startup();
  async function request(path,data={},authorized=true){
    const handler=new endpoints['/research-reader'+path]();
    const [status,,body]=await handler.init({data,headers:authorized?{'X-Research-Reader-Key':'paired-secret'}:{}});
    return {status,data:JSON.parse(body)};
  }
  return {request,items,calls,context,endpoints,files,library};
}

test('native endpoints require the paired key and do not disclose it',async()=>{
  const b=await bridge();assert.equal((await b.request('/inventory',{},false)).status,403);
  const status=await b.request('/status',{},false);assert.equal(status.status,200);assert.equal(status.data.authorized,false);
  assert.ok(!JSON.stringify(status.data).includes('paired-secret'));
  assert.equal((await b.request('/status')).data.authorized,true);
});

test('replaying a saved reader operation cannot create a second native item',async()=>{
  const b=await bridge(),payload={library_id:1,operation_id:'reader:doc1',items:[{title:'Paper',itemType:'journalArticle'}]};
  const first=await b.request('/save',payload),second=await b.request('/save',payload);
  assert.equal(first.status,200);assert.equal(second.data.items[0].key,first.data.items[0].key);assert.equal(b.items.size,1);
});

test('same DOI is reused without replacing native title or abstract',async()=>{
  const b=await bridge();const first=await b.request('/save',{items:[{title:'Original',DOI:'10.1000/one',abstractNote:'Keep'}]});
  const second=await b.request('/save',{items:[{title:'Changed',DOI:'https://doi.org/10.1000/ONE',abstractNote:'Overwrite'}]});
  assert.equal(second.data.items[0].key,first.data.items[0].key);assert.equal(second.data.items[0].reused,true);
  assert.equal(second.data.items[0].data.title,'Original');assert.equal(second.data.items[0].data.abstractNote,'Keep');
});

test('fingerprint rejects stale native updates and restricted fields stay intact',async()=>{
  const b=await bridge();const saved=(await b.request('/save',{items:[{title:'Original',date:'2025-07-13'}]})).data.items[0];
  assert.equal((await b.request('/update',{key:saved.key,expected_fingerprint:'old',fields:{title:'Lost'}})).status,409);
  const changed=await b.request('/update',{key:saved.key,expected_fingerprint:saved.fingerprint,fields:{title:'New',deleted:true,relations:{danger:'x'}}});
  assert.equal(changed.status,200);assert.equal(changed.data.data.title,'New');assert.equal(changed.data.data.date,'2025-07-13');assert.equal(changed.data.data.deleted,undefined);
});

test('native sync requires explicit confirmation; missing Word stops before insertion',async()=>{
  const b=await bridge();assert.equal((await b.request('/sync',{})).status,400);assert.equal(b.calls.length,0);
  const item=(await b.request('/save',{items:[{title:'Paper'}]})).data.items[0];
  assert.equal((await b.request('/word',{key:item.key,confirm:true})).status,400);assert.ok(!b.calls.some(c=>c[0]==='word'));
});

test('disabling the plugin unregisters every paired endpoint',async()=>{
  const b=await bridge();assert.ok(Object.keys(b.endpoints).length>5);b.context.shutdown();assert.equal(Object.keys(b.endpoints).length,0);
});

test('supported Zotero versions share the authenticated transfer contract',async()=>{
  const manifest=JSON.parse(fs.readFileSync('plugins/zotero-reader-bridge/manifest.json','utf8'));
  assert.equal(manifest.applications.zotero.strict_max_version,'10.0.*');
  for(const version of ['9.0.6','10.0.6']){
    const b=await bridge(version),status=(await b.request('/status')).data;
    assert.equal(status.zotero_version,version);assert.equal(status.bridge_version,manifest.version);assert.equal(status.pdf_transfer,'chunks-v1');
  }
});

test('PDF reads are keyed to native items and reject stale metadata and unpaired access',async()=>{
  const b=await bridge();const item=(await b.request('/save',{items:[{itemType:'attachment'}]})).data.items[0];
  b.items.get(item.key).path='/source.pdf';b.items.get(item.key).attachmentContentType='application/pdf';b.files.set('/source.pdf',Buffer.from('%PDF-fixture'));
  const info=(await b.request('/file',{key:item.key})).data;
  const payload={key:item.key,size:info.size,mtime:info.mtime,offset:0};
  assert.equal((await b.request('/file-chunk',payload,false)).status,403);
  assert.equal((await b.request('/file-chunk',{...payload,mtime:0})).status,409);
  const chunk=(await b.request('/file-chunk',payload)).data;
  assert.equal(Buffer.from(chunk.base64,'base64').toString(),'%PDF-fixture');
  assert.equal((await b.request('/file-chunk',{...payload,offset:-1})).status,400);
  b.items.get(item.key).deleted=true;
  assert.equal((await b.request('/file',{key:item.key})).status,400);
});

test('chunk upload rejects bad ordering, validates checksum, reuses attachments and cleans temporary files',async()=>{
  const b=await bridge();const parent=(await b.request('/save',{items:[{title:'Isolated'}]})).data.items[0];
  const bytes=Buffer.from('%PDF-isolated-test'),md5=crypto.createHash('md5').update(bytes).digest('hex');
  const begin=()=>b.request('/attachment-upload',{action:'begin',key:parent.key,size:bytes.length,md5});
  const upload=(await begin()).data.upload;
  assert.equal((await b.request('/attachment-upload',{action:'append',upload,offset:2,base64:bytes.toString('base64')})).status,400);
  assert.equal((await b.request('/attachment-upload',{action:'finish',upload})).status,400);
  assert.equal(b.calls.filter(c=>c[0]==='attach').length,0);
  assert.equal((await b.request('/attachment-upload',{action:'append',upload,offset:0,base64:bytes.toString('base64')})).data.offset,bytes.length);
  const saved=await b.request('/attachment-upload',{action:'finish',upload});assert.equal(saved.status,200);
  assert.ok(![...b.files.keys()].some(p=>p.startsWith('/temp/')));
  const again=(await begin()).data.upload;
  await b.request('/attachment-upload',{action:'append',upload:again,offset:0,base64:bytes.toString('base64')});
  assert.equal((await b.request('/attachment-upload',{action:'finish',upload:again})).data.reused,true);
  assert.equal(b.calls.filter(c=>c[0]==='attach').length,1);
  await begin();await b.context.shutdown();assert.ok(![...b.files.keys()].some(p=>p.startsWith('/temp/')));
});

test('upload cannot target an uneditable library or retain a forged checksum',async()=>{
  const b=await bridge();const parent=(await b.request('/save',{items:[{title:'Isolated'}]})).data.items[0];
  const payload={action:'begin',key:parent.key,size:8,md5:'0'.repeat(32)};
  b.library.filesEditable=false;assert.equal((await b.request('/attachment-upload',payload)).status,400);
  b.library.filesEditable=true;const upload=(await b.request('/attachment-upload',payload)).data.upload;
  await b.request('/attachment-upload',{action:'append',upload,offset:0,base64:Buffer.from('%PDF-bad').toString('base64')});
  assert.equal((await b.request('/attachment-upload',{action:'finish',upload})).status,400);
  await b.request('/attachment-upload',{action:'cancel',upload});assert.ok(!b.calls.some(c=>c[0]==='attach'));
  assert.ok(![...b.files.keys()].some(p=>p.startsWith('/temp/')));
});
