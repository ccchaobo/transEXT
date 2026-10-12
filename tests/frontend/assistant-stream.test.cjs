const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');
function parser(){const context=vm.createContext({TextDecoder,ReadableStream,Response});
  const source=fs.readFileSync('apps/web/js/api.js','utf8').replace(/^export /gm,'');
  vm.runInContext(source+'\nglobalThis.consume=consumeAssistantEvents;',context);return context.consume;}

test('UTF-8 and SSE frames split across bytes preserve Chinese streaming text',async()=>{
  const input='event: delta\r\ndata: {"text":"逐段输出中文"}\r\n\r\nevent: done\r\ndata: {"answer":"逐段输出中文"}\r\n\r\n';
  const bytes=new TextEncoder().encode(input);const body=new ReadableStream({start(c){for(let i=0;i<bytes.length;i+=3)c.enqueue(bytes.slice(i,i+3));c.close();}});
  const events=[];const result=await parser()(new Response(body),(kind,value)=>events.push([kind,value]));
  assert.equal(result.answer,'逐段输出中文');assert.equal(events[0][1].text,'逐段输出中文');assert.equal(events.length,2);
});
test('malformed provider frames cancel the stream without a repeated request',async()=>{
  let cancelled=0;const body=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('event: delta\ndata: invalid\n\n'));},cancel(){cancelled++;}});
  await assert.rejects(parser()(new Response(body),()=>{}),/格式损坏/);assert.equal(cancelled,1);
});
test('a stream ending without done retains prior deltas and reports incomplete status',async()=>{
  const body=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('event: delta\ndata: {"text":"已有内容"}\n\n'));c.close();}});
  const events=[];await assert.rejects(parser()(new Response(body),(kind,value)=>events.push(value.text)),/回答未完成/);
  assert.deepEqual(events,['已有内容']);
});
