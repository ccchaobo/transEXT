const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');

async function bridge(){
  const items=new Map(),calls=[],endpoints={};let count=0;
  const library={libraryID:1,name:'Isolated',editable:true,filesEditable:true,libraryType:'user'};
  class Item{
    constructor(type='journalArticle'){this.data={itemType:type};this.libraryID=1;this.library=library;this.deleted=false;this.itemTypeID=type;this.parentItemKey=null;this.id=++count;this.key='TEST'+String(count).padStart(4,'0');}
    toJSON(){return structuredClone(this.data);}
    async fromJSON(value){this.data=structuredClone(value);}
    async save(){items.set(this.key,this);calls.push(['save',this.key]);}
    isAttachment(){return this.data.itemType==='attachment';}isNote(){return this.data.itemType==='note';}
    getRelations(){return this.data.relations || {};}
    addRelation(predicate,value){this.data.relations={...this.getRelations(),[predicate]:value};}
  }
  const context=vm.createContext({TextEncoder,URL,structuredClone,
    Cc:{'@mozilla.org/security/hash;1':{createInstance:()=>({SHA256:1,init(){},update(bytes){this.value=crypto.createHash('sha256').update(bytes).digest('latin1');},finish(){return this.value;}})}},Ci:{nsICryptoHash:{}},
    Services:{uuid:{generateUUID:()=>({toString:()=>crypto.randomUUID()})}},
    Zotero:{version:'9.0.6',Server:{Endpoints:endpoints},Prefs:{get:()=> 'paired-secret'},Users:{getLocalUserKey:()=> 'isolated',getCurrentUserID:()=>1},
      Libraries:{get:()=>library,getAll:()=>[library],userLibraryID:1},Collections:{getByLibrary:async()=>[]},
      Items:{getAll:async()=>[...items.values()],getByLibraryAndKey:(id,key)=>items.get(key),merge:async(master,others)=>calls.push(['merge',master.key,others.map(x=>x.key)])},
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
  return {request,items,calls,context,endpoints};
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
