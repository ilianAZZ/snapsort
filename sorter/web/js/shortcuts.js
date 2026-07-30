/** Keyboard shortcuts for the sorting screen. */

import { $, isScreenActive, toast } from './dom.js';
import { Browser } from './browser.js';
import { Prompt } from './prompt.js';
import { Sorter } from './sorter.js';

const ACTIONS = {
  ArrowRight: () => Sorter.decide('keep'),
  ArrowLeft: () => Sorter.decide('trash'),
  ArrowUp: () => Sorter.decide('fav'),
  ArrowDown: () => Sorter.decide('skip'),
  Backspace: () => Sorter.undo(),
  u: () => Sorter.undo(),
  j: () => Sorter.join(),
  ' ': () => Sorter.togglePlay(),
  m: () => Sorter.toggleSound(),
  c: () => Sorter.toggleOverlay(),
  n: () => Sorter.newFolder(),
};

// These keys cancel the browser's own behaviour (scrolling, going back); the
// others are harmless.
const PREVENT = new Set(['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Backspace', ' ']);

export function initShortcuts() {
  document.addEventListener('keydown', event => {
    if (Prompt.isOpen || Browser.isOpen) {
      if (event.key === 'Escape') {
        Prompt.close(null);
        Browser.close(null);
      }
      return;
    }
    if (event.key === 'Escape') {
      $('#help').classList.add('hidden');
      $('#about').classList.add('hidden');
      return;
    }
    if (!isScreenActive('sorter')) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    const key = event.key;
    const handler = ACTIONS[key] || ACTIONS[key.toLowerCase()];
    if (handler) {
      if (PREVENT.has(key)) event.preventDefault();
      return handler();
    }
    if (key === '?' || key === '/') return $('#help').classList.toggle('hidden');
    if (/^[0-9]$/.test(key)) {
      const folder = (Sorter.session?.folders || []).find(f => f.key === key);
      if (folder) Sorter.decide('folder', folder.id);
      else toast(`No folder on key ${key} — press N to create one`);
    }
  });
}
