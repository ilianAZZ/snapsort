/** Startup wizard: sources, destination, settings. */

import { $, escapeHtml, toast } from './dom.js';
import { Boot } from './boot.js';
import { Router } from './router.js';
import { api } from './api.js';
import { bytes } from './format.js';
import { Browser } from './browser.js';
import { Sorter } from './sorter.js';

export const Setup = {
  sources: [],
  dest: '',

  /** `boot` is the /api/bootstrap payload, already fetched by main.js. */
  init(boot) {
    const session = boot.session;
    if (session) {
      $('#resume-card').classList.remove('hidden');
      $('#resume-info').textContent =
        `${session.counts.done} / ${session.counts.total} sorted — ${session.dest}`;
      $('#resume-btn').onclick = async () => {
        if (session.scan.done) {
          await Sorter.enter(session);
          Router.go('sorter');
        } else {
          Router.go('scanning');
        }
      };
    }
    this.renderSuggestions(boot.suggestions || []);
    this.refresh();
  },

  renderSuggestions(groups) {
    const box = $('#suggestions');
    box.innerHTML = '';
    groups.forEach(group => {
      const button = document.createElement('button');
      button.className = 'sugg';
      button.innerHTML = `<span class="zi">🗜️</span>
        <span><b>${group.count} archive${group.count > 1 ? 's' : ''} — ${bytes(group.size)}</b>
        <small>${escapeHtml(group.dir)}</small></span>`;
      button.onclick = () => this.setSources(group.files);
      box.append(button);
    });
    if (!box.children.length) {
      box.innerHTML = `<p class="dim tiny">No export found automatically in
        Desktop / Downloads / Documents. Use “Browse”.</p>`;
    }
  },

  setSources(list) {
    this.sources = [...new Set(list)];
    this.refresh();
  },

  refresh() {
    const n = this.sources.length;
    $('#source-summary').textContent = n
      ? `${n} source${n > 1 ? 's' : ''} selected`
      : 'No source selected';

    const chips = $('#source-chips');
    chips.innerHTML = '';
    this.sources.forEach(path => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.innerHTML = `<span>${escapeHtml(path.split('/').pop())}</span>`;
      const remove = document.createElement('button');
      remove.textContent = '✕';
      remove.onclick = () => {
        this.sources = this.sources.filter(s => s !== path);
        this.refresh();
      };
      chip.append(remove);
      chips.append(chip);
    });

    $('#dest-summary').textContent = this.dest || 'No destination';
    $('.step[data-step="1"]').classList.toggle('ok', n > 0);
    $('.step[data-step="2"]').classList.toggle('ok', !!this.dest);

    // Sorting into a source would make us re-read what we have just filed.
    const inside = this.dest && this.sources.some(src =>
      this.dest.startsWith(src.replace(/\.zip$/i, '')) || src.startsWith(this.dest + '/'));
    $('#dest-warn').classList.toggle('hidden', !inside);
    if (inside) {
      $('#dest-warn').textContent =
        '⚠︎ The destination sits inside a source. Pick a separate folder instead.';
    }
    $('#start-btn').disabled = !(n > 0 && this.dest && !inside);
  },

  options() {
    return {
      layout: $('#opt-layout').value,
      order: $('#opt-order').value,
      naming: $('#opt-naming').value,
      trash: $('#opt-trash').value,
      keep_overlay: $('#opt-overlay').checked,
      embed_metadata: $('#opt-metadata').checked,
      auto_join: $('#opt-autojoin').checked,
    };
  },
};

export function initSetup() {
  $('#pick-source').onclick = async () => {
    const chosen = await Browser.open('zip', Setup.sources[0] || '');
    if (chosen) Setup.setSources([...Setup.sources, ...chosen]);
  };
  $('#pick-dest').onclick = async () => {
    const chosen = await Browser.open('dir', Setup.dest || '');
    if (chosen && chosen[0]) {
      Setup.dest = chosen[0];
      Setup.refresh();
    }
  };
  $('#start-btn').onclick = async () => {
    $('#start-btn').disabled = true;
    try {
      await api('/api/session/start', {
        sources: Setup.sources, dest: Setup.dest, options: Setup.options(),
      });
      await Boot.refresh();          // the session exists now; the router checks
      Router.go('scanning');
    } catch (e) {
      toast(e.message, true);
      $('#start-btn').disabled = false;
    }
  };
}
