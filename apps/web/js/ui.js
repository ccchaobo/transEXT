/* Shared workspace chrome. Menus do not own library or reader state. */
const SVG_NS = 'http://www.w3.org/2000/svg';
const paths = {
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  folder: 'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z',
  library: 'M4 4v16M9 4v16M14 4v16M18 4l3 16',
  star: 'm12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z',
  clock: 'M12 8v4l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  book: 'M12 5C8 3 5 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-2-1-5-1-9 1Zm0 0v15',
  check: 'm5 12 4 4L19 6',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 14h12l1-14M10 10v6M14 10v6',
  edit: 'm16 3 5 5-12 12-6 1 1-6L16 3ZM14 5l5 5',
  plus: 'M12 5v14M5 12h14',
  file: 'M14 3H5v18h14V8l-5-5ZM14 3v5h5M8 12h8M8 16h6',
  tag: 'M3 3h8l10 10-8 8L3 11V3ZM7 7h.01',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  sun: 'M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  moon: 'M20 15A9 9 0 0 1 9 3a9 9 0 1 0 11 12Z',
  monitor: 'M3 4h18v13H3V4ZM8 21h8M12 17v4',
  settings: 'M4 7h16M4 17h16M9 4v6M15 14v6',
  close: 'M6 6l12 12M6 18 18 6',
  send: 'M12 19V5m-6 6 6-6 6 6',
  stop: 'M7 7h10v10H7Z',
  copy: 'M9 4h11v13H9V4ZM4 9v11h11',
};

export function icon(name) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', name === 'more' ? '3.5' : '1.7');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('ui-icon');
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', paths[name] || paths.file);
  svg.appendChild(path);
  return svg;
}

let activeMenu = null;
let sequence = 0;
export function closeActionMenu(owner = null) {
  if (!owner || owner.contains(activeMenu?.button)) activeMenu?.close(false);
}

/** Destructive choices remain explicit; Escape and the initial focus both cancel. */
export function confirmAction({ title, message, confirmLabel = '确认', danger = true }) {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog'); dialog.className = 'settings-dialog confirm-dialog';
    const heading = document.createElement('h2'); heading.id = `confirm-title-${++sequence}`; heading.textContent = title;
    const detail = document.createElement('p'); detail.id = `confirm-detail-${sequence}`; detail.className = 'confirm-description'; detail.textContent = message;
    dialog.setAttribute('aria-labelledby', heading.id); dialog.setAttribute('aria-describedby', detail.id);
    const actions = document.createElement('div'); actions.className = 'settings-actions';
    const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'button'; cancel.textContent = '取消';
    const accept = document.createElement('button'); accept.type = 'button'; accept.className = `button ${danger ? 'danger confirm-danger' : 'primary'}`; accept.textContent = confirmLabel;
    let accepted = false;
    cancel.addEventListener('click', () => dialog.close());
    accept.addEventListener('click', () => { accepted = true; dialog.close(); });
    dialog.addEventListener('close', () => { dialog.remove(); resolve(accepted); }, { once: true });
    actions.append(cancel, accept); dialog.append(heading, detail, actions); document.body.appendChild(dialog);
    dialog.showModal(); cancel.focus();
  });
}

/** Portal in the top layer where available, so scroll panes never clip a menu. */
export function createActionMenu({ label, items, onError = console.error, trigger = null }) {
  const button = trigger || document.createElement('button');
  if (!trigger) { button.className = 'button icon-button more-button'; button.appendChild(icon('more')); }
  button.type = 'button';
  button.setAttribute('aria-label', label);
  button.title = label;
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  const id = `action-menu-${++sequence}`;
  button.setAttribute('aria-controls', id);
  let panel = null, options = [], current = 0, point = null, previousFocus = null;
  let prefix = '', lastKey = 0;

  function close(restore = true) {
    if (!panel) return;
    const oldPanel = panel;
    panel = null;
    if (typeof oldPanel.hidePopover === 'function' && oldPanel.matches(':popover-open')) oldPanel.hidePopover();
    oldPanel.remove();
    button.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('scroll', scrolled, true);
    window.removeEventListener('resize', dismiss);
    window.removeEventListener('hashchange', dismiss);
    if (activeMenu === menu) activeMenu = null;
    if (restore) (button.isConnected ? button : previousFocus)?.focus({ preventScroll: true });
  }
  function outside(event) { if (!panel?.contains(event.target) && !button.contains(event.target)) close(false); }
  function scrolled(event) {
    if (panel?.contains(event.target)) return;
    if (event.target === document || event.target.contains?.(button)) close(false);
  }
  function dismiss() { close(false); }
  function focus(index) {
    if (!options.length) return;
    current = (index + options.length) % options.length;
    options[current].focus({ preventScroll: true });
    options[current].scrollIntoView({ block: 'nearest' });
  }
  function keydown(event) {
    if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Escape'].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      if (event.key === 'Escape') close();
      else focus(event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : current + (event.key === 'ArrowDown' ? 1 : -1));
    } else if (event.key === 'Tab') close();
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = performance.now(); prefix = now - lastKey < 700 ? prefix + event.key : event.key; lastKey = now;
      const found = options.findIndex(item => item.textContent.trim().toLowerCase().startsWith(prefix.toLowerCase()));
      if (found >= 0) { event.preventDefault(); focus(found); }
    }
  }
  function open(location = null, last = false) {
    if (button.disabled || !button.isConnected) return;
    closeActionMenu();
    previousFocus = document.activeElement; point = location; prefix = ''; options = [];
    panel = document.createElement('div'); panel.className = 'action-menu'; panel.id = id;
    panel.setAttribute('role', 'menu'); panel.setAttribute('aria-label', label);
    const heading = document.createElement('div'); heading.className = 'action-menu-heading'; heading.textContent = label; panel.appendChild(heading);
    for (const entry of typeof items === 'function' ? items() : items) {
      if (entry.separator) { const divider = document.createElement('div'); divider.className = 'action-menu-divider'; divider.setAttribute('role', 'separator'); panel.appendChild(divider); continue; }
      const item = document.createElement(entry.href ? 'a' : 'button');
      if (entry.href) { item.href = entry.href; if (entry.newTab) { item.target = '_blank'; item.rel = 'noopener noreferrer'; } }
      else item.type = 'button';
      item.className = `action-menu-item${entry.danger ? ' is-danger' : ''}`;
      item.tabIndex = -1;
      item.setAttribute('role', typeof entry.checked === 'boolean' ? 'menuitemradio' : 'menuitem');
      if (typeof entry.checked === 'boolean') item.setAttribute('aria-checked', String(entry.checked));
      if (entry.disabled) { item.setAttribute('aria-disabled', 'true'); if (!entry.href) item.disabled = true; }
      item.appendChild(icon(entry.icon));
      const text = document.createElement('span'); text.textContent = entry.label; item.appendChild(text);
      if (entry.checked) item.appendChild(icon('check'));
      item.addEventListener('focus', () => { current = options.indexOf(item); });
      item.addEventListener('click', async event => {
        if (entry.disabled) { event.preventDefault(); return; }
        close();
        try { await entry.onSelect?.(); } catch (error) { onError(error); }
      });
      panel.appendChild(item); if (!entry.disabled) options.push(item);
    }
    panel.addEventListener('keydown', keydown);
    (button.closest('dialog') || document.body).appendChild(panel);
    if (typeof panel.showPopover === 'function') { panel.popover = 'manual'; panel.showPopover(); }
    // Measure once; flipping and clamping keep the popup on screen at every zoom level.
    const anchor = button.getBoundingClientRect(), box = panel.getBoundingClientRect();
    let x = point ? point.x : anchor.right - box.width;
    let y = point ? point.y : anchor.bottom + 8;
    if (y + box.height > window.innerHeight - 8) y = point ? point.y - box.height : anchor.top - box.height - 8;
    panel.style.left = `${Math.max(8, Math.min(x, window.innerWidth - box.width - 8))}px`;
    panel.style.top = `${Math.max(8, Math.min(y, window.innerHeight - box.height - 8))}px`;
    button.setAttribute('aria-expanded', 'true'); activeMenu = menu;
    document.addEventListener('pointerdown', outside, true);
    // A click may finish scrolling its trigger into view in this frame. It must not
    // dismiss the menu it just opened; later workspace scrolling still closes it.
    const openedPanel = panel;
    requestAnimationFrame(() => { if (panel === openedPanel) document.addEventListener('scroll', scrolled, true); });
    window.addEventListener('resize', dismiss);
    window.addEventListener('hashchange', dismiss);
    focus(last ? options.length - 1 : 0);
  }
  button.addEventListener('click', event => { event.stopPropagation(); if (panel) close(); else open(); });
  button.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); open(null, event.key === 'ArrowUp'); }
  });
  const menu = { button, open, close, bindContext(element) {
    element.addEventListener('contextmenu', event => { event.preventDefault(); event.stopPropagation(); open({ x: event.clientX, y: event.clientY }); });
    element.addEventListener('keydown', event => { if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { event.preventDefault(); event.stopPropagation(); open(); } });
  } };
  return menu;
}

export function initWorkspaceUI() {
  const trigger = document.getElementById('appearance-button');
  if (!trigger) return;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const names = { system: '跟随系统', light: '浅色', dark: '深色' };
  let preference = 'system';
  try { const stored = localStorage.getItem('rr.appearance'); if (stored in names) preference = stored; } catch { /* Private browsing can disable storage. */ }
  function apply() {
    document.documentElement.dataset.theme = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
    trigger.replaceChildren(icon(preference === 'system' ? 'monitor' : preference === 'dark' ? 'moon' : 'sun'));
    trigger.title = `外观：${names[preference]}`;
  }
  createActionMenu({ label: '选择外观', trigger, items: () => Object.entries(names).map(([value, name]) => ({
    label: name, icon: value === 'system' ? 'monitor' : value === 'dark' ? 'moon' : 'sun', checked: preference === value,
    onSelect() { preference = value; try { localStorage.setItem('rr.appearance', value); } catch { /* The choice still applies to this session. */ } apply(); },
  })) });
  media.addEventListener('change', apply); apply();
  document.getElementById('settings-button')?.prepend(icon('settings'));
}
