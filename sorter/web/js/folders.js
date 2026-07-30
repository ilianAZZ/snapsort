/** Custom folders: the dock strip, plus creating and renaming. */

import { $, escapeHtml } from './dom.js';
import { api } from './api.js';
import { Prompt } from './prompt.js';

export function renderFolders(folders, { onPick, onEdit, onAdd }) {
  const box = $('#folders');
  box.innerHTML = '';
  (folders || []).forEach(folder => {
    const button = document.createElement('button');
    button.className = 'folder';
    button.dataset.id = folder.id;
    button.innerHTML = `<span class="k" style="background:${escapeHtml(folder.color)}">
        ${escapeHtml(folder.key || '·')}</span>
      <span class="n">${escapeHtml(folder.name)}</span>
      <span class="c">${folder.count || 0}</span>`;
    button.onclick = () => onPick(folder);
    button.oncontextmenu = event => { event.preventDefault(); onEdit(folder); };
    box.append(button);
  });
  const add = document.createElement('button');
  add.className = 'folder add';
  add.innerHTML = '<span>＋ New folder</span><span class="c">N</span>';
  add.onclick = onAdd;
  box.append(add);
}

const usedKeys = (folders, except) => (folders || [])
  .map(f => f.key)
  .filter(key => key && key !== except);

/** Ask for a name, then create the folder. Returns the server reply, or null. */
export async function createFolder(folders) {
  const answer = await Prompt.text('New folder', 'Holidays, Family, Best of…',
    true, usedKeys(folders));
  if (!answer) return null;
  return api('/api/folders', { name: answer.name, key: answer.key });
}

export async function renameFolder(folder, folders) {
  const answer = await Prompt.text(`Rename “${folder.name}”`, folder.name,
    true, usedKeys(folders, folder.key));
  if (!answer) return null;
  return api('/api/folders/update', { id: folder.id, name: answer.name, key: answer.key });
}

export function flashFolder(id) {
  const chip = $(`#folders .folder[data-id="${id}"]`);
  if (!chip) return;
  chip.classList.add('flash');
  setTimeout(() => chip.classList.remove('flash'), 400);
}
