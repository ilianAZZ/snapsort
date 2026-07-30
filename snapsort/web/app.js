/* ══════════════════════════════════════════════════════════════════
   SnapSort — interface
   ══════════════════════════════════════════════════════════════════ */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Erreur ${res.status}`);
  return data;
}

function toast(msg, err) {
  const el = document.createElement('div');
  el.className = 'toast' + (err ? ' err' : '');
  el.textContent = msg;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), err ? 4200 : 1900);
}

const bytes = n => {
  if (!n) return '0 o';
  const u = ['o', 'Ko', 'Mo', 'Go', 'To'];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), 4);
  return (n / 1024 ** i).toFixed(i ? 1 : 0) + ' ' + u[i];
};
const fmtDate = ts => new Date(ts * 1000).toLocaleDateString('fr-FR',
  { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString('fr-FR',
  { hour: '2-digit', minute: '2-digit' });
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);

function showScreen(id) {
  $$('.screen').forEach(s => s.classList.toggle('active', s.id === id));
}

/* ════════════════════════ sélecteur de fichiers ════════════════════════ */

const Browser = {
  mode: 'dir', selection: new Set(), current: '', resolve: null,

  open(mode, startPath) {
    this.mode = mode;
    this.selection = new Set();
    this.resolve = null;
    $('#browser-title').textContent = mode === 'zip'
      ? 'Sélectionner les archives (.zip) ou un dossier'
      : 'Choisir le dossier de destination';
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

  async go(path) {
    let data;
    try { data = await api('/api/browse?path=' + encodeURIComponent(path || '')); }
    catch (e) { return toast(e.message, true); }
    this.current = data.path;
    this.data = data;

    const crumbs = $('#crumbs');
    crumbs.innerHTML = '';
    const parts = data.path.split('/').filter(Boolean);
    const mk = (label, target) => {
      const b = document.createElement('button');
      b.textContent = label;
      b.onclick = () => this.go(target);
      crumbs.append(b);
    };
    mk('/', '/');
    let acc = '';
    parts.forEach((p, i) => {
      acc += '/' + p;
      const sep = document.createElement('span');
      sep.textContent = '›';
      if (i) crumbs.append(sep);
      mk(p, acc);
    });

    const list = $('#listing');
    list.innerHTML = '';
    if (data.parent) {
      list.append(this.row('↰', '..', () => this.go(data.parent)));
    }
    data.dirs.forEach(d => {
      const row = this.row('📁', d.name, () => this.go(d.path));
      if (this.mode === 'zip') {
        const use = document.createElement('small');
        use.textContent = 'utiliser ce dossier';
        use.style.cursor = 'pointer';
        use.onclick = e => { e.stopPropagation(); this.close([d.path]); };
        row.append(use);
      }
      list.append(row);
    });
    if (this.mode === 'zip') {
      data.zips.forEach(z => {
        const row = this.row('🗜️', z.name, () => this.toggle(z.path, row));
        const s = document.createElement('small');
        s.textContent = bytes(z.size);
        row.append(s);
        row.classList.toggle('sel', this.selection.has(z.path));
        list.append(row);
      });
      if (data.zips.length > 1) {
        const all = this.row('✓', `Tout sélectionner (${data.zips.length} archives)`, () => {
          data.zips.forEach(z => this.selection.add(z.path));
          this.go(this.current);
        });
        list.prepend(all);
      }
    }
    this.updateHint();
  },

  row(icon, label, onClick) {
    const b = document.createElement('button');
    b.className = 'entry';
    b.innerHTML = `<span class="ic">${icon}</span><span>${label.replace(/</g, '&lt;')}</span>`;
    b.onclick = onClick;
    return b;
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
        ? `${this.selection.size} archive(s) sélectionnée(s)`
        : 'Coche des .zip, ou valide pour utiliser le dossier courant';
    } else {
      const d = this.data || {};
      hint.textContent = d.writable === false
        ? '⚠︎ dossier non accessible en écriture'
        : `${bytes(d.free || 0)} libres`;
    }
  },
};

$('#browser-ok').onclick = () => {
  if (Browser.mode === 'zip') {
    Browser.close(Browser.selection.size ? [...Browser.selection] : [Browser.current]);
  } else {
    Browser.close([Browser.current]);
  }
};
$('#new-folder-btn').onclick = async () => {
  const name = await Prompt.text('Nouveau dossier', 'Nom du dossier', false);
  if (!name) return;
  // Le dossier est réellement créé au démarrage de la session.
  Browser.close([Browser.current.replace(/\/$/, '') + '/' + name]);
};
$$('#browser [data-close]').forEach(b => b.onclick = () => Browser.close(null));

/* ════════════════════════ modale de saisie ════════════════════════ */

const Prompt = {
  resolve: null, withKeys: false, key: null,

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
    if (withKeys) {
      '1234567890'.split('').forEach(d => {
        const b = document.createElement('button');
        b.textContent = d;
        b.disabled = usedKeys.includes(d);
        b.onclick = () => {
          this.key = this.key === d ? null : d;
          $$('#prompt-keys button').forEach(x => x.classList.toggle('on', x.textContent === this.key));
        };
        row.append(b);
      });
      const free = '1234567890'.split('').find(d => !usedKeys.includes(d));
      if (free) {
        this.key = free;
        $$('#prompt-keys button').forEach(x => x.classList.toggle('on', x.textContent === free));
      }
    }
    $('#prompt').classList.remove('hidden');
    setTimeout(() => input.focus(), 50);
    return new Promise(r => { this.resolve = r; });
  },

  close(value) {
    $('#prompt').classList.add('hidden');
    if (this.resolve) this.resolve(value);
    this.resolve = null;
  },
};

$('#prompt-ok').onclick = () => {
  const v = $('#prompt-input').value.trim();
  if (!v) return;
  Prompt.close(Prompt.withKeys ? { name: v, key: Prompt.key } : v);
};
$('#prompt-input').onkeydown = e => {
  e.stopPropagation();
  if (e.key === 'Enter') $('#prompt-ok').click();
  if (e.key === 'Escape') Prompt.close(null);
};
$$('#prompt [data-close]').forEach(b => b.onclick = () => Prompt.close(null));
$$('#help [data-close]').forEach(b => b.onclick = () => $('#help').classList.add('hidden'));
$$('.modal').forEach(m => m.addEventListener('click', e => {
  if (e.target === m) {
    if (m.id === 'browser') Browser.close(null);
    else if (m.id === 'prompt') Prompt.close(null);
    else m.classList.add('hidden');
  }
}));

/* ════════════════════════ assistant de démarrage ════════════════════════ */

const Setup = {
  sources: [], dest: '',

  async init() {
    let boot;
    try { boot = await api('/api/bootstrap'); }
    catch { return toast('Impossible de contacter le serveur local', true); }

    if (boot.session) {
      const s = boot.session;
      // Lancé avec --source/--dest : les choix sont déjà faits, on va au tri.
      if (boot.autostart) return s.scan.done ? Sorter.enter(s) : Sorter.watchScan();
      $('#resume-card').classList.remove('hidden');
      $('#resume-info').textContent =
        `${s.counts.done} / ${s.counts.total} triés — ${s.dest}`;
      $('#resume-btn').onclick = () => Sorter.enter(s);
      if (!s.scan.done) Sorter.watchScan();
    }

    const box = $('#suggestions');
    box.innerHTML = '';
    (boot.suggestions || []).forEach(g => {
      const b = document.createElement('button');
      b.className = 'sugg';
      b.innerHTML = `<span class="zi">🗜️</span>
        <span><b>${g.count} archive${g.count > 1 ? 's' : ''} — ${bytes(g.size)}</b>
        <small>${g.dir}</small></span>`;
      b.onclick = () => this.setSources(g.files);
      box.append(b);
    });
    if (!box.children.length) {
      box.innerHTML = `<p class="dim tiny">Aucun export détecté automatiquement dans
        Bureau / Téléchargements / Documents. Utilise « Parcourir ».</p>`;
    }
    this.refresh();
  },

  setSources(list) {
    this.sources = [...new Set(list)];
    this.refresh();
  },

  refresh() {
    const n = this.sources.length;
    $('#source-summary').textContent = n
      ? `${n} source${n > 1 ? 's' : ''} sélectionnée${n > 1 ? 's' : ''}`
      : 'Aucune source sélectionnée';
    const chips = $('#source-chips');
    chips.innerHTML = '';
    this.sources.forEach(p => {
      const c = document.createElement('span');
      c.className = 'chip';
      c.innerHTML = `<span>${p.split('/').pop()}</span>`;
      const x = document.createElement('button');
      x.textContent = '✕';
      x.onclick = () => { this.sources = this.sources.filter(s => s !== p); this.refresh(); };
      c.append(x);
      chips.append(c);
    });
    $('#dest-summary').textContent = this.dest || 'Aucune destination';

    $('.step[data-step="1"]').classList.toggle('ok', n > 0);
    $('.step[data-step="2"]').classList.toggle('ok', !!this.dest);

    const inside = this.dest && this.sources.some(s =>
      this.dest.startsWith(s.replace(/\.zip$/i, '')) || s.startsWith(this.dest + '/'));
    $('#dest-warn').classList.toggle('hidden', !inside);
    if (inside) $('#dest-warn').textContent =
      "⚠︎ La destination est à l'intérieur d'une source. Choisis plutôt un dossier séparé.";

    $('#start-btn').disabled = !(n > 0 && this.dest && !inside);
  },

  options() {
    return {
      layout: $('#opt-layout').value,
      order: $('#opt-order').value,
      naming: $('#opt-naming').value,
      trash: $('#opt-trash').value,
      keep_overlay: $('#opt-overlay').checked,
    };
  },
};

$('#pick-source').onclick = async () => {
  const r = await Browser.open('zip', Setup.sources[0] || '');
  if (r) Setup.setSources([...Setup.sources, ...r]);
};
$('#pick-dest').onclick = async () => {
  const r = await Browser.open('dir', Setup.dest || '');
  if (r && r[0]) { Setup.dest = r[0]; Setup.refresh(); }
};
$('#start-btn').onclick = async () => {
  $('#start-btn').disabled = true;
  try {
    await api('/api/session/start', {
      sources: Setup.sources, dest: Setup.dest, options: Setup.options(),
    });
    showScreen('scanning');
    Sorter.watchScan();
  } catch (e) {
    toast(e.message, true);
    $('#start-btn').disabled = false;
  }
};

/* ════════════════════════ écran de tri ════════════════════════ */

const Sorter = {
  session: null,
  buffer: [],       // souvenirs non triés déjà chargés (le 1er est la carte du dessus)
  scanNext: 0,      // position de reprise du balayage côté serveur
  exhausted: false, // plus rien à charger
  muted: true,
  showOverlay: true,
  busy: false,

  async watchScan() {
    showScreen('scanning');
    const tick = async () => {
      let s;
      try { s = await api('/api/session'); } catch { return setTimeout(tick, 500); }
      $('#scan-step').textContent = {
        lecture: 'Lecture des archives…',
        indexation: 'Indexation des souvenirs…',
        'métadonnées': 'Association des métadonnées…',
        'terminé': 'Prêt !',
      }[s.scan.step] || 'Analyse…';
      $('#scan-detail').textContent = s.scan.done
        ? `${s.stats.total} souvenirs trouvés`
        : `${s.scan.files} fichiers parcourus${s.scan.current ? ' — ' + s.scan.current : ''}`;
      if (s.scan.done) return this.enter(s);
      setTimeout(tick, 350);
    };
    tick();
  },

  async enter(session) {
    this.session = session;
    this.scanNext = session.cursor || 0;
    this.buffer = [];
    this.exhausted = false;
    showScreen('sorter');
    this.renderFolders();
    await this.fill();
    this.render();
    this.updateProgress();
    this.poll();
  },

  poll() {
    clearInterval(this._poll);
    this._poll = setInterval(async () => {
      if ($('#sorter').classList.contains('active') === false) return;
      try {
        const s = await api('/api/session');
        this.session.counts = s.counts;
        this.updateProgress();
        (s.errors || []).forEach(() => {});
      } catch { /* ignore */ }
    }, 2500);
  },

  /** Complète la file jusqu'à ~8 cartes d'avance. */
  async fill() {
    while (this.buffer.length < 8 && !this.exhausted) {
      const data = await api(`/api/queue?start=${this.scanNext}&count=12`);
      const known = new Set(this.buffer.map(i => i.id));
      data.items.forEach(i => { if (!known.has(i.id)) this.buffer.push(i); });
      if (data.next <= this.scanNext || (!data.items.length && data.next >= data.total)) {
        this.exhausted = true;
      }
      this.scanNext = data.next;
    }
  },

  /** Recharge la file depuis zéro (après annulation ou changement d'ordre). */
  async reload(from = 0) {
    this.buffer = [];
    this.scanNext = from;
    this.exhausted = false;
    await this.fill();
  },

  item(offset = 0) {
    return this.buffer[offset] || null;
  },

  /* ─────────── rendu des cartes ─────────── */

  render() {
    const holder = $('#cards');
    holder.innerHTML = '';
    const items = [0, 1, 2].map(o => this.item(o)).filter(Boolean);
    if (!items.length) {
      $('#empty').classList.remove('hidden');
      $('#info').innerHTML = '';
      return;
    }
    $('#empty').classList.add('hidden');
    items.slice().reverse().forEach(it => {
      const depth = items.indexOf(it);
      holder.append(this.buildCard(it, depth));
    });
    this.renderInfo(items[0]);
    this.attachDrag();
  },

  buildCard(item, depth) {
    const card = document.createElement('div');
    card.className = 'card' + (depth === 1 ? ' behind' : depth === 2 ? ' behind2' : '');
    card.dataset.id = item.id;

    const media = document.createElement('div');
    media.className = 'media';
    const src = `/api/media/${encodeURIComponent(item.id)}/main`;

    const blur = document.createElement('div');
    blur.className = 'blur';
    if (item.kind === 'image') blur.style.backgroundImage = `url("${src}")`;
    media.append(blur);

    if (item.kind === 'video') {
      const v = document.createElement('video');
      v.className = 'main';
      v.src = src;
      v.muted = this.muted;
      v.loop = true;
      v.autoplay = depth === 0;
      v.playsInline = true;
      v.preload = depth === 0 ? 'auto' : 'metadata';
      if (depth === 0) {
        v.addEventListener('loadedmetadata', () => this.renderInfo(item, v));
        // Fond flouté : une seule image capturée à la première frame.
        v.addEventListener('loadeddata', () => {
          try {
            const c = document.createElement('canvas');
            c.width = 48;
            c.height = Math.max(1, Math.round(48 * v.videoHeight / (v.videoWidth || 1)));
            c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
            blur.style.backgroundImage = `url("${c.toDataURL('image/jpeg', 0.6)}")`;
          } catch { /* frame indisponible : fond noir */ }
        }, { once: true });
        v.addEventListener('timeupdate', () => {
          const bar = card.querySelector('.vprog i');
          if (bar && v.duration) bar.style.width = (v.currentTime / v.duration * 100) + '%';
        });
        v.play?.().catch(() => {});
      }
      media.append(v);
      const p = document.createElement('div');
      p.className = 'vprog';
      p.innerHTML = '<i></i>';
      media.append(p);
    } else {
      const img = document.createElement('img');
      img.className = 'main';
      img.src = src;
      img.decoding = 'async';
      if (depth === 0) img.addEventListener('load', () => this.renderInfo(item, img));
      media.append(img);
    }

    if (item.overlay && this.showOverlay) {
      const ov = document.createElement('img');
      ov.className = 'ov';
      ov.src = `/api/media/${encodeURIComponent(item.id)}/overlay`;
      media.append(ov);
    }

    const badge = document.createElement('div');
    badge.className = 'badge-kind';
    badge.textContent = (item.kind === 'video' ? '🎬 Vidéo' : '📷 Photo')
      + (item.overlay ? ' · calque' : '');
    media.append(badge);

    const stamp = document.createElement('div');
    stamp.className = 'stamp';
    media.append(stamp);

    card.append(media);
    return card;
  },

  renderInfo(item, el) {
    if (!item) return;
    const info = $('#info');
    let dims = '';
    let dur = '';
    if (el) {
      if (el.tagName === 'VIDEO') {
        dims = `${el.videoWidth}×${el.videoHeight}`;
        if (isFinite(el.duration)) {
          const s = Math.round(el.duration);
          dur = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
        }
      } else {
        dims = `${el.naturalWidth}×${el.naturalHeight}`;
      }
    }
    const gps = item.lat != null
      ? `<a class="gps" target="_blank" rel="noreferrer"
           href="https://www.openstreetmap.org/?mlat=${item.lat}&mlon=${item.lon}#map=16/${item.lat}/${item.lon}">
           📍 ${item.lat.toFixed(4)}, ${item.lon.toFixed(4)}</a>`
      : '';
    info.innerHTML = `
      <div class="date">${cap(fmtDate(item.ts))}</div>
      <div class="meta">
        <div class="kv"><span>Heure</span><b>${fmtTime(item.ts)}</b></div>
        <div class="kv"><span>Type</span><b>${item.kind === 'video' ? 'Vidéo' : 'Photo'}</b></div>
        ${dur ? `<div class="kv"><span>Durée</span><b>${dur}</b></div>` : ''}
        ${dims ? `<div class="kv"><span>Définition</span><b>${dims}</b></div>` : ''}
        <div class="kv"><span>Poids</span><b>${bytes(item.size)}</b></div>
        <div class="kv"><span>Position</span><b>${(item.index ?? 0) + 1} / ${this.session.counts.total}</b></div>
      </div>
      ${gps}`;
  },

  renderFolders() {
    const box = $('#folders');
    box.innerHTML = '';
    (this.session.folders || []).forEach(f => {
      const b = document.createElement('button');
      b.className = 'folder';
      b.dataset.id = f.id;
      b.innerHTML = `<span class="k" style="background:${f.color}">${f.key || '·'}</span>
        <span class="n">${f.name.replace(/</g, '&lt;')}</span>
        <span class="c">${f.count || 0}</span>`;
      b.onclick = () => this.decide('folder', f.id);
      b.oncontextmenu = e => { e.preventDefault(); this.editFolder(f); };
      box.append(b);
    });
    const add = document.createElement('button');
    add.className = 'folder add';
    add.innerHTML = '<span>＋ Nouveau dossier</span><span class="c">N</span>';
    add.onclick = () => this.newFolder();
    box.append(add);
  },

  async newFolder() {
    const used = (this.session.folders || []).map(f => f.key).filter(Boolean);
    const r = await Prompt.text('Nouveau dossier', 'Vacances, Famille, Best of…', true, used);
    if (!r) return;
    const res = await api('/api/folders', { name: r.name, key: r.key });
    this.session.folders = res.folders;
    this.renderFolders();
    toast(`Dossier « ${res.folder.name} » — touche ${res.folder.key || '—'}`);
  },

  async editFolder(f) {
    const used = (this.session.folders || []).map(x => x.key).filter(k => k && k !== f.key);
    const r = await Prompt.text(`Renommer « ${f.name} »`, f.name, true, used);
    if (!r) return;
    const res = await api('/api/folders/update', { id: f.id, name: r.name, key: r.key });
    this.session.folders = res.folders;
    this.renderFolders();
  },

  /* ─────────── décisions ─────────── */

  async decide(action, folderId) {
    if (this.busy) return;
    const item = this.item(0);
    if (!item) return;
    this.busy = true;

    const card = $('#cards .card:last-child');
    const label = {
      keep: ['Gardé', 'var(--keep)', 'right'],
      trash: ['Supprimé', 'var(--trash)', 'left'],
      fav: ['Favori', 'var(--fav)', 'up'],
      skip: ['Passé', 'var(--skip)', 'down'],
      folder: [(this.session.folders.find(f => f.id === folderId) || {}).name || 'Dossier',
        (this.session.folders.find(f => f.id === folderId) || {}).color || 'var(--accent)', 'right'],
    }[action];

    if (card) {
      const stamp = card.querySelector('.stamp');
      stamp.textContent = label[0];
      stamp.style.color = label[1];
      stamp.classList.add('show');
      const dir = label[2];
      const t = { right: 'translateX(130%) rotate(18deg)', left: 'translateX(-130%) rotate(-18deg)',
        up: 'translateY(-130%) scale(.9)', down: 'translateY(60%) scale(.9)' }[dir];
      card.classList.add('leaving');
      requestAnimationFrame(() => { card.style.transform = t; card.style.opacity = '0'; });
    }
    if (folderId) {
      const chip = $(`#folders .folder[data-id="${folderId}"]`);
      chip?.classList.add('flash');
      setTimeout(() => chip?.classList.remove('flash'), 400);
    }

    try {
      const res = await api('/api/decide', { id: item.id, action, folder: folderId || null });
      this.session.counts = res.counts;
      this.session.folders = res.folders;
    } catch (e) {
      toast(e.message, true);
      this.busy = false;
      return this.render();
    }

    this.buffer.shift();
    await this.fill();
    setTimeout(() => {
      this.render();
      this.renderFolders();
      this.updateProgress();
      this.busy = false;
    }, 170);
  },

  async undo() {
    if (this.busy) return;
    this.busy = true;
    try {
      const res = await api('/api/undo', {});
      if (!res.ok) { toast('Rien à annuler'); this.busy = false; return; }
      this.session.counts = res.counts;
      this.session.folders = res.folders;
      await this.reload(res.cursor);
      this.render();
      this.renderFolders();
      this.updateProgress();
      toast('Décision annulée');
    } catch (e) { toast(e.message, true); }
    this.busy = false;
  },

  updateProgress() {
    const c = this.session.counts || { done: 0, total: 0, pending: 0 };
    $('#pbar-fill').style.width = (c.total ? c.done / c.total * 100 : 0) + '%';
    $('#p-count').textContent = `${c.done} / ${c.total}`;
    $('#p-left').textContent = `reste ${Math.max(c.total - c.done, 0)}`;
    $('#p-pending').textContent = c.pending ? `· ${c.pending} copie(s) en cours…` : '';
  },

  /* ─────────── glisser-déposer ─────────── */

  attachDrag() {
    const card = $('#cards .card:last-child');
    if (!card) return;
    let sx = 0, sy = 0, dragging = false;
    const stamp = card.querySelector('.stamp');

    const down = e => {
      if (e.target.closest('a')) return;
      dragging = true; sx = e.clientX; sy = e.clientY;
      card.setPointerCapture(e.pointerId);
      card.classList.remove('settle');
    };
    const move = e => {
      if (!dragging) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      card.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx / 22}deg)`;
      const horiz = Math.abs(dx) > Math.abs(dy);
      const strong = horiz ? Math.abs(dx) > 70 : dy < -70;
      if (strong) {
        stamp.textContent = horiz ? (dx > 0 ? 'Garder' : 'Supprimer') : 'Favori';
        stamp.style.color = horiz ? (dx > 0 ? 'var(--keep)' : 'var(--trash)') : 'var(--fav)';
        stamp.classList.add('show');
      } else stamp.classList.remove('show');
    };
    const up = e => {
      if (!dragging) return;
      dragging = false;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      const horiz = Math.abs(dx) > Math.abs(dy);
      if (horiz && Math.abs(dx) > 110) return this.decide(dx > 0 ? 'keep' : 'trash');
      if (!horiz && dy < -110) return this.decide('fav');
      if (!horiz && dy > 130) return this.decide('skip');
      stamp.classList.remove('show');
      card.classList.add('settle');
      card.style.transform = '';
    };
    card.addEventListener('pointerdown', down);
    card.addEventListener('pointermove', move);
    card.addEventListener('pointerup', up);
    card.addEventListener('pointercancel', up);
  },

  toggleSound() {
    this.muted = !this.muted;
    $$('#cards video').forEach(v => { v.muted = this.muted; });
    toast(this.muted ? 'Son coupé' : 'Son activé');
  },

  togglePlay() {
    const v = $('#cards .card:last-child video');
    if (!v) return;
    v.paused ? v.play() : v.pause();
  },

  toggleOverlay() {
    this.showOverlay = !this.showOverlay;
    this.render();
    toast(this.showOverlay ? 'Calques affichés' : 'Calques masqués');
  },

  async finish() {
    let r;
    try { r = await api('/api/report'); } catch (e) { return toast(e.message, true); }
    const c = this.session.counts;
    showScreen('done');
    $('#done-sub').textContent = `${c.done} souvenirs traités sur ${c.total}`;
    $('#done-stats').innerHTML = `
      <div class="stat"><b>${c.keep + c.fav}</b><span>gardés</span></div>
      <div class="stat"><b>${c.fav}</b><span>favoris</span></div>
      <div class="stat"><b>${c.trash}</b><span>à supprimer</span></div>
      <div class="stat"><b>${c.skip}</b><span>passés</span></div>
      <div class="stat"><b>${c.total - c.done}</b><span>restants</span></div>`;
    $('#done-report').textContent = r.markdown;
    $('#btn-open-dest').onclick = () => api('/api/reveal', { path: this.session.dest });
    $('#btn-replay').classList.toggle('hidden', !c.skip);
    $('#btn-replay').textContent = `Revoir les ${c.skip} passés`;
  },

  async replaySkipped() {
    const res = await api('/api/replay', { action: 'skip' });
    if (!res.count) return toast('Aucun souvenir passé');
    this.session.counts = res.counts;
    this.session.folders = res.folders;
    await this.reload(res.cursor);
    showScreen('sorter');
    this.render();
    this.renderFolders();
    this.updateProgress();
    toast(`${res.count} souvenirs remis en file`);
  },
};

/* ════════════════════════ raccourcis clavier ════════════════════════ */

document.addEventListener('keydown', e => {
  if (!$('#prompt').classList.contains('hidden') || !$('#browser').classList.contains('hidden')) {
    if (e.key === 'Escape') { Prompt.close(null); Browser.close(null); }
    return;
  }
  if (e.key === 'Escape') { $('#help').classList.add('hidden'); return; }
  if (!$('#sorter').classList.contains('active')) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  const k = e.key;
  if (k === 'ArrowRight') { e.preventDefault(); Sorter.decide('keep'); }
  else if (k === 'ArrowLeft') { e.preventDefault(); Sorter.decide('trash'); }
  else if (k === 'ArrowUp') { e.preventDefault(); Sorter.decide('fav'); }
  else if (k === 'ArrowDown') { e.preventDefault(); Sorter.decide('skip'); }
  else if (k === 'Backspace' || k === 'u' || k === 'U') { e.preventDefault(); Sorter.undo(); }
  else if (k === ' ') { e.preventDefault(); Sorter.togglePlay(); }
  else if (k === 'm' || k === 'M') Sorter.toggleSound();
  else if (k === 'c' || k === 'C') Sorter.toggleOverlay();
  else if (k === 'n' || k === 'N') Sorter.newFolder();
  else if (k === '?' || k === '/') $('#help').classList.toggle('hidden');
  else if (/^[0-9]$/.test(k)) {
    const f = (Sorter.session.folders || []).find(x => x.key === k);
    if (f) Sorter.decide('folder', f.id);
    else toast(`Aucun dossier sur la touche ${k} — appuie sur N pour en créer un`);
  }
});

$('#btn-undo').onclick = () => Sorter.undo();
$('#btn-help').onclick = () => $('#help').classList.toggle('hidden');
$('#btn-finish').onclick = () => Sorter.finish();
$('#btn-finish2').onclick = () => Sorter.finish();
$('#btn-back').onclick = () => showScreen('sorter');
$('#btn-replay').onclick = () => Sorter.replaySkipped();
$$('.act').forEach(b => b.onclick = () => Sorter.decide(b.dataset.action));

Setup.init();
