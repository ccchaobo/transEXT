const test=require('node:test');const assert=require('node:assert/strict');
const parser=()=>import('../../apps/web/js/assistant-markdown.js');

test('AI prose renders real headings, emphasis, nested lists, tables and code',async()=>{
  const {answerHtml}=await parser();
  const html=answerHtml('## 方法\n\n1. **网络结构**\n   - 编码器\n   - 解码器\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nx = 1\n```');
  for(const element of ['<h2>','<strong>','<ol>','<ul>','<table>','<pre><code'])assert.ok(html.includes(element),element);
});
test('raw HTML and model image URLs are displayed without executable nodes or requests',async()=>{
  const {answerHtml}=await parser();
  const html=answerHtml('<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n\n![tracker](https://external.example/tracker.png)');
  assert.ok(!html.includes('<script>')&&!html.includes('<img'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('图片：tracker'));
});
test('unsafe links never render anchors and HTTP(S) links isolate the opener',async()=>{
  const {answerHtml}=await parser();
  for(const url of ['javascript:alert(1)','data:text/html,bad','//external.example','file:///C:/private'])assert.ok(!answerHtml(`[open](${url})`).includes('<a '));
  assert.match(answerHtml('[paper](https://example.com/paper)'),/rel="noopener noreferrer"/);
});
