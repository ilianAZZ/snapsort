"""État de tri : dossiers, décisions, cache média et file de copie.

Tout vit dans `<destination>/.snapsort/` :
  index.json     catalogue des souvenirs et ordre de tri (écrit une seule fois)
  state.json     réglages, dossiers, décisions, curseur (petit, écrit souvent)
  journal.jsonl  historique append-only de chaque action (audit)
  cache/         médias extraits à la demande des zips (purge LRU)

Supprimer ce dossier remet le tri à zéro sans toucher aux fichiers déjà rangés.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

APP_DIR = ".snapsort"

PALETTE = [
    "#f97316", "#22d3ee", "#a78bfa", "#f472b6", "#4ade80",
    "#facc15", "#60a5fa", "#fb7185", "#34d399", "#c084fc",
]
DIGITS = "1234567890"

DEFAULT_OPTIONS = {
    "layout": "year",        # flat | year | year-month
    "naming": "date",        # date | original
    "mode": "copy",          # copy | move  (move réservé aux sources dossier)
    "trash": "ignore",       # ignore | collect
    "order": "oldest",       # oldest | newest | random
    "keep_overlay": True,    # copier le calque -overlay.png à côté du média
    "cache_gb": 3.0,
}

SAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# strftime('%B') suit la locale du système (souvent l'anglais) : on fixe les noms.
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre")


def safe_folder_name(name: str) -> str:
    name = SAFE_NAME.sub("", (name or "").strip()).strip(". ")
    return name[:60] or "Sans titre"


def human_bytes(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} To"


# ==========================================================================
# Cache média
# ==========================================================================

class MediaCache:
    """Extrait à la demande un média d'un zip vers un fichier local réutilisable."""

    def __init__(self, root: str, limit_bytes: int):
        self.root = root
        self.limit = limit_bytes
        os.makedirs(root, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="prefetch")

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def path_for(self, item: dict, part: str) -> str | None:
        """Chemin local lisible pour `main` ou `overlay` (None si absent)."""
        if part == "overlay":
            entry, container, ext = item.get("overlay"), item.get("overlay_container"), "png"
        else:
            entry, container, ext = item["entry"], item["container"], item["ext"]
        if not entry:
            return None
        if item["src"] == "file":
            return entry if os.path.exists(entry) else None

        target = os.path.join(self.root, f"{item['id']}-{part}.{ext}")
        if os.path.exists(target):
            os.utime(target, None)
            return target

        with self._lock_for(target):
            if os.path.exists(target):
                return target
            tmp = target + ".part"
            try:
                with zipfile.ZipFile(container) as z, z.open(entry) as fsrc, open(tmp, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1024 * 512)
                os.replace(tmp, target)
            except (KeyError, OSError, zipfile.BadZipFile):
                if os.path.exists(tmp):
                    os.remove(tmp)
                return None
        self._pool.submit(self.evict)
        return target

    def prefetch(self, items):
        for it in items:
            self._pool.submit(self.path_for, it, "main")
            if it.get("overlay"):
                self._pool.submit(self.path_for, it, "overlay")

    def evict(self):
        """Purge LRU (basée sur l'atime) une fois la limite dépassée."""
        try:
            entries = []
            total = 0
            with os.scandir(self.root) as it:
                for e in it:
                    if not e.is_file():
                        continue
                    st = e.stat()
                    entries.append((st.st_atime, e.path, st.st_size))
                    total += st.st_size
            if total <= self.limit:
                return
            entries.sort()
            for _, path, size in entries:
                if total <= self.limit * 0.8:
                    break
                try:
                    os.remove(path)
                    total -= size
                except OSError:
                    pass
        except OSError:
            pass

    def clear(self):
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root, exist_ok=True)


# ==========================================================================
# Session
# ==========================================================================

class Session:
    ACTIONS = ("keep", "trash", "fav", "skip", "folder")

    def __init__(self, dest: str):
        self.dest = os.path.abspath(dest)
        self.dir = os.path.join(self.dest, APP_DIR)
        self.lock = threading.RLock()

        self.sources: list[str] = []
        self.options = dict(DEFAULT_OPTIONS)
        self.folders: list[dict] = []
        self.items: list[dict] = []
        self.order: list[str] = []
        self.by_id: dict[str, dict] = {}
        self.decisions: dict[str, dict] = {}
        self.cursor = 0
        self.stats: dict = {}
        self.scan_progress = {"done": False, "step": "", "files": 0, "items": 0, "current": ""}

        self.cache: MediaCache | None = None
        self._queue: list[tuple] = []
        self._queue_cv = threading.Condition()
        self._worker: threading.Thread | None = None
        self._pending = 0
        self._errors: list[str] = []
        self._stop = False
        self._dirty = False
        self._save_timer: threading.Timer | None = None
        self._replaying = False

    # ---------------------------------------------------------------- infra

    def _ensure_dirs(self):
        os.makedirs(self.dir, exist_ok=True)
        self.cache = MediaCache(
            os.path.join(self.dir, "cache"),
            int(self.options.get("cache_gb", 3.0) * 1024 ** 3),
        )
        if self._worker is None:
            self._stop = False
            self._worker = threading.Thread(target=self._run_worker, daemon=True)
            self._worker.start()

    @staticmethod
    def exists(dest: str) -> bool:
        return os.path.isfile(os.path.join(dest, APP_DIR, "state.json"))

    # ------------------------------------------------------------ création

    def start(self, sources: list[str], options: dict):
        with self.lock:
            self.sources = [os.path.abspath(s) for s in sources]
            self.options = {**DEFAULT_OPTIONS, **(options or {})}
            if any(s.lower().endswith(".zip") for s in self.sources):
                self.options["mode"] = "copy"  # jamais de déplacement depuis un zip
            self.folders, self.decisions, self.cursor = [], {}, 0
            self._ensure_dirs()
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        from . import scan as scanner

        result = scanner.scan(self.sources, self.scan_progress)
        with self.lock:
            self.items = result["items"]
            self.stats = result["stats"]
            self.by_id = {i["id"]: i for i in self.items}
            self._apply_order()
        self.save_index()
        self.save()

    def _apply_order(self):
        order = self.options.get("order", "oldest")
        ids = [i["id"] for i in self.items]
        if order == "newest":
            ids.reverse()
        elif order == "random":
            import random

            random.Random(1789).shuffle(ids)
        self.order = ids

    # ------------------------------------------------------ persistance

    def _write(self, name: str, payload: dict):
        tmp = os.path.join(self.dir, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.dir, name))

    def save_index(self):
        """L'index (lourd, ~3 Mo) ne change qu'au scan ou si l'ordre change."""
        with self.lock:
            payload = {"version": 2, "sources": self.sources, "stats": self.stats,
                       "items": self.items, "order": self.order}
        self._write("index.json", payload)

    def save(self):
        """Écrit l'état immédiatement (petit fichier : décisions, dossiers, curseur)."""
        with self.lock:
            self._dirty = False
            payload = {"version": 2, "saved_at": time.time(), "options": self.options,
                       "folders": self.folders, "cursor": self.cursor,
                       "decisions": self.decisions}
        self._write("state.json", payload)

    def touch(self):
        """Marque l'état modifié ; l'écriture est regroupée (au plus une par seconde).

        Le journal append-only garde de son côté la trace de chaque action.
        """
        with self.lock:
            self._dirty = True
            if self._save_timer and self._save_timer.is_alive():
                return
            self._save_timer = threading.Timer(1.0, self._flush)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _flush(self):
        with self.lock:
            dirty = self._dirty
        if dirty:
            self.save()

    def load(self) -> bool:
        index_path = os.path.join(self.dir, "index.json")
        state_path = os.path.join(self.dir, "state.json")
        if not os.path.isfile(state_path):
            return False
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        index = {}
        if os.path.isfile(index_path):
            with open(index_path, encoding="utf-8") as fh:
                index = json.load(fh)
        with self.lock:
            self.sources = index.get("sources", [])
            self.options = {**DEFAULT_OPTIONS, **state.get("options", {})}
            self.folders = state.get("folders", [])
            self.items = index.get("items", [])
            self.order = index.get("order") or [i["id"] for i in self.items]
            self.decisions = state.get("decisions", {})
            self.cursor = state.get("cursor", 0)
            self.stats = index.get("stats", {})
            self.by_id = {i["id"]: i for i in self.items}
            self.scan_progress = {"done": True, "step": "terminé",
                                  "files": 0, "items": len(self.items), "current": ""}
            self._ensure_dirs()
        self._recover(state.get("saved_at", 0))
        return True

    def _recover(self, since: float) -> int:
        """Rejoue les actions du journal plus récentes que le dernier état écrit.

        L'état est écrit de façon regroupée : une coupure brutale (terminal fermé,
        machine éteinte) peut le laisser en retard d'une seconde. Le journal, lui,
        est écrit à chaque action : il permet de ne rien perdre.
        """
        path = os.path.join(self.dir, "journal.jsonl")
        if not os.path.isfile(path):
            return 0
        pending = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("at", 0) > since:
                    pending.append(record)
        if not pending:
            return 0

        self._replaying = True
        try:
            for record in pending:
                kind = record.get("type")
                try:
                    if kind == "decide":
                        self.decide(record["id"], record["action"], record.get("folder"))
                    elif kind == "undo":
                        self.undo()
                    elif kind == "replay":
                        self.replay(record.get("action", "skip"))
                except (KeyError, ValueError):
                    continue
        finally:
            self._replaying = False
        self.save()
        return len(pending)

    def _journal(self, record: dict):
        if self._replaying:
            return
        record["at"] = time.time()
        with open(os.path.join(self.dir, "journal.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --------------------------------------------------------- dossiers

    def free_key(self) -> str | None:
        used = {f.get("key") for f in self.folders}
        return next((d for d in DIGITS if d not in used), None)

    def add_folder(self, name: str, key: str | None = None) -> dict:
        with self.lock:
            name = safe_folder_name(name)
            existing = next((f for f in self.folders if f["name"].lower() == name.lower()), None)
            if existing:
                return existing
            if key and any(f.get("key") == key for f in self.folders):
                key = None
            folder = {
                "id": f"f{int(time.time() * 1000) % 10_000_000}{len(self.folders)}",
                "name": name,
                "key": key or self.free_key(),
                "color": PALETTE[len(self.folders) % len(PALETTE)],
                "count": 0,
            }
            self.folders.append(folder)
            self.save()
            return folder

    def update_folder(self, fid: str, name=None, key=None, color=None) -> dict | None:
        with self.lock:
            folder = next((f for f in self.folders if f["id"] == fid), None)
            if not folder:
                return None
            if name:
                folder["name"] = safe_folder_name(name)
            if key is not None:
                key = key or None
                for other in self.folders:
                    if other is not folder and other.get("key") == key:
                        other["key"] = None
                folder["key"] = key
            if color:
                folder["color"] = color
            self.save()
            return folder

    def delete_folder(self, fid: str):
        with self.lock:
            self.folders = [f for f in self.folders if f["id"] != fid]
            self.save()

    # -------------------------------------------------------- décisions

    def _bucket_name(self, action: str, folder: dict | None) -> str | None:
        if action == "folder" and folder:
            return safe_folder_name(folder["name"])
        return {"keep": "Gardés", "fav": "Favoris",
                "trash": "_Corbeille" if self.options["trash"] == "collect" else None,
                "skip": None}.get(action)

    def _target_path(self, item: dict, bucket: str, part: str) -> str:
        dt = datetime.fromtimestamp(item["ts"], timezone.utc).astimezone()
        layout = self.options["layout"]
        sub = ""
        if layout == "year":
            sub = dt.strftime("%Y")
        elif layout == "year-month":
            sub = os.path.join(dt.strftime("%Y"), f"{dt.month:02d} - {MOIS[dt.month - 1]}")

        ext = "png" if part == "overlay" else item["ext"]
        if self.options["naming"] == "original":
            stem = f"{item['id']}-{part}"
        else:
            suffix = "-calque" if part == "overlay" else ""
            stem = f"{dt.strftime('%Y-%m-%d_%Hh%Mm%Ss')}_{item['id'][-6:]}{suffix}"
        return os.path.join(self.dest, bucket, sub, f"{stem}.{ext}")

    def decide(self, item_id: str, action: str, folder_id: str | None = None) -> dict:
        if action not in self.ACTIONS:
            raise ValueError(f"action inconnue : {action}")
        with self.lock:
            item = self.by_id.get(item_id)
            if not item:
                raise KeyError(item_id)
            folder = next((f for f in self.folders if f["id"] == folder_id), None) if folder_id else None
            if action == "folder" and not folder:
                raise ValueError("dossier introuvable")

            previous = self.decisions.get(item_id)
            if previous:
                self._undo_files(previous)

            decision = {"action": action, "folder": folder_id, "files": [], "at": time.time()}
            self.decisions[item_id] = decision

            bucket = self._bucket_name(action, folder)
            if bucket:
                jobs = [("main", self._target_path(item, bucket, "main"))]
                if item.get("overlay") and self.options["keep_overlay"]:
                    jobs.append(("overlay", self._target_path(item, bucket, "overlay")))
                decision["files"] = [p for _, p in jobs]
                self._enqueue(item, jobs)

            self._recount()
            idx = self.order.index(item_id) if item_id in self.order else self.cursor
            self.cursor = max(self.cursor, idx + 1)
            self._journal({"type": "decide", "id": item_id, "action": action, "folder": folder_id})
            self.touch()
            return decision

    def _recount(self):
        """Recalcule le nombre de souvenirs de chaque dossier."""
        tally: dict[str, int] = {}
        for d in self.decisions.values():
            if d["action"] == "folder" and d.get("folder"):
                tally[d["folder"]] = tally.get(d["folder"], 0) + 1
        for folder in self.folders:
            folder["count"] = tally.get(folder["id"], 0)

    def undo(self) -> str | None:
        """Annule la dernière décision et repositionne le curseur dessus."""
        with self.lock:
            if not self.decisions:
                return None
            item_id = max(self.decisions, key=lambda k: self.decisions[k]["at"])
            decision = self.decisions.pop(item_id)
            self._undo_files(decision)
            self._recount()
            if item_id in self.order:
                self.cursor = self.order.index(item_id)
            self._journal({"type": "undo", "id": item_id})
            self.touch()
            return item_id

    def _undo_files(self, decision: dict):
        """Retire les fichiers produits par une décision (jamais la source)."""
        for path in decision.get("files", []):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                parent = os.path.dirname(path)
                while os.path.commonpath([parent, self.dest]) == self.dest and parent != self.dest:
                    if os.listdir(parent):
                        break
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
            except (OSError, ValueError):
                pass

    def seek(self, index: int):
        with self.lock:
            self.cursor = max(0, min(index, len(self.order)))
            self.save()

    # ------------------------------------------------------- copie async

    def _enqueue(self, item: dict, jobs: list[tuple]):
        with self._queue_cv:
            for part, target in jobs:
                self._queue.append((item, part, target))
                self._pending += 1
            self._queue_cv.notify()

    def _run_worker(self):
        while not self._stop:
            with self._queue_cv:
                while not self._queue and not self._stop:
                    self._queue_cv.wait(0.5)
                if self._stop:
                    return
                item, part, target = self._queue.pop(0)
            try:
                self._transfer(item, part, target)
            except Exception as exc:  # noqa: BLE001 — on ne bloque jamais le tri
                self._errors.append(f"{item['id']} : {exc}")
            finally:
                with self._queue_cv:
                    self._pending -= 1

    def _transfer(self, item: dict, part: str, target: str):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".part"
        if item["src"] == "file":
            source = item["entry"] if part == "main" else item.get("overlay")
            if not source or not os.path.exists(source):
                return
            if self.options["mode"] == "move":
                shutil.move(source, tmp)
            else:
                shutil.copy2(source, tmp)
        else:
            entry = item["entry"] if part == "main" else item.get("overlay")
            container = item["container"] if part == "main" else item.get("overlay_container")
            if not entry:
                return
            cached = self.cache.path_for(item, part) if self.cache else None
            if cached and os.path.exists(cached):
                shutil.copy2(cached, tmp)
            else:
                with zipfile.ZipFile(container) as z, z.open(entry) as fsrc, open(tmp, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1024 * 512)
        os.replace(tmp, target)
        os.utime(target, (item["ts"], item["ts"]))  # conserve la date du souvenir

    # ------------------------------------------------------------ lecture

    def counts(self) -> dict:
        with self.lock:
            out = {a: 0 for a in self.ACTIONS}
            for d in self.decisions.values():
                out[d["action"]] = out.get(d["action"], 0) + 1
            out["done"] = len(self.decisions)
            out["total"] = len(self.order)
            out["pending"] = self._pending
            return out

    def queue(self, start: int, count: int) -> tuple[list[dict], int]:
        """Les `count` prochains souvenirs *non encore décidés* à partir de `start`.

        Chaque élément porte son index absolu dans l'ordre de tri, et le second
        membre du tuple indique où reprendre le balayage. Les souvenirs déjà
        triés sont ignorés : reprendre une session ou annuler ne les ré-affiche
        jamais.
        """
        with self.lock:
            out: list[dict] = []
            i = max(0, start)
            while i < len(self.order) and len(out) < count:
                iid = self.order[i]
                if iid not in self.decisions and iid in self.by_id:
                    out.append(dict(self.by_id[iid], index=i))
                i += 1
        if self.cache:
            self.cache.prefetch(out[:6])
        return out, i

    def replay(self, action: str = "skip") -> int:
        """Remet en file les souvenirs décidés avec `action` (par défaut : passés)."""
        with self.lock:
            ids = [k for k, v in self.decisions.items() if v["action"] == action]
            for k in ids:
                self._undo_files(self.decisions.pop(k))
            positions = [self.order.index(i) for i in ids if i in self.order]
            if positions:
                self.cursor = min(positions)
            self._recount()
            self._journal({"type": "replay", "action": action, "count": len(ids)})
            self.save()
            return len(ids)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "dest": self.dest,
                "sources": self.sources,
                "options": self.options,
                "folders": self.folders,
                "cursor": self.cursor,
                "stats": self.stats,
                "counts": self.counts(),
                "scan": dict(self.scan_progress),
                "errors": self._errors[-5:],
            }

    # ------------------------------------------------------------ rapport

    def report(self) -> str:
        self.save()  # l'état sur disque reflète le rapport produit
        with self.lock:
            counts = self.counts()
            lines = [
                "# Rapport de tri SnapSort", "",
                f"*Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}*", "",
                "## Résumé", "",
                f"- **{counts['total']}** souvenirs indexés",
                f"- **{counts['done']}** triés "
                f"({counts['done'] * 100 // max(counts['total'], 1)} %)",
                f"- **{counts['keep']}** gardés · **{counts['fav']}** favoris · "
                f"**{counts['trash']}** à supprimer · **{counts['skip']}** passés",
                f"- Volume source : **{human_bytes(self.stats.get('bytes', 0))}**",
                "", "## Dossiers", "",
            ]
            if self.folders:
                lines += ["| Touche | Dossier | Souvenirs |", "|---|---|---|"]
                for f in self.folders:
                    lines.append(f"| `{f.get('key') or '—'}` | {f['name']} | {f.get('count', 0)} |")
            else:
                lines.append("*Aucun dossier personnalisé.*")

            per_year: dict[str, int] = {}
            for item_id, d in self.decisions.items():
                if d["action"] in ("trash", "skip"):
                    continue
                item = self.by_id.get(item_id)
                if item:
                    year = datetime.fromtimestamp(item["ts"], timezone.utc).strftime("%Y")
                    per_year[year] = per_year.get(year, 0) + 1
            if per_year:
                lines += ["", "## Conservés par année", "", "| Année | Souvenirs |", "|---|---|"]
                lines += [f"| {y} | {n} |" for y, n in sorted(per_year.items())]

            lines += ["", "---", "", "Les fichiers source n'ont jamais été modifiés.", ""]
            text = "\n".join(lines)
        with open(os.path.join(self.dest, "RAPPORT.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        return text
