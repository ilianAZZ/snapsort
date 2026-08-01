/** Building the cards, the info panel and the drag gesture. */

import { $, el } from './dom.js';
import { mediaUrl } from './api.js';
import { bytes, cap, fmtDate, fmtDuration, fmtTime } from './format.js';
import { Sound } from './sound.js';

const SWIPE = { commit: 110, hint: 70, down: 130 };  // thresholds in pixels

/**
 * One card of the stack. `depth` is 0 for the top one: only that card plays
 * its video and reports its dimensions to the info panel.
 */
export function buildCard(item, depth, { showOverlay, onMediaReady }) {
  const card = el('div', {
    class: 'card' + (depth === 1 ? ' behind' : depth === 2 ? ' behind2' : ''),
  });
  card.dataset.id = item.id;

  const media = el('div', { class: 'media' });
  const src = mediaUrl(item.id, 'main');
  const blur = el('div', { class: 'blur' });
  if (item.kind === 'image') blur.style.backgroundImage = `url("${src}")`;
  media.append(blur);

  media.append(item.kind === 'video'
    ? buildVideo(item, depth, card, blur, onMediaReady)
    : buildImage(item, depth, src, onMediaReady));

  if (item.overlay && showOverlay) {
    media.append(el('img', { class: 'ov', src: mediaUrl(item.id, 'overlay') }));
  }
  const segments = (item.parts || []).length;
  media.append(el('div', {
    class: 'badge-kind',
    text: (item.kind === 'video' ? '🎬 Video' : '📷 Photo')
      + (segments > 1 ? ` · one recording, ${segments} clips` : '')
      + (item.overlay ? ' · overlay' : ''),
  }));
  media.append(el('div', { class: 'stamp' }));

  card.append(media);
  return card;
}

function buildImage(item, depth, src, onMediaReady) {
  const img = el('img', { class: 'main', src, decoding: 'async' });
  if (depth === 0) img.addEventListener('load', () => onMediaReady(item, img));
  return img;
}

/**
 * A recording, not a clip. Snapchat cuts anything long into ten-second pieces;
 * when the queue hands them over as one card they play back to back here, so
 * the whole video is watched before it is decided. Two video elements take
 * turns: one on screen, the other already buffering the piece after it.
 */
function buildVideo(item, depth, card, blur, onMediaReady) {
  const parts = item.parts || [item];
  const fragment = document.createDocumentFragment();

  const make = index => {
    const video = el('video', { class: 'main', src: mediaUrl(parts[index].id, 'main') });
    video.playsInline = true;                      // required on iOS
    video.preload = depth === 0 ? 'auto' : 'metadata';
    video.muted = true;
    video.dataset.part = index;
    return video;
  };

  const first = make(0);
  first.classList.add('on');
  first.loop = parts.length === 1;                 // a lone clip repeats on its own
  first.muted = depth !== 0 || Sound.muted;
  fragment.append(first);
  if (depth !== 0) return fragment;

  const buffers = [first];
  if (parts.length > 1) buffers.push(make(1));
  if (buffers[1]) fragment.append(buffers[1]);

  // Server-side lengths, read from the file headers; the browser refines each
  // one as it loads, which matters for the last piece (never measured server
  // side) and for a clip whose header lied.
  const lengths = parts.map(p => p.duration || 0);
  const sum = (upto = lengths.length) => lengths.slice(0, upto).reduce((a, b) => a + b, 0);
  const progress = el('div', { class: 'vprog', html: '<i></i>' });
  parts.slice(0, -1).forEach((_p, i) => {
    if (sum()) {
      progress.append(el('span', { class: 'tick', style: `left:${sum(i + 1) / sum() * 100}%` }));
    }
  });

  let slot = 0;    // buffer on screen
  let at = 0;      // segment it holds

  const paint = () => {
    const bar = progress.querySelector('i');
    if (!bar || !sum()) return;
    bar.style.width = `${Math.min((sum(at) + buffers[slot].currentTime) / sum(), 1) * 100}%`;
  };

  const load = (video, index) => {
    if (Number(video.dataset.part) === index) return;
    video.dataset.part = index;
    video.src = mediaUrl(parts[index].id, 'main');
  };

  const advance = () => {
    const next = (at + 1) % parts.length;
    const other = buffers.length > 1 ? 1 - slot : 0;
    load(buffers[other], next);
    buffers[slot].classList.remove('on');
    buffers[slot].pause();
    buffers[other].classList.add('on');
    buffers[other].currentTime = 0;
    slot = other;
    at = next;
    Sound.start(buffers[slot]).then(() => Sound.refresh());
    if (buffers.length > 1) load(buffers[1 - slot], (next + 1) % parts.length);
    card.querySelector('.part-of')?.replaceChildren(`Clip ${at + 1} of ${parts.length}`);
  };

  buffers.forEach(video => {
    video.addEventListener('loadedmetadata', () => {
      if (isFinite(video.duration)) lengths[Number(video.dataset.part)] = video.duration;
    });
    video.addEventListener('timeupdate', paint);
    if (parts.length > 1) {
      video.addEventListener('ended', () => { if (video === buffers[slot]) advance(); });
    }
  });
  first.addEventListener('loadedmetadata', () => onMediaReady(item, first), { once: true });
  // Blurred backdrop: a single still grabbed from the first frame.
  first.addEventListener('loadeddata', () => paintBlur(first, blur), { once: true });

  const hint = el('div', {
    class: 'sound-hint hidden',
    text: '🔇 Enable sound',
    onclick: event => { event.stopPropagation(); Sound.resume(); },
  });
  fragment.append(progress, hint);
  if (parts.length > 1) {
    fragment.append(el('div', { class: 'part-of', text: `Clip 1 of ${parts.length}` }));
  }

  Sound.start(first).then(() => Sound.refresh());
  return fragment;
}

function paintBlur(video, blur) {
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 48;
    canvas.height = Math.max(1, Math.round(48 * video.videoHeight / (video.videoWidth || 1)));
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    blur.style.backgroundImage = `url("${canvas.toDataURL('image/jpeg', 0.6)}")`;
  } catch { /* frame not available: plain black backdrop */ }
}

/** OpenStreetMap link — a plain anchor, only followed if you click it. */
function gpsLink(lat, lon) {
  const url = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}`
    + `#map=16/${lat}/${lon}`;
  return `<a class="gps" target="_blank" rel="noreferrer" href="${url}">
      📍 ${lat.toFixed(4)}, ${lon.toFixed(4)}</a>`;
}

/** Side panel: date, time, resolution, location. */
export function renderInfo(item, mediaEl, total) {
  if (!item) return;
  const parts = item.parts || [];
  let dims = '';
  let duration = '';
  if (mediaEl?.tagName === 'VIDEO') {
    dims = `${mediaEl.videoWidth}×${mediaEl.videoHeight}`;
    // A split recording is one video: show how long the whole thing runs.
    duration = fmtDuration(parts.length > 1 ? item.duration : mediaEl.duration);
  } else if (mediaEl) {
    dims = `${mediaEl.naturalWidth}×${mediaEl.naturalHeight}`;
  }
  const size = parts.length > 1
    ? parts.reduce((total, p) => total + (p.size || 0), 0)
    : item.size;
  const gps = item.lat != null ? gpsLink(item.lat, item.lon) : '';
  $('#info').innerHTML = `
    <div class="date">${cap(fmtDate(item.ts))}</div>
    <div class="meta">
      <div class="kv"><span>Time</span><b>${fmtTime(item.ts)}</b></div>
      <div class="kv"><span>Type</span><b>${item.kind === 'video' ? 'Video' : 'Photo'}</b></div>
      ${duration ? `<div class="kv"><span>Length</span><b>${duration}</b></div>` : ''}
      ${parts.length > 1 ? `<div class="kv"><span>Clips</span><b>${parts.length}</b></div>` : ''}
      ${dims ? `<div class="kv"><span>Resolution</span><b>${dims}</b></div>` : ''}
      <div class="kv"><span>Size</span><b>${bytes(size)}</b></div>
      <div class="kv"><span>Position</span><b>${(item.index ?? 0) + 1} / ${total}</b></div>
    </div>
    ${gps}`;
}

/** Drag the top card: right keeps, left discards, up favourites, down skips. */
export function attachDrag(card, onDecide) {
  if (!card) return;
  const stamp = card.querySelector('.stamp');
  let startX = 0;
  let startY = 0;
  let dragging = false;

  const down = event => {
    if (event.target.closest('a, .sound-hint')) return;
    dragging = true;
    startX = event.clientX;
    startY = event.clientY;
    card.setPointerCapture(event.pointerId);
    card.classList.remove('settle');
  };

  const move = event => {
    if (!dragging) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    card.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx / 22}deg)`;
    const horizontal = Math.abs(dx) > Math.abs(dy);
    const strong = horizontal ? Math.abs(dx) > SWIPE.hint : dy < -SWIPE.hint;
    if (!strong) return stamp.classList.remove('show');
    stamp.textContent = horizontal ? (dx > 0 ? 'Keep' : 'Discard') : 'Favourite';
    stamp.style.color = horizontal ? (dx > 0 ? 'var(--keep)' : 'var(--trash)') : 'var(--fav)';
    stamp.classList.add('show');
  };

  const up = event => {
    if (!dragging) return;
    dragging = false;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    const horizontal = Math.abs(dx) > Math.abs(dy);
    if (horizontal && Math.abs(dx) > SWIPE.commit) return onDecide(dx > 0 ? 'keep' : 'trash');
    if (!horizontal && dy < -SWIPE.commit) return onDecide('fav');
    if (!horizontal && dy > SWIPE.down) return onDecide('skip');
    stamp.classList.remove('show');
    card.classList.add('settle');
    card.style.transform = '';
  };

  card.addEventListener('pointerdown', down);
  card.addEventListener('pointermove', move);
  card.addEventListener('pointerup', up);
  card.addEventListener('pointercancel', up);
}
