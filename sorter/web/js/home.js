/** Landing page: what the app is, and the ways in.
 *
 * Three ways in, and each one carries the state that makes it worth clicking:
 * how far the session got, or what export is already sitting on this machine.
 */

import { $, $$, toast } from './dom.js';
import { Boot } from './boot.js';
import { bytes } from './format.js';
import { Router } from './router.js';
import { Sorter } from './sorter.js';

export const Home = {
  session: null,

  /** Called on every boot; `boot` is the /api/bootstrap payload. */
  update(boot) {
    this.session = boot.session || null;
    const resumable = !!this.session;
    $('#home-continue').classList.toggle('hidden', !resumable);
    $('#home-new').classList.toggle('primary', !resumable);

    if (resumable) {
      const { counts } = this.session;
      const done = counts.done || 0;
      const total = counts.total || 0;
      const left = Math.max(total - done, 0);
      $('#home-resume-info').textContent =
        `${done.toLocaleString('en-GB')} of ${total.toLocaleString('en-GB')} sorted`
        + (left ? ` · ${left.toLocaleString('en-GB')} to go` : ' · all done');
      $('#home-meter').style.width = (total ? done / total * 100 : 0) + '%';
    }

    // Say what is already sitting on this machine — it is the one thing the
    // user would otherwise have to go and look up.
    const group = (boot.suggestions || [])[0];
    $('#home-new-sub').textContent = group
      ? `${group.count} archive${group.count > 1 ? 's' : ''} found `
        + `(${bytes(group.size)}) in ${group.dir.split('/').pop()}`
      : 'Pick your export and start sorting';
  },

  async resume() {
    if (!this.session) return Router.go('setup');
    try {
      if (!this.session.scan.done) return Router.go('scanning');
      await Sorter.enter(this.session);
      Router.go('sorter');
    } catch (e) { toast(e.message, true); }
  },
};

export function initHome() {
  $('#home-new').onclick = () => Router.go('setup');
  $('#home-continue').onclick = () => Home.resume();
  $('#home-about').onclick = () => $('#about').classList.remove('hidden');
  $('#setup-back').onclick = () => Router.go('home');
  $$('#about [data-close]').forEach(b => {
    b.onclick = () => $('#about').classList.add('hidden');
  });
}

/** Re-read the session before deciding what the home screen should offer. */
export async function refreshHome() {
  try {
    Home.update(await Boot.refresh());
  } catch { /* the server will be back on the next visit */ }
}
