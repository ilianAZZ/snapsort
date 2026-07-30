/** Small DOM helpers shared by every module. */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/** Use this whenever a user-provided name goes through innerHTML. */
export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ENTITIES[c]);

export function showScreen(id) {
  $$('.screen').forEach(s => s.classList.toggle('active', s.id === id));
}

export const isScreenActive = id => !!$(`#${id}`)?.classList.contains('active');

export function toast(message, isError) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' err' : '');
  el.textContent = message;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), isError ? 4200 : 1900);
}

/** Build an element in one line: el('div', {class: 'x'}, child…). */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node[key] = value;
    else node.setAttribute(key, value);
  }
  node.append(...children.filter(c => c !== null && c !== undefined));
  return node;
}
