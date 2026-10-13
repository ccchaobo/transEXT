import MarkdownIt from '../vendor/markdown-it/markdown-it.esm.min.mjs';

const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true });
markdown.validateLink = value => /^https?:\/\//i.test(value);
markdown.renderer.rules.image = (tokens, index) => `[图片：${markdown.utils.escapeHtml(tokens[index].content || '未提供图片')}]`;
const defaultLink = markdown.renderer.rules.link_open || ((tokens, index, options, env, self) => self.renderToken(tokens, index, options));
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('target', '_blank');
  tokens[index].attrSet('rel', 'noopener noreferrer');
  return defaultLink(tokens, index, options, env, self);
};

export function answerHtml(text) { return markdown.render(String(text || '')); }

export function renderAnswer(element, text, { sources = [], onSource } = {}) {
  // Only the configured offline parser may produce markup: raw HTML and images
  // are disabled, links are restricted to HTTP(S), and citation buttons use DOM APIs.
  element.innerHTML = answerHtml(text);
  if (!onSource) return;
  const known = new Map(sources.map(source => [source.id, source]));
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const item = walker.currentNode;
    if (!item.parentElement.closest('code,pre,a,button') && /\[S\d+\]/.test(item.textContent)) nodes.push(item);
  }
  for (const item of nodes) {
    const fragment = document.createDocumentFragment(); let offset = 0;
    for (const match of item.textContent.matchAll(/\[(S\d+)\]/g)) {
      fragment.appendChild(document.createTextNode(item.textContent.slice(offset, match.index)));
      const source = known.get(match[1]);
      if (source) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'assistant-inline-source';
        button.textContent = match[0]; button.title = `第 ${source.page_number} 页 · ${source.type}`;
        button.setAttribute('aria-label', `查看来源 ${source.id}，第 ${source.page_number} 页`);
        button.addEventListener('click', () => onSource(source)); fragment.appendChild(button);
      } else fragment.appendChild(document.createTextNode(match[0]));
      offset = match.index + match[0].length;
    }
    fragment.appendChild(document.createTextNode(item.textContent.slice(offset))); item.replaceWith(fragment);
  }
}
