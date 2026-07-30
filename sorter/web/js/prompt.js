/** Input modal: a name, and optionally a shortcut key. */

import { $, $$ } from './dom.js';

const DIGITS = '1234567890'.split('');

export const Prompt = {
  resolve: null,
  withKeys: false,
  key: null,

  /** Returns {name, key} when `withKeys`, otherwise the typed string — or null. */
  text(title, placeholder, withKeys = true, usedKeys = []) {
    $('#prompt-title').textContent = title;
    const input = $('#prompt-input');
    input.placeholder = placeholder;
    input.value = '';
    this.withKeys = withKeys;
    this.key = null;

    const row = $('#prompt-keys');
    row.classList.toggle('hidden', !withKeys);
    row.innerHTML = '';
    if (withKeys) this.buildKeys(row, usedKeys);

    $('#prompt').classList.remove('hidden');
    setTimeout(() => input.focus(), 50);
    return new Promise(r => { this.resolve = r; });
  },

  buildKeys(row, usedKeys) {
    DIGITS.forEach(digit => {
      const button = document.createElement('button');
      button.textContent = digit;
      button.disabled = usedKeys.includes(digit);
      button.onclick = () => this.select(this.key === digit ? null : digit);
      row.append(button);
    });
    this.select(DIGITS.find(d => !usedKeys.includes(d)) || null);
  },

  select(key) {
    this.key = key;
    $$('#prompt-keys button').forEach(b => b.classList.toggle('on', b.textContent === key));
  },

  close(value) {
    $('#prompt').classList.add('hidden');
    if (this.resolve) this.resolve(value);
    this.resolve = null;
  },

  get isOpen() {
    return !$('#prompt').classList.contains('hidden');
  },
};

export function initPrompt() {
  $('#prompt-ok').onclick = () => {
    const value = $('#prompt-input').value.trim();
    if (!value) return;
    Prompt.close(Prompt.withKeys ? { name: value, key: Prompt.key } : value);
  };
  $('#prompt-input').onkeydown = event => {
    event.stopPropagation();          // do not fire the sorting shortcuts
    if (event.key === 'Enter') $('#prompt-ok').click();
    if (event.key === 'Escape') Prompt.close(null);
  };
  $$('#prompt [data-close]').forEach(b => { b.onclick = () => Prompt.close(null); });
}
