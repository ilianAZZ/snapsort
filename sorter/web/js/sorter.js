/** Sorting screen: the memory queue, decisions, progress and summary. */

import { $, $$, isScreenActive, toast } from './dom.js';
import { Router } from './router.js';
import { api } from './api.js';
import { attachDrag, buildCard, renderInfo } from './cards.js';
import { createFolder, flashFolder, renameFolder, renderFolders } from './folders.js';
import { Sound } from './sound.js';

const AHEAD = 8;            // cards kept loaded ahead of the top one
const PAGE = 12;            // memories requested per call
const POLL_MS = 2500;       // refresh interval for counters and errors

// A Snapchat recording is capped at ten seconds, so the next segment of a long
// video starts exactly where the previous one ended. Nothing in the export says
// "this is part 2" — the export carries no such field — so this timing is the
// only signal there is. The ZIP format stores timestamps to the nearest two
// seconds, hence the tolerance.
const CONTINUATION_SLACK_S = 2;
// Without a measured duration we can only say "close enough to be worth
// offering", which lights the button but never triggers an automatic join.
const NEARBY_S = 25;

const SCAN_STEPS = {
  reading: 'Reading the archives…',
  indexing: 'Indexing the memories…',
  metadata: 'Matching the metadata…',
  done: 'Ready!',
};

const LABELS = {
  keep: ['Kept', 'var(--keep)', 'right'],
  trash: ['Discarded', 'var(--trash)', 'left'],
  fav: ['Favourite', 'var(--fav)', 'up'],
  skip: ['Skipped', 'var(--skip)', 'down'],
  merge: ['Joined', 'var(--join)', 'right'],
};

const EXITS = {
  right: 'translateX(130%) rotate(18deg)',
  left: 'translateX(-130%) rotate(-18deg)',
  up: 'translateY(-130%) scale(.9)',
  down: 'translateY(60%) scale(.9)',
};

// Decisions that produce a file a later clip can be appended to.
const JOINABLE = new Set(['keep', 'fav', 'folder', 'merge']);

export const Sorter = {
  session: null,
  buffer: [],         // undecided memories already loaded (the first is the top card)
  scanNext: 0,        // where the server should resume scanning
  exhausted: false,   // nothing left to load
  showOverlay: true,
  busy: false,
  seenErrors: 0,
  watching: false,    // a scan is being polled
  reportLoaded: false,
  topDuration: null,  // measured length of the video on top, for Join detection
  last: null,         // the memory just decided, for the Join action

  /* ─────────── scan ─────────── */

  async watchScan() {
    if (this.watching) return;
    this.watching = true;
    const tick = async () => {
      let session;
      try { session = await api('/api/session'); } catch { return setTimeout(tick, 500); }
      $('#scan-step').textContent = SCAN_STEPS[session.scan.step] || 'Analysing…';
      $('#scan-detail').textContent = session.scan.done
        ? `${session.stats.total} memories found`
        : `${session.scan.files} files scanned`
          + (session.scan.current ? ' — ' + session.scan.current : '');
      if (session.scan.done) {
        this.watching = false;
        await this.enter(session);
        return Router.go('sorter');
      }
      setTimeout(tick, 350);
    };
    tick();
  },

  async enter(session) {
    this.session = session;
    this.scanNext = session.cursor || 0;
    this.buffer = [];
    this.exhausted = false;
    this.last = null;
    this.topDuration = null;
    this.drawFolders();
    await this.fill();
    this.render();
    this.updateProgress();
    this.poll();
  },

  poll() {
    clearInterval(this._poll);
    this._poll = setInterval(async () => {
      if (!isScreenActive('sorter')) return;
      try {
        const snapshot = await api('/api/session');
        this.session.counts = snapshot.counts;
        this.updateProgress();
        this.reportErrors(snapshot.errors || []);
      } catch { /* the server will answer on the next tick */ }
    }, POLL_MS);
  },

  /** Copy errors never interrupt sorting, but the user has to see them. */
  reportErrors(errors) {
    if (errors.length > this.seenErrors) {
      errors.slice(this.seenErrors).forEach(e => toast(`Copy failed — ${e}`, true));
    }
    this.seenErrors = errors.length;
  },

  /* ─────────── queue ─────────── */

  /** Top up the queue to AHEAD cards. */
  async fill() {
    while (this.buffer.length < AHEAD && !this.exhausted) {
      const data = await api(`/api/queue?start=${this.scanNext}&count=${PAGE}`);
      const known = new Set(this.buffer.map(i => i.id));
      data.items.forEach(item => { if (!known.has(item.id)) this.buffer.push(item); });
      if (data.next <= this.scanNext || (!data.items.length && data.next >= data.total)) {
        this.exhausted = true;
      }
      this.scanNext = data.next;
    }
  },

  /** Reload the queue from scratch (after an undo or an order change). */
  async reload(from = 0) {
    this.buffer = [];
    this.scanNext = from;
    this.exhausted = false;
    await this.fill();
  },

  item(offset = 0) {
    return this.buffer[offset] || null;
  },

  /* ─────────── rendering ─────────── */

  render() {
    const holder = $('#cards');
    holder.innerHTML = '';
    const items = [0, 1, 2].map(o => this.item(o)).filter(Boolean);
    if (!items.length) {
      $('#empty').classList.remove('hidden');
      $('#info').innerHTML = '';
      this.updateJoin();
      return;
    }
    $('#empty').classList.add('hidden');
    // The last child is the top card, so stack them in reverse.
    items.slice().reverse().forEach(item => holder.append(buildCard(item, items.indexOf(item), {
      showOverlay: this.showOverlay,
      onMediaReady: (it, mediaEl) => {
        if (it === items[0] && mediaEl.tagName === 'VIDEO' && isFinite(mediaEl.duration)) {
          this.topDuration = mediaEl.duration;
          this.updateJoin();
        }
        renderInfo(it, mediaEl, this.session.counts.total);
      },
    })));
    renderInfo(items[0], null, this.session.counts.total);
    attachDrag($('#cards .card:last-child'), action => this.decide(action));
    Sound.refresh();
    this.updateJoin();
  },

  drawFolders() {
    renderFolders(this.session.folders, {
      onPick: folder => this.decide('folder', folder.id),
      onEdit: folder => this.editFolder(folder),
      onAdd: () => this.newFolder(),
    });
  },

  updateProgress() {
    const counts = this.session.counts || { done: 0, total: 0, pending: 0 };
    $('#pbar-fill').style.width = (counts.total ? counts.done / counts.total * 100 : 0) + '%';
    $('#p-count').textContent = `${counts.done} / ${counts.total}`;
    $('#p-left').textContent = `${Math.max(counts.total - counts.done, 0)} left`;
    $('#p-pending').textContent = counts.pending ? `· ${counts.pending} copy in progress…` : '';
  },

  /* ─────────── joining split videos ─────────── */

  /** Can the card on screen be appended to the video just filed? */
  canJoin() {
    const item = this.item(0);
    return !!(item && item.kind === 'video' && this.last
      && this.last.kind === 'video' && JOINABLE.has(this.last.action));
  },

  /** True when this clip starts exactly where the previous video ended. */
  isContinuation() {
    if (!this.canJoin() || !this.last.duration) return false;
    const expected = this.last.ts + this.last.duration;
    return Math.abs(this.item(0).ts - expected) <= CONTINUATION_SLACK_S;
  },

  updateJoin() {
    const button = $('#btn-join');
    const banner = $('#join-banner');
    const split = $('#btn-split');
    if (!button) return;
    const possible = this.canJoin();
    const sure = this.isContinuation();
    const nearby = possible && this.item(0).ts - this.last.ts < NEARBY_S;
    button.disabled = !possible;
    button.classList.toggle('likely', sure || nearby);
    button.title = sure
      ? 'This clip continues the previous video — join them (J)'
      : 'Append this clip to the end of the previous video (J)';
    banner?.classList.toggle('hidden', !sure);
    split?.classList.toggle('hidden', (this.item(0)?.parts || []).length < 2);
  },

  /**
   * Break the recording on screen back into its clips.
   *
   * Nothing in the export marks a split recording, so grouping is a judgement
   * made from timestamps: this is the way out when it groups two videos that
   * merely follow one another.
   */
  split() {
    const item = this.item(0);
    const parts = item?.parts || [];
    if (parts.length < 2) return toast('This memory is a single clip');
    this.buffer.splice(0, 1, ...parts.map((part, i) => ({ ...part, index: item.index + i })));
    this.render();
    toast(`Sorting the ${parts.length} clips separately`);
  },

  async join() {
    if (this.busy) return;
    const item = this.item(0);
    if (!item) return;
    if (!this.canJoin()) {
      return toast('Keep a video first, then join the next clip to it');
    }
    this.busy = true;
    this.animateExit('merge');
    try {
      const res = await api('/api/merge', { id: item.id });
      this.session.counts = res.counts;
      this.session.folders = res.folders;
      this.last = this.trailOf(item, 'merge');
    } catch (e) {
      toast(e.message, true);
      this.busy = false;
      return this.render();
    }
    await this.advance();
    toast('Joined to the previous video');
  },

  /* ─────────── decisions ─────────── */

  /** What a decision leaves behind for the Join button: its last clip. */
  trailOf(item, action) {
    const parts = item.parts || [item];
    const tail = parts[parts.length - 1];
    // topDuration was measured on the clip on screen, so it only stands in for
    // a card that had a single one.
    return { id: tail.id, kind: item.kind, ts: tail.ts, action,
      duration: tail.duration ?? (parts.length === 1 ? this.topDuration : null) };
  },

  async decide(action, folderId) {
    if (action === 'merge') return this.join();
    if (this.busy) return;
    const item = this.item(0);
    if (!item) return;
    this.busy = true;

    this.animateExit(action, folderId);
    if (folderId) flashFolder(folderId);

    // A split recording travels as one decision: the server files the first
    // clip and joins the rest onto it.
    const parts = (item.parts || []).map(p => p.id);
    try {
      const res = await api('/api/decide',
        { id: item.id, action, folder: folderId || null, parts });
      this.session.counts = res.counts;
      this.session.folders = res.folders;
      const joined = parts.length > 1 && JOINABLE.has(action);
      this.last = this.trailOf(item, joined ? 'merge' : action);
    } catch (e) {
      toast(e.message, true);
      this.busy = false;
      return this.render();
    }
    await this.advance();
  },

  /** Drop the decided card and let the animation finish before redrawing. */
  async advance() {
    this.buffer.shift();
    this.topDuration = null;      // belongs to the card that just left
    await this.fill();
    setTimeout(() => {
      this.render();
      this.drawFolders();
      this.updateProgress();
      this.busy = false;
    }, 170);
  },

  animateExit(action, folderId) {
    const card = $('#cards .card:last-child');
    if (!card) return;
    const folder = (this.session.folders || []).find(f => f.id === folderId);
    const [text, color, direction] = action === 'folder'
      ? [folder?.name || 'Folder', folder?.color || 'var(--accent)', 'right']
      : LABELS[action];
    const stamp = card.querySelector('.stamp');
    stamp.textContent = text;
    stamp.style.color = color;
    stamp.classList.add('show');
    card.classList.add('leaving');
    requestAnimationFrame(() => {
      card.style.transform = EXITS[direction];
      card.style.opacity = '0';
    });
  },

  async undo() {
    if (this.busy) return;
    this.busy = true;
    try {
      const res = await api('/api/undo', {});
      if (!res.ok) {
        toast('Nothing to undo');
        this.busy = false;
        return;
      }
      this.session.counts = res.counts;
      this.session.folders = res.folders;
      // We no longer know what the previous decision was: the server decides.
      this.last = null;
      await this.reload(res.cursor);
      this.render();
      this.drawFolders();
      this.updateProgress();
      toast('Decision undone');
    } catch (e) { toast(e.message, true); }
    this.busy = false;
  },

  /* ─────────── folders ─────────── */

  async newFolder() {
    try {
      const res = await createFolder(this.session.folders);
      if (!res) return;
      this.session.folders = res.folders;
      this.drawFolders();
      toast(`Folder “${res.folder.name}” — key ${res.folder.key || '—'}`);
    } catch (e) { toast(e.message, true); }
  },

  async editFolder(folder) {
    try {
      const res = await renameFolder(folder, this.session.folders);
      if (!res) return;
      this.session.folders = res.folders;
      this.drawFolders();
    } catch (e) { toast(e.message, true); }
  },

  /* ─────────── playback ─────────── */

  togglePlay() {
    const video = $('#cards .card:last-child video.on');
    if (!video) return;
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  },

  toggleSound() {
    toast(Sound.toggle() ? 'Sound off' : 'Sound on');
  },

  toggleOverlay() {
    this.showOverlay = !this.showOverlay;
    this.render();
    toast(this.showOverlay ? 'Overlays shown' : 'Overlays hidden');
  },

  /* ─────────── finishing ─────────── */

  async finish() {
    let report;
    try { report = await api('/api/report'); } catch (e) { return toast(e.message, true); }
    const counts = this.session.counts;
    this.reportLoaded = true;
    Router.go('done');
    $('#done-sub').textContent = `${counts.done} of ${counts.total} memories handled`;
    $('#done-stats').innerHTML = `
      <div class="stat"><b>${counts.keep + counts.fav}</b><span>kept</span></div>
      <div class="stat"><b>${counts.fav}</b><span>favourites</span></div>
      <div class="stat"><b>${counts.merge || 0}</b><span>clips joined</span></div>
      <div class="stat"><b>${counts.trash}</b><span>discarded</span></div>
      <div class="stat"><b>${counts.total - counts.done}</b><span>remaining</span></div>`;
    $('#done-report').textContent = report.markdown;
    $('#btn-open-dest').onclick = () => api('/api/reveal', { path: this.session.dest });
    $('#btn-replay').classList.toggle('hidden', !counts.skip);
    $('#btn-replay').textContent = `Review the ${counts.skip} skipped`;
  },

  async replaySkipped() {
    const res = await api('/api/replay', { action: 'skip' });
    if (!res.count) return toast('No skipped memories');
    this.session.counts = res.counts;
    this.session.folders = res.folders;
    this.last = null;
    await this.reload(res.cursor);
    Router.go('sorter');
    this.render();
    this.drawFolders();
    this.updateProgress();
    toast(`${res.count} memories back in the queue`);
  },
};

export function initSorter() {
  $('#btn-undo').onclick = () => Sorter.undo();
  $('#btn-sound').onclick = () => Sorter.toggleSound();
  $('#btn-help').onclick = () => $('#help').classList.toggle('hidden');
  $('#btn-finish').onclick = () => Sorter.finish();
  $('#btn-finish2').onclick = () => Sorter.finish();
  $('#btn-back').onclick = () => Router.go('sorter');
  $('#btn-replay').onclick = () => Sorter.replaySkipped();
  $('#btn-join').onclick = () => Sorter.join();
  $('#join-now').onclick = () => Sorter.join();
  $('#btn-split').onclick = () => Sorter.split();
  $$('.act[data-action]').forEach(b => { b.onclick = () => Sorter.decide(b.dataset.action); });
}
