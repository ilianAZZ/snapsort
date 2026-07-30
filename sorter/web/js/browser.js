/** Server-side file browser: pick the archives or the destination. */

import { $, $$, escapeHtml, toast } from './dom.js';
import { api } from './api.js';
import { bytes } from './format.js';
import { Prompt } from './prompt.js';

export const Browser = {
  mode: 'dir',              // 'zip' (sources) or 'dir' (destination)
  selection: new Set(),
  current: '',
  data: null,
  resolve: null,

  /** Opens the modal and resolves with the chosen paths, or null. */
  open(mode, startPath) {
    this.mode = mode;
    this.selection = new Set();
    this.resolve = null;
    $('#browser-title').textContent = mode === 'zip'
      ? 'Select the archives (.zip) or a folder'
      : 'Choose the destination folder';
    $('#new-folder-btn').classList.toggle('hidden', mode === 'zip');
    $('#browser').classList.remove('hidden');
    this.go(startPath);
    return new Promise(r => { this.resolve = r; });
  },

  close(value) {
    $('#browser').classList.add('hidden');
    if (this.resolve) this.resolve(value);
    this.resolve = null;
  },

  get isOpen() {
    return !$('#browser').classList.contains('hidden');
  },

  async go(path) {
    let data;
    try {
      data = await api('/api/browse?path=' + encodeURIComponent(path || ''));
    } catch (e) {
      return toast(e.message, true);
    }
    this.current = data.path;
    this.data = data;
    this.renderCrumbs(data);
    this.renderListing(data);
    this.updateHint();
  },

  renderCrumbs(data) {
    const crumbs = $('#crumbs');
    crumbs.innerHTML = '';
    const add = (label, target) => {
      const b = document.createElement('button');
      b.textContent = label;
      b.onclick = () => this.go(target);
      crumbs.append(b);
    };
    add('/', '/');
    let acc = '';
    data.path.split('/').filter(Boolean).forEach((part, i) => {
      acc += '/' + part;
      if (i) {
        const sep = document.createElement('span');
        sep.textContent = '›';
        crumbs.append(sep);
      }
      add(part, acc);
    });
  },

  renderListing(data) {
    const list = $('#listing');
    list.innerHTML = '';
    if (data.parent) list.append(this.row('↰', '..', () => this.go(data.parent)));

    data.dirs.forEach(dir => {
      const row = this.row('📁', dir.name, () => this.go(dir.path));
      if (this.mode === 'zip') {
        const use = document.createElement('small');
        use.textContent = 'use this folder';
        use.style.cursor = 'pointer';
        use.onclick = event => { event.stopPropagation(); this.close([dir.path]); };
        row.append(use);
      }
      list.append(row);
    });

    if (this.mode !== 'zip') return;
    data.zips.forEach(zip => {
      const row = this.row('🗜️', zip.name, () => this.toggle(zip.path, row));
      const size = document.createElement('small');
      size.textContent = bytes(zip.size);
      row.append(size);
      row.classList.toggle('sel', this.selection.has(zip.path));
      list.append(row);
    });
    if (data.zips.length > 1) {
      list.prepend(this.row('✓', `Select all (${data.zips.length} archives)`, () => {
        data.zips.forEach(z => this.selection.add(z.path));
        this.go(this.current);
      }));
    }
  },

  row(icon, label, onClick) {
    const button = document.createElement('button');
    button.className = 'entry';
    button.innerHTML = `<span class="ic">${icon}</span><span>${escapeHtml(label)}</span>`;
    button.onclick = onClick;
    return button;
  },

  toggle(path, row) {
    if (this.selection.has(path)) this.selection.delete(path);
    else this.selection.add(path);
    row.classList.toggle('sel', this.selection.has(path));
    this.updateHint();
  },

  updateHint() {
    const hint = $('#browser-hint');
    if (this.mode === 'zip') {
      hint.textContent = this.selection.size
        ? `${this.selection.size} archive(s) selected`
        : 'Tick some .zip files, or confirm to use the current folder';
    } else {
      const data = this.data || {};
      hint.textContent = data.writable === false
        ? '⚠︎ folder is not writable'
        : `${bytes(data.free || 0)} free`;
    }
  },
};

export function initBrowser() {
  $('#browser-ok').onclick = () => {
    Browser.close(Browser.mode === 'zip' && Browser.selection.size
      ? [...Browser.selection]
      : [Browser.current]);
  };
  $('#new-folder-btn').onclick = async () => {
    const name = await Prompt.text('New folder', 'Folder name', false);
    if (!name) return;
    // The folder is actually created when the session starts.
    Browser.close([Browser.current.replace(/\/$/, '') + '/' + name]);
  };
  $$('#browser [data-close]').forEach(b => { b.onclick = () => Browser.close(null); });

  $$('.modal').forEach(modal => modal.addEventListener('click', event => {
    if (event.target !== modal) return;
    if (modal.id === 'browser') Browser.close(null);
    else if (modal.id === 'prompt') Prompt.close(null);
    else modal.classList.add('hidden');   // help, about
  }));
  $$('#help [data-close]').forEach(b => {
    b.onclick = () => $('#help').classList.add('hidden');
  });
}
