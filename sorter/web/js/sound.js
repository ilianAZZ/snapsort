/** Playing videos with sound.
 *
 * Browsers refuse to start an unmuted video until the page has been interacted
 * with. In practice clicking "Scan and start" or "Resume" is enough, but not in
 * autostart mode (`--source/--dest`), where sorting opens straight away. So we
 * try with sound, and if the browser says no we fall back to a muted start and
 * say so: the first click restores it.
 */

import { $, $$ } from './dom.js';

const STORAGE_KEY = 'sorter.sound';

export const Sound = {
  muted: localStorage.getItem(STORAGE_KEY) === 'off',
  blocked: false,        // the browser forced silence on us

  /** Start the video, with sound when that is allowed. */
  async start(video) {
    video.muted = this.muted;
    try {
      await video.play();
      if (!this.muted) this.setBlocked(false);
    } catch {
      video.muted = true;
      try { await video.play(); } catch { /* tab in the background */ }
      if (!this.muted) this.setBlocked(true);
    }
  },

  setBlocked(value) {
    if (this.blocked === value) return;
    this.blocked = value;
    this.refresh();
  },

  /** Toggle sound — the M key or the button in the top bar. */
  toggle() {
    this.muted = !this.muted;
    localStorage.setItem(STORAGE_KEY, this.muted ? 'off' : 'on');
    if (!this.muted) this.resume();
    else $$('#cards video').forEach(v => { v.muted = true; });
    this.refresh();
    return this.muted;
  },

  /** Give the sound back to the top card after an unblock or a re-enable. */
  resume() {
    // `.on` is the clip actually on screen: a split recording keeps the next
    // one loaded beside it.
    const video = $('#cards .card:last-child video.on');
    if (!video) return;
    video.muted = false;
    video.play().then(() => this.setBlocked(false)).catch(() => {
      video.muted = true;
      this.setBlocked(true);
    });
  },

  refresh() {
    const btn = $('#btn-sound');
    if (btn) {
      btn.textContent = this.muted || this.blocked ? '🔇' : '🔊';
      btn.classList.toggle('muted', this.muted);
      btn.classList.toggle('blocked', this.blocked && !this.muted);
      btn.title = this.blocked && !this.muted
        ? 'Sound blocked by the browser — click to enable it (M)'
        : this.muted ? 'Sound off (M)' : 'Sound on (M)';
    }
    $$('#cards .sound-hint').forEach(hint => hint.classList.toggle('hidden',
      this.muted || !this.blocked));
  },

  /** The user's first gesture lifts the block imposed at load time. */
  watchForUnlock() {
    const unlock = () => { if (this.blocked && !this.muted) this.resume(); };
    document.addEventListener('pointerdown', unlock, { capture: true });
    document.addEventListener('keydown', unlock, { capture: true });
    this.refresh();
  },
};
