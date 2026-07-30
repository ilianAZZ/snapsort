"""Sorting state: folders, decisions, media cache and the copy queue.

Everything lives in `<destination>/.sorter/`:
  index.json     memory catalogue and sort order (written once)
  state.json     settings, folders, decisions, cursor (small, written often)
  journal.jsonl  append-only history of every action (audit trail)
  cache/         media extracted from the archives on demand (LRU eviction)

Deleting that folder resets the sort without touching the files already filed.
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

from . import metadata, mp4

APP_DIR = ".sorter"
# The project used to be called SnapSort; sessions started back then keep
# working rather than silently losing their progress.
LEGACY_DIR = ".snapsort"

PALETTE = [
    "#f97316", "#22d3ee", "#a78bfa", "#f472b6", "#4ade80",
    "#facc15", "#60a5fa", "#fb7185", "#34d399", "#c084fc",
]
DIGITS = "1234567890"

DEFAULT_OPTIONS = {
    "layout": "year",        # flat | year | year-month
    "naming": "date",        # date | original
    "mode": "copy",          # copy | move  (move is for folder sources only)
    "trash": "ignore",       # ignore | collect
    "order": "oldest",       # oldest | newest | random
    "keep_overlay": True,    # copy the -overlay.png layer next to the media
    "embed_metadata": True,  # write date + GPS into the copy (Exif / QuickTime)
    "auto_join": False,      # join a clip that clearly continues the previous video
    "cache_gb": 3.0,
}

BUCKETS = {"keep": "Kept", "fav": "Favorites", "trash": "_Trash"}

SAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# strftime('%B') follows the system locale, which we do not control: pin the names.
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def safe_folder_name(name: str) -> str:
    name = SAFE_NAME.sub("", (name or "").strip()).strip(". ")
    return name[:60] or "Untitled"


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ==========================================================================
# Media cache
# ==========================================================================

class MediaCache:
    """Extracts a media file from an archive on demand into a reusable local file."""

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
        """Readable local path for `main` or `overlay` (None when absent)."""
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
                with zipfile.ZipFile(container) as archive, \
                        archive.open(entry) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 512)
                os.replace(tmp, target)
            except (KeyError, OSError, zipfile.BadZipFile):
                if os.path.exists(tmp):
                    os.remove(tmp)
                return None
        self._pool.submit(self.evict)
        return target

    def prefetch(self, items):
        for item in items:
            self._pool.submit(self.path_for, item, "main")
            if item.get("overlay"):
                self._pool.submit(self.path_for, item, "overlay")

    def evict(self):
        """LRU eviction (based on atime) once the limit is exceeded."""
        try:
            entries = []
            total = 0
            with os.scandir(self.root) as listing:
                for entry in listing:
                    if not entry.is_file():
                        continue
                    stat = entry.stat()
                    entries.append((stat.st_atime, entry.path, stat.st_size))
                    total += stat.st_size
            if total <= self.limit:
                return
            entries.sort()
            for _atime, path, size in entries:
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
    ACTIONS = ("keep", "trash", "fav", "skip", "folder", "merge")

    def __init__(self, dest: str):
        self.dest = os.path.abspath(dest)
        self.dir = self.state_dir(self.dest)
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

    # ------------------------------------------------------------ plumbing

    def _sweep_temp(self):
        """Drop `.part` files a previous crash left behind (destination only).

        A copy killed mid-flight leaves one. Any decision that mattered is
        replayed from the journal right after, which writes it again.
        """
        for root, dirs, files in os.walk(self.dest):
            dirs[:] = [d for d in dirs if d not in (APP_DIR, LEGACY_DIR)]
            for name in files:
                if name.endswith(".part"):
                    try:
                        os.remove(os.path.join(root, name))
                    except OSError:
                        pass

    def _ensure_dirs(self):
        os.makedirs(self.dir, exist_ok=True)
        self._sweep_temp()
        self.cache = MediaCache(
            os.path.join(self.dir, "cache"),
            int(self.options.get("cache_gb", 3.0) * 1024 ** 3),
        )
        if self._worker is None:
            self._stop = False
            self._worker = threading.Thread(target=self._run_worker, daemon=True)
            self._worker.start()

    @staticmethod
    def state_dir(dest: str) -> str:
        current = os.path.join(dest, APP_DIR)
        legacy = os.path.join(dest, LEGACY_DIR)
        if not os.path.isdir(current) and os.path.isdir(legacy):
            return legacy
        return current

    @staticmethod
    def exists(dest: str) -> bool:
        return os.path.isfile(os.path.join(Session.state_dir(dest), "state.json"))

    # ------------------------------------------------------------- startup

    def start(self, sources: list[str], options: dict):
        with self.lock:
            self.sources = [os.path.abspath(s) for s in sources]
            self.options = {**DEFAULT_OPTIONS, **(options or {})}
            if any(s.lower().endswith(".zip") for s in self.sources):
                self.options["mode"] = "copy"  # never move anything out of an archive
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

    # --------------------------------------------------------- persistence

    def _write(self, name: str, payload: dict):
        tmp = os.path.join(self.dir, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.dir, name))

    def save_index(self):
        """The index is heavy (~3 MB) and only changes on a scan or a reorder."""
        with self.lock:
            payload = {"version": 2, "sources": self.sources, "stats": self.stats,
                       "items": self.items, "order": self.order}
        self._write("index.json", payload)

    def save(self):
        """Write the state right away (small file: decisions, folders, cursor)."""
        with self.lock:
            self._dirty = False
            payload = {"version": 2, "saved_at": time.time(), "options": self.options,
                       "folders": self.folders, "cursor": self.cursor,
                       "decisions": self.decisions}
        self._write("state.json", payload)

    def touch(self):
        """Mark the state dirty; the write is batched (at most one per second).

        The append-only journal keeps a trace of every action in the meantime.
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
            self.scan_progress = {"done": True, "step": "done",
                                  "files": 0, "items": len(self.items), "current": ""}
            self._ensure_dirs()
        self._recover(state.get("saved_at", 0))
        return True

    def _recover(self, since: float) -> int:
        """Replay journal entries newer than the last written state.

        The state is written in batches: an abrupt shutdown (terminal closed,
        machine powered off) can leave it a second behind. The journal is
        written on every action, so nothing is ever lost.
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
                    elif kind == "merge":
                        self.merge(record["id"])
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

    # ------------------------------------------------------------- folders

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

    # ----------------------------------------------------------- decisions

    def _bucket_name(self, action: str, folder: dict | None) -> str | None:
        if action == "folder" and folder:
            return safe_folder_name(folder["name"])
        if action == "trash":
            return BUCKETS["trash"] if self.options["trash"] == "collect" else None
        return BUCKETS.get(action)

    def _target_path(self, item: dict, bucket: str, part: str) -> str:
        when = datetime.fromtimestamp(item["ts"], timezone.utc).astimezone()
        layout = self.options["layout"]
        sub = ""
        if layout == "year":
            sub = when.strftime("%Y")
        elif layout == "year-month":
            sub = os.path.join(when.strftime("%Y"),
                               f"{when.month:02d} - {MONTHS[when.month - 1]}")

        ext = "png" if part == "overlay" else item["ext"]
        if self.options["naming"] == "original":
            stem = f"{item['id']}-{part}"
        else:
            suffix = "-overlay" if part == "overlay" else ""
            stem = f"{when.strftime('%Y-%m-%d_%Hh%Mm%Ss')}_{item['id'][-6:]}{suffix}"
        return os.path.join(self.dest, bucket, sub, f"{stem}.{ext}")

    def decide(self, item_id: str, action: str, folder_id: str | None = None) -> dict:
        if action == "merge":
            return self.merge(item_id)
        if action not in self.ACTIONS:
            raise ValueError(f"unknown action: {action}")
        with self.lock:
            item = self.by_id.get(item_id)
            if not item:
                raise KeyError(item_id)
            folder = (next((f for f in self.folders if f["id"] == folder_id), None)
                      if folder_id else None)
            if action == "folder" and not folder:
                raise ValueError("folder not found")

            previous = self.decisions.get(item_id)
            if previous:
                self._detach(item_id, previous)

            decision = {"action": action, "folder": folder_id, "files": [], "at": time.time()}
            self.decisions[item_id] = decision

            bucket = self._bucket_name(action, folder)
            if bucket:
                jobs = [("main", self._target_path(item, bucket, "main"))]
                if item.get("overlay") and self.options["keep_overlay"]:
                    jobs.append(("overlay", self._target_path(item, bucket, "overlay")))
                decision["files"] = [path for _part, path in jobs]
                self._enqueue([("copy", item, part, path) for part, path in jobs])

            # Segments already joined to this one follow it to its new home.
            members = self._members(item_id)
            if members and bucket:
                self._enqueue([("merge", item_id, None, None)])
            elif members:
                for member_id in members:
                    self.decisions.pop(member_id, None)

            self._recount()
            index = self.order.index(item_id) if item_id in self.order else self.cursor
            self.cursor = max(self.cursor, index + 1)
            self._journal({"type": "decide", "id": item_id, "action": action,
                           "folder": folder_id})
            self.touch()
            return decision

    # --------------------------------------------------------- joining videos

    def _last_decided(self) -> str | None:
        if not self.decisions:
            return None
        return max(self.decisions, key=lambda k: self.decisions[k]["at"])

    def _members(self, root_id: str) -> list[str]:
        """Segments joined to `root_id`, in sort order."""
        ids = [k for k, d in self.decisions.items()
               if d.get("action") == "merge" and d.get("root") == root_id]
        position = {item_id: i for i, item_id in enumerate(self.order)}
        return sorted(ids, key=lambda k: position.get(k, 0))

    def merge(self, item_id: str) -> dict:
        """Join this video to the previous decision instead of filing it on its own.

        Snapchat caps a recording at ten seconds, so a long video comes back as
        several consecutive memories. The segments are concatenated into the
        file the first one produced; nothing is re-encoded.
        """
        with self.lock:
            item = self.by_id.get(item_id)
            if not item:
                raise KeyError(item_id)
            if item["kind"] != "video":
                raise ValueError("only videos can be joined")

            previous_id = self._last_decided()
            if not previous_id or previous_id == item_id:
                raise ValueError("no previous memory to join to")
            previous = self.decisions[previous_id]
            root_id = previous.get("root") or previous_id
            root = self.decisions.get(root_id)
            root_item = self.by_id.get(root_id)
            if not root or not root.get("files"):
                raise ValueError("the previous memory was not kept anywhere")
            if not root_item or root_item["kind"] != "video":
                raise ValueError("the previous memory is not a video")

            existing = self.decisions.get(item_id)
            if existing:
                self._detach(item_id, existing)
            self.decisions[item_id] = {"action": "merge", "folder": None, "root": root_id,
                                       "files": [], "at": time.time()}
            self._enqueue([("merge", root_id, None, None)])

            self._recount()
            index = self.order.index(item_id) if item_id in self.order else self.cursor
            self.cursor = max(self.cursor, index + 1)
            self._journal({"type": "merge", "id": item_id})
            self.touch()
            return self.decisions[item_id]

    def _media_bytes(self, item: dict) -> bytes | None:
        if item["src"] == "file":
            path = item["entry"]
            if not os.path.exists(path):
                return None
            with open(path, "rb") as fh:
                return fh.read()
        cached = self.cache.path_for(item, "main") if self.cache else None
        if cached and os.path.exists(cached):
            with open(cached, "rb") as fh:
                return fh.read()
        with zipfile.ZipFile(item["container"]) as archive:
            return archive.read(item["entry"])

    def _do_merge(self, root_id: str):
        """Rebuild a joined video from its segments, in order.

        Always rebuilt from scratch rather than appended to, so that undoing a
        join simply produces the file again with one segment fewer.
        """
        with self.lock:
            root = self.decisions.get(root_id)
            members = [root_id] + self._members(root_id)
            target = root["files"][0] if root and root.get("files") else None
            items = [self.by_id.get(i) for i in members]
        if not target or not all(items):
            return

        segments = [self._media_bytes(item) for item in items]
        if any(chunk is None for chunk in segments):
            self._errors.append(f"{root_id}: a segment could not be read")
            return
        joined = mp4.concat(segments)
        if joined is None:
            self._errors.append(
                f"{root_id}: these segments cannot be joined without re-encoding")
            return

        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".part"
        with open(tmp, "wb") as fh:
            fh.write(joined)
        if self.options.get("embed_metadata", True):
            metadata.embed(tmp, items[0]["ts"], items[0].get("lat"), items[0].get("lon"),
                           ext=target.rsplit(".", 1)[-1])
        os.replace(tmp, target)
        os.utime(target, (items[0]["ts"], items[0]["ts"]))

    def _recount(self):
        """Recompute how many memories each folder holds."""
        tally: dict[str, int] = {}
        for decision in self.decisions.values():
            if decision["action"] == "folder" and decision.get("folder"):
                tally[decision["folder"]] = tally.get(decision["folder"], 0) + 1
        for folder in self.folders:
            folder["count"] = tally.get(folder["id"], 0)

    def undo(self) -> str | None:
        """Undo the most recent decision and put the cursor back on it."""
        with self.lock:
            if not self.decisions:
                return None
            item_id = self._last_decided()
            decision = self.decisions.pop(item_id)
            self._detach(item_id, decision)
            self._recount()
            if item_id in self.order:
                self.cursor = self.order.index(item_id)
            self._journal({"type": "undo", "id": item_id})
            self.touch()
            return item_id

    def _detach(self, item_id: str, decision: dict):
        """Roll back everything a decision produced, before dropping or replacing it."""
        self._undo_files(decision)
        if decision.get("action") == "merge":
            # The file belongs to the root: rebuild it without this segment.
            root_id = decision.get("root")
            if root_id in self.decisions:
                self._enqueue([("merge", root_id, None, None)])
            return
        # Undoing a root leaves its segments with nowhere to go.
        for member_id in self._members(item_id):
            self.decisions.pop(member_id, None)

    def _undo_files(self, decision: dict):
        """Remove the files a decision produced (never a source file)."""
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

    # ------------------------------------------------------ background copy

    def _enqueue(self, jobs: list[tuple]):
        with self._queue_cv:
            for job in jobs:
                self._queue.append(job)
                self._pending += 1
            self._queue_cv.notify()

    def _run_worker(self):
        while not self._stop:
            with self._queue_cv:
                while not self._queue and not self._stop:
                    self._queue_cv.wait(0.5)
                if self._stop:
                    return
                job = self._queue.pop(0)
            try:
                if job[0] == "merge":
                    self._do_merge(job[1])
                else:
                    self._transfer(job[1], job[2], job[3])
            except Exception as exc:  # noqa: BLE001 — sorting must never block
                self._errors.append(f"{job[1] if job[0] == 'merge' else job[1]['id']}: {exc}")
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
                with zipfile.ZipFile(container) as archive, \
                        archive.open(entry) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 512)
        if self.options.get("embed_metadata", True):
            # Written on the copy, never on the source (invariant 1).
            metadata.embed(tmp, item["ts"], item.get("lat"), item.get("lon"),
                           ext=target.rsplit(".", 1)[-1])
        os.replace(tmp, target)
        os.utime(target, (item["ts"], item["ts"]))  # keep the memory's own date

    # ------------------------------------------------------------- reading

    def counts(self) -> dict:
        with self.lock:
            out = {action: 0 for action in self.ACTIONS}
            for decision in self.decisions.values():
                out[decision["action"]] = out.get(decision["action"], 0) + 1
            out["done"] = len(self.decisions)
            out["total"] = len(self.order)
            out["pending"] = self._pending
            return out

    def queue(self, start: int, count: int) -> tuple[list[dict], int]:
        """The next `count` memories *not yet decided*, starting at `start`.

        Each entry carries its absolute index in the sort order, and the second
        member of the tuple says where to resume scanning. Memories already
        sorted are skipped, so resuming a session or undoing never shows one
        again.
        """
        with self.lock:
            out: list[dict] = []
            i = max(0, start)
            while i < len(self.order) and len(out) < count:
                item_id = self.order[i]
                if item_id not in self.decisions and item_id in self.by_id:
                    out.append(dict(self.by_id[item_id], index=i))
                i += 1
        if self.cache:
            self.cache.prefetch(out[:6])
        return out, i

    def replay(self, action: str = "skip") -> int:
        """Put memories decided with `action` back in the queue (default: skipped)."""
        with self.lock:
            ids = [k for k, v in self.decisions.items() if v["action"] == action]
            for item_id in ids:
                self._detach(item_id, self.decisions.pop(item_id))
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

    # -------------------------------------------------------------- report

    def report(self) -> str:
        self.save()  # what is on disk matches the report we hand out
        with self.lock:
            counts = self.counts()
            lines = [
                "# Snapchat Memories Sorter report", "",
                f"*Generated on {datetime.now().strftime('%Y-%m-%d at %H:%M')}*", "",
                "## Summary", "",
                f"- **{counts['total']}** memories indexed",
                f"- **{counts['done']}** sorted "
                f"({counts['done'] * 100 // max(counts['total'], 1)}%)",
                f"- **{counts['keep']}** kept · **{counts['fav']}** favourites · "
                f"**{counts['trash']}** discarded · **{counts['skip']}** skipped",
                f"- Source volume: **{human_bytes(self.stats.get('bytes', 0))}**",
            ]
            if counts.get("merge"):
                lines.append(f"- **{counts['merge']}** segments joined to a longer video")
            lines += ["", "## Folders", ""]
            if self.folders:
                lines += ["| Key | Folder | Memories |", "|---|---|---|"]
                for folder in self.folders:
                    lines.append(f"| `{folder.get('key') or '—'}` | {folder['name']} "
                                 f"| {folder.get('count', 0)} |")
            else:
                lines.append("*No custom folder.*")

            per_year: dict[str, int] = {}
            for item_id, decision in self.decisions.items():
                if decision["action"] in ("trash", "skip"):
                    continue
                item = self.by_id.get(item_id)
                if item:
                    year = datetime.fromtimestamp(item["ts"], timezone.utc).strftime("%Y")
                    per_year[year] = per_year.get(year, 0) + 1
            if per_year:
                lines += ["", "## Kept per year", "", "| Year | Memories |", "|---|---|"]
                lines += [f"| {year} | {n} |" for year, n in sorted(per_year.items())]

            lines += ["", "---", "", "The source files were never modified.", ""]
            text = "\n".join(lines)
        with open(os.path.join(self.dest, "REPORT.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        return text
