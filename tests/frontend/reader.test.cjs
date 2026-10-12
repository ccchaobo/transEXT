/* Execute the real reader logic with controlled DOM/API boundaries. These tests cover
   request ordering and job lifecycle, not browser layout or real translation quality. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.listeners = {}; this.style = {};
    this.dataset = {}; this.classList = { add() {}, remove() {}, toggle() {} };
    this.value = ''; this.disabled = false;
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children;this._text=''; }
  set textContent(value) { this._text=value;this.children=[]; }
  get textContent() { return (this._text || '')+this.children.map(child=>child.textContent || '').join(''); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this[name] = value; }
  set innerHTML(value) { this.children = []; }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness() {
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const timers = new Map();
  let timerId = 0;
  const context = vm.createContext({
    document: {
      body: new Element('body'),
      getElementById: get, createElement: tag => new Element(tag),
      createTextNode: text => ({ textContent: text }),
      querySelector: () => ({ dataset: { mode: 'side_by_side' } }),
      querySelectorAll: () => [],
    },
    localStorage: { getItem: () => null, setItem() {} },
    location: { hash: '#/doc/d1' }, window: { addEventListener() {} },
    performance: { now: () => 0 }, console: { error() {} },
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
    api: { originalUrl: id => `/original/${id}`, exportUrl: (id, language) => `/export/${id}/${language}` },
    ApiError: class extends Error {}, createSettingsPanel() {},
    createLibrary: options => ({load: async () => { context.libraryOptions=options; },dispose(){},activeCollection:()=>null}),
    createAssistantPanel: () => ({element: new Element(), toggle() {}, dispose() {}, setBlock() {}}),
  });
  let source = fs.readFileSync(path.join(__dirname, '../../apps/web/js/app.js'), 'utf8');
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../apps/web/js/text-layout.js'),'utf8').replace(/^export /gm,''),context);
  source = source.replace(/^import .*;\r?\n/gm, '').replace(/boot\(\);\s*$/, '');
  vm.runInContext(source + `\n globalThis.reader = {state, els, route, loadPage, gotoPage,
    renderLibrary, renderTarget, openReader, startJobPolling, stopJobPolling,
    refreshJobs, buildToolbar, boot, selectBlock, scrollPaneToBlock, linkPanes, blockArea, comparisonLayout};`, context);
  return { context, reader: context.reader, api: context.api, get, timers };
}

function page(index) {
  return { page: { index, page_number: index + 1, display_width: 600,
    display_height: 800, rotation: 0, source_kind: 'pdf' }, blocks: [] };
}

function ready(h) {
  h.reader.state.document = { id: 'd1', page_count: 3, active_revision_id: 'r1' };
  h.api.putReadingState = async () => {};
  vm.runInContext('renderSource = () => {}; renderTarget = () => {};', h.context);
}

test('a late page response cannot overwrite the latest page or save its reading position', async () => {
  const h = harness(); ready(h);
  const first = deferred(), second = deferred(), saved = [];
  h.api.page = (id, index) => index === 1 ? first.promise : second.promise;
  h.api.putReadingState = async (id, body) => saved.push(body.page_index);
  const a = h.reader.loadPage(1), b = h.reader.loadPage(2);
  second.resolve(page(2)); assert.equal(await b, true);
  first.resolve(page(1)); assert.equal(await a, false);
  assert.equal(h.reader.state.pageIndex, 2); assert.deepEqual(saved, [2]);
});

test('rapid next-page clicks advance from the requested page', async () => {
  const h = harness(); ready(h); const calls = [];
  h.api.page = (id, index) => { calls.push(index); return new Promise(() => {}); };
  const bar = h.reader.buildToolbar(h.reader.state.document, 'side_by_side');
  const next = bar.children[0].children[2];
  next.listeners.click(); next.listeners.click();
  assert.deepEqual(calls, [1, 2]);
});

test('leaving a document ignores its pending page response', async () => {
  const h = harness(); ready(h); const pending = deferred();
  h.api.page = () => pending.promise;
  const loading = h.reader.loadPage(1);
  h.reader.state.routeVersion++;
  pending.resolve(page(1)); assert.equal(await loading, false);
  assert.equal(h.reader.state.page, null);
});

test('switching language ignores a response for the previous language', async () => {
  const h = harness(); ready(h); const pending = deferred();
  h.api.page = () => pending.promise;
  const loading = h.reader.loadPage(1);
  h.reader.state.targetLanguage = 'ja';
  pending.resolve(page(1)); assert.equal(await loading, false);
});

test('opening an unparsed document waits and resumes polling without requesting a nonexistent page', async () => {
  const h = harness(); let pageCalls = 0;
  h.api.getDocument = async () => ({ id: 'd1', page_count: 3, parse_status: 'queued' });
  h.api.getReadingState = async () => ({ reading_state: null });
  h.api.outline = async () => ({ outline: [] });
  h.api.listAnnotations = async () => ({ annotations: [] });
  h.api.listJobs = async () => ({ jobs: [{ id: 'parse1', status: 'queued', type: 'parse' }] });
  h.api.page = async () => { pageCalls++; };
  await h.reader.openReader('d1');
  assert.equal(pageCalls, 0); assert.equal(h.timers.size, 1);
  assert.equal(h.get('translate-button').disabled, true);
});

test('a stale document response cannot replace the current document', async () => {
  const h = harness(); const pending = deferred();
  h.api.getDocument = () => pending.promise;
  const opening = h.reader.openReader('old');
  h.reader.state.routeVersion++;
  h.reader.state.document = { id: 'new' };
  pending.resolve({ id: 'old' }); await opening;
  assert.equal(h.reader.state.document.id, 'new');
});

test('a stale job response cannot replace another document jobs', async () => {
  const h = harness(); ready(h); const pending = deferred();
  h.api.listJobs = () => pending.promise;
  const loading = h.reader.refreshJobs();
  h.reader.state.routeVersion++;
  h.reader.state.jobs = [{ id: 'new-job' }];
  pending.resolve({ jobs: [{ id: 'old-job' }] }); await loading;
  assert.equal(h.reader.state.jobs[0].id, 'new-job');
});

test('a completed parse refreshes the whole reader including outline and page count', async () => {
  const h = harness(); ready(h); let refreshes = 0;
  h.reader.state.jobs = [{ id: 'parse1', status: 'running', type: 'parse' }];
  h.api.listJobs = async () => ({ jobs: [{ id: 'parse1', status: 'succeeded', type: 'parse' }] });
  h.api.getDocument = async () => h.reader.state.document;
  h.context.recordRefresh = () => { refreshes++; h.reader.state.routeVersion++; };
  vm.runInContext('route = async () => recordRefresh();', h.context);
  h.reader.startJobPolling();
  await [...h.timers.values()][0]();
  assert.equal(refreshes, 1);
});

test('polling does not start another request while one is in flight', async () => {
  const h = harness(); ready(h); const pending = deferred();
  h.reader.state.jobs = [{ id: 'j1', status: 'running' }];
  h.api.listJobs = () => pending.promise;
  h.api.getDocument = async () => h.reader.state.document;
  h.reader.startJobPolling();
  const [id, tick] = [...h.timers.entries()][0]; h.timers.delete(id);
  const polling = tick(); assert.equal(h.timers.size, 0);
  pending.resolve({ jobs: [{ id: 'j1', status: 'running' }] });
  await polling; assert.equal(h.timers.size, 1);
});

test('translated titles are displayed in the translation pane', () => {
  const h = harness();
  h.reader.renderTarget({ document_id: 'd1', blocks: [{ id: 'title', type: 'title',
    text: 'A paper', translation: { id: 't1', status: 'ok', text: '论文标题' } }] });
  const card = h.get('target-body').children[0];
  assert.ok(card.children.some(child => child.textContent === '论文标题'));
});

test('library receives route guards and the existing import action', async () => {
  const h = harness();
  await h.reader.renderLibrary();
  assert.equal(h.context.libraryOptions.container,h.get('view'));
  assert.equal(h.context.libraryOptions.api,h.api);
  assert.equal(h.context.libraryOptions.isCurrent(),true);
  h.reader.state.routeVersion++;
  assert.equal(h.context.libraryOptions.isCurrent(),false);
});

test('changing language updates the export link immediately', async () => {
  const h = harness(); ready(h);
  h.api.translationConfiguration = async () => ({ configured: false });
  h.api.exportUrl = (id, language) => `/export/${id}/${language}`;
  vm.runInContext('route = async () => {}; loadPage = async () => true;', h.context);
  await h.reader.boot();
  h.reader.state.route = { name: 'reader' };
  h.get('target-language').value = 'ja';
  await h.get('target-language').listeners.change();
  assert.equal(h.get('export-link').href, '/export/d1/ja');
});

test('linked scrolling records the clamped destination to prevent feedback at the page end', () => {
  const h = harness();
  const body = { id: 'source-body', scrollTop: 0, scrollHeight: 500, clientHeight: 400,
    getBoundingClientRect: () => ({ top: 0 }) };
  const block = { offsetHeight: 30, getBoundingClientRect: () => ({ top: 700 }) };
  h.reader.scrollPaneToBlock(body, new Map([['block', block]]), 'block');
  assert.equal(body.scrollTop, 100);
  assert.equal(h.reader.state.linkedTo['source-body'], 100);
});

test('side-by-side target scrolling never moves the fixed source even with a saved link preference', () => {
  const h = harness();
  const source = new Element(), target = new Element();
  Object.assign(source, { id: 'source-body', scrollTop: 50, scrollHeight: 1000, clientHeight: 100,
    getBoundingClientRect: () => ({ top: 0 }) });
  Object.assign(target, { id: 'target-body', scrollTop: 100,
    getBoundingClientRect: () => ({ top: 0 }) });
  h.reader.state.linkScrolling = true;
  h.reader.state.cards = new Map([['block', { offsetHeight: 50, getBoundingClientRect: () => ({ top: 20 }) }]]);
  h.reader.state.boxes = new Map([['block', { offsetHeight: 20, getBoundingClientRect: () => ({ top: 400 }) }]]);
  h.reader.linkPanes(source, target);
  target.listeners.scroll();
  assert.equal(source.scrollTop, 50);
});

test('overlay geometry excludes spans from other pages', () => {
  const h = harness();
  const area = h.reader.blockArea({ spans: [
    { page_index: 0, bbox: [.1, .2, .4, .22] },
    { page_index: 1, bbox: [.7, .8, .9, .9] },
  ] }, 0);
  assert.deepEqual(Array.from(area), [.1, .2, .4, .22]);
});

test('overlay geometry does not turn disconnected columns into a page-sized cover', () => {
  const h = harness();
  const area = h.reader.blockArea({ spans: [
    { page_index: 0, bbox: [.1, .2, .4, .22] },
    { page_index: 0, bbox: [.1, .23, .4, .25] },
    { page_index: 0, bbox: [.7, .8, .9, .9] },
  ] }, 0);
  assert.deepEqual(Array.from(area), [.1, .2, .4, .25]);
});

test('comparison bands never cut through graphics and retain both page edges', () => {
  const h = harness();
  const plan = h.reader.comparisonLayout({page:{index:0,visual_regions:[[.5,.3,.9,.7]]},blocks:[
    {id:'body',type:'paragraph',translation:{status:'ok',text:'正文'},spans:[{page_index:0,bbox:[.1,.4,.4,.45]}]},
    {id:'axis',type:'paragraph',translation:{status:'ok',text:'图内标签'},spans:[{page_index:0,bbox:[.6,.45,.7,.5]}]},
  ]});
  assert.equal(plan.bands[0].start,0);
  assert.equal(plan.bands.at(-1).end,1);
  assert.ok(plan.bands.every(band => !(band.start>.3 && band.start<.7)));
  assert.deepEqual(Array.from(plan.entries,entry=>entry.block.id),['body']);
});


test('split section numbers ignore old bad translations and stay in the original page', () => {
  const h = harness();
  const plan = h.reader.comparisonLayout({page:{index:3,visual_regions:[]},blocks:[
    {id:'number',type:'heading',text:'2',translation:{status:'ok',text:'请提供需要翻译的英文论文内容。'},spans:[{page_index:3,bbox:[.22,.147,.231,.162]}]},
    {id:'title',type:'heading',text:'Methods',translation:{status:'ok',text:'方法'},spans:[{page_index:3,bbox:[.253,.147,.337,.162]}]},
    {id:'body',type:'paragraph',text:'Body',spans:[{page_index:3,bbox:[.22,.22,.785,.35]}]},
  ]});
  assert.deepEqual(Array.from(plan.entries,entry=>entry.block.id),['title']);
  assert.equal(plan.entries[0].area[0],.253);
  assert.equal(plan.entries[0].area[2],.785);
});

test('a plot-edge label must not cover even a thin strip of a graph', () => {
  const h=harness();
  const plan=h.reader.comparisonLayout({page:{index:0,visual_regions:[[.5,.083,.9,.3]]},blocks:[
    {id:'label',type:'paragraph',text:'Internal test set',translation:{status:'ok',text:'内部测试集'},spans:[{page_index:0,bbox:[.6,.073,.7,.085]}]},
    {id:'body',type:'paragraph',text:'Body',translation:{status:'ok',text:'正文'},spans:[{page_index:0,bbox:[.1,.4,.9,.6]}]},
  ]});
  assert.deepEqual(Array.from(plan.entries,entry=>entry.block.id),['body']);
  assert.equal(plan.bands.length,1);
  assert.deepEqual(Array.from(plan.fallbacks,entry=>entry.block.id),['label']);
});

test('CodecLM page 5 shows the translated paragraph despite overlapping formula font boxes', () => {
  const h=harness();
  const payload=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/codec-overlay-page5.json'),'utf8'));
  const plan=h.reader.comparisonLayout(payload);
  const entry=plan.entries.find(e=>e.block.id==='blk_87617b4d3b1c0152cb09');
  assert.ok(entry,'the already translated long paragraph must have an overlay');
  assert.equal(entry.block.translation.status,'ok');
  assert.ok(entry.block.translation.text.startsWith('虽然 Self-Rubrics'));
  assert.equal(entry.area[1],entry.sourceArea[1]);
  assert.ok(entry.area[3]<=.6806953493979713,'the formula remains outside the cover');
  assert.equal(plan.inGraphic(entry.area),false);
  assert.equal(plan.fallbacks.length,0);
});

test('inline mathematical symbols do not remove an entire translated prose paragraph', () => {
  const h=harness();
  const plan=h.reader.comparisonLayout({page:{index:0,visual_regions:[]},blocks:[
    {id:'prose',type:'paragraph',translatable:true,text:'For x ∈ R, we calculate ∑ x and √x.',
      translation:{status:'ok',text:'对于 x ∈ R，我们计算 ∑ x 和 √x。'},spans:[{page_index:0,bbox:[.1,.2,.45,.3]}]},
  ]});
  assert.deepEqual(Array.from(plan.entries,e=>e.block.id),['prose']);
});

test('prose completely intersecting a graphic retains its complete translation in an explicit fallback', () => {
  const h=harness();
  const block={id:'mixed',type:'paragraph',translatable:true,text:'Body in a diagram',
    translation:{status:'ok',text:'完整中文正文'},spans:[{page_index:0,bbox:[.1,.3,.45,.4]}]};
  const plan=h.reader.comparisonLayout({page:{index:0,visual_regions:[[.05,.25,.5,.5]]},blocks:[block]});
  assert.equal(plan.entries.length,0);
  assert.equal(plan.fallbacks.length,1);
  assert.equal(plan.fallbacks[0].block.translation.text,'完整中文正文');
  assert.equal(plan.fallbacks[0].reason,'protected-region');
});
