"""Source discovery and building the memory index.

A "source" is either a .zip from the Snapchat export or an already extracted
folder. Only the archives' catalogue (their central directory) is read: not one
byte of media is decompressed during the scan.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "heic", "gif"}
VIDEO_EXT = {"mp4", "mov", "m4v", "webm"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT

# 2017-11-24_6a1c7e35-1d8e-36a2-6706-fb08fff113a0-main.mp4
NAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<uid>[0-9a-fA-F][0-9a-fA-F-]+)"
    r"-(?P<part>main|overlay|thumbnail)\.(?P<ext>[A-Za-z0-9]+)$"
)
LOC_RE = re.compile(r"(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)")


def kind_of(ext: str) -> str:
    return "video" if ext.lower() in VIDEO_EXT else "image"


# --------------------------------------------------------------------------
# Finding sources automatically
# --------------------------------------------------------------------------

def looks_like_export_zip(path: str) -> bool:
    """True when the zip holds a memories/ folder (quick test, no extraction)."""
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist()[:400]:
                if "memories/" in name or "chat_media/" in name:
                    return True
    except Exception:
        return False
    return False


def suggest_sources(extra_dirs=()):
    """Look for Snapchat exports in the usual places.

    Returns a list of groups: {dir, files[], count, size} — one per folder
    holding `mydata~*.zip` archives.
    """
    home = os.path.expanduser("~")
    roots = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "Bureau"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Téléchargements"),
        os.path.join(home, "Documents"),
        home,
        *extra_dirs,
    ]
    seen, groups = set(), []
    for root in roots:
        if not os.path.isdir(root) or root in seen:
            continue
        seen.add(root)
        # the root itself plus one level of sub-folders
        candidates = [root]
        try:
            with os.scandir(root) as listing:
                candidates += [e.path for e in listing
                               if e.is_dir() and not e.name.startswith(".")]
        except OSError:
            pass
        for folder in candidates:
            if folder in seen and folder != root:
                continue
            seen.add(folder)
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            zips = [
                os.path.join(folder, n)
                for n in names
                if n.lower().endswith(".zip") and ("mydata" in n.lower() or "snapchat" in n.lower())
            ]
            if not zips:
                continue
            zips = [p for p in zips if looks_like_export_zip(p)]
            if not zips:
                continue
            groups.append({
                "dir": folder,
                "files": zips,
                "count": len(zips),
                "size": sum(os.path.getsize(p) for p in zips),
            })
    groups.sort(key=lambda g: -g["size"])
    return groups[:6]


# --------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------

def _add(bucket, key, part, record):
    bucket.setdefault(key, {})[part] = record


def _scan_zip(path: str, bucket: dict, records: list, progress):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        for info in infos:
            if info.is_dir():
                continue
            base = os.path.basename(info.filename)
            low = base.lower()

            if low == "memories_history.json":
                try:
                    data = json.loads(archive.read(info).decode("utf-8", "replace"))
                    records.extend(data.get("Saved Media") or [])
                except Exception:
                    pass
                continue

            match = NAME_RE.match(base)
            ext = (match.group("ext") if match else base.rsplit(".", 1)[-1]).lower()
            if ext not in MEDIA_EXT:
                continue

            # The mtime stored in a Snapchat zip is the memory's UTC timestamp.
            ts = datetime(*info.date_time, tzinfo=timezone.utc).timestamp()
            if match:
                key = f"{match.group('date')}_{match.group('uid')}"
                part = match.group("part")
            else:
                key, part = base.rsplit(".", 1)[0], "main"
            if part == "thumbnail":
                continue
            _add(bucket, key, part, {
                "src": "zip", "container": path, "entry": info.filename,
                "ext": ext, "size": info.file_size, "ts": ts,
            })
        progress["files"] += len(infos)


def _scan_dir(path: str, bucket: dict, records: list, progress):
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != ".sorter"]
        for base in files:
            full = os.path.join(root, base)
            low = base.lower()

            if low == "memories_history.json":
                try:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        records.extend(json.load(fh).get("Saved Media") or [])
                except Exception:
                    pass
                continue

            match = NAME_RE.match(base)
            ext = (match.group("ext") if match else base.rsplit(".", 1)[-1]).lower()
            if ext not in MEDIA_EXT:
                continue
            try:
                stat = os.stat(full)
            except OSError:
                continue
            # unzip rewrote the zip's UTC mtime as local time: undo that.
            naive = datetime.fromtimestamp(stat.st_mtime)
            ts = naive.replace(tzinfo=timezone.utc).timestamp()

            if match:
                key = f"{match.group('date')}_{match.group('uid')}"
                part = match.group("part")
            else:
                key, part = base.rsplit(".", 1)[0], "main"
            if part == "thumbnail":
                continue
            _add(bucket, key, part, {
                "src": "file", "container": "", "entry": full,
                "ext": ext, "size": stat.st_size, "ts": ts,
            })
            progress["files"] += 1


def _attach_metadata(items: list, records: list):
    """Match memories_history.json entries to the media files.

    The link is the exact UTC timestamp, identical on both sides down to the
    zip format's two-second granularity. Ties are broken by media type, then by
    a stable order.
    """
    if not records:
        return 0

    by_ts = defaultdict(list)
    for record in records:
        try:
            when = datetime.strptime(record.get("Date", ""), "%Y-%m-%d %H:%M:%S UTC")
        except ValueError:
            continue
        by_ts[int(when.replace(tzinfo=timezone.utc).timestamp()) // 2].append(record)

    matched = 0
    for item in sorted(items, key=lambda i: (i["ts"], i["id"])):
        slot = int(item["ts"]) // 2
        pool = by_ts.get(slot) or by_ts.get(slot - 1) or by_ts.get(slot + 1)
        if not pool:
            continue
        want = "Video" if item["kind"] == "video" else "Image"
        pick = next((r for r in pool if r.get("Media Type") == want), pool[0])
        pool.remove(pick)

        location = LOC_RE.search(pick.get("Location", "") or "")
        if location:
            lat, lon = float(location.group(1)), float(location.group(2))
            if abs(lat) > 0.0001 or abs(lon) > 0.0001:
                item["lat"], item["lon"] = lat, lon
        matched += 1
    return matched


def expand_sources(sources) -> list[str]:
    """Expand sources: a folder holding export archives is replaced by them.

    Picking "the folder that holds my zips" is the natural gesture: nobody
    should have to tick twelve archives one by one.
    """
    out: list[str] = []
    for src in sources:
        src = os.path.abspath(os.path.expanduser(src))
        if os.path.isdir(src):
            try:
                inner = sorted(
                    os.path.join(src, n) for n in os.listdir(src)
                    if n.lower().endswith(".zip")
                )
            except OSError:
                inner = []
            out.extend(p for p in inner if looks_like_export_zip(p))
            # keep the folder too: it may hold loose media
            out.append(src)
        else:
            out.append(src)
    seen, unique = set(), []
    for path in out:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def scan(sources, progress=None) -> dict:
    """Build the index. `progress` is a dict updated in place."""
    progress = progress if progress is not None else {}
    progress.update({"files": 0, "items": 0, "step": "reading", "current": "", "done": False})

    bucket: dict = {}
    records: list = []
    for src in expand_sources(sources):
        progress["current"] = os.path.basename(src) or src
        if os.path.isdir(src):
            _scan_dir(src, bucket, records, progress)
        elif zipfile.is_zipfile(src):
            _scan_zip(src, bucket, records, progress)

    progress["step"] = "indexing"
    items = []
    for key, parts in bucket.items():
        main = parts.get("main") or parts.get("overlay")
        if not main:
            continue
        over = parts.get("overlay") if parts.get("main") else None
        items.append({
            "id": key,
            "src": main["src"],
            "container": main["container"],
            "entry": main["entry"],
            "ext": main["ext"],
            "kind": kind_of(main["ext"]),
            "size": main["size"],
            "ts": main["ts"],
            "overlay": (over or {}).get("entry"),
            "overlay_container": (over or {}).get("container", ""),
            "lat": None,
            "lon": None,
        })

    progress["step"] = "metadata"
    matched = _attach_metadata(items, records)

    items.sort(key=lambda i: (i["ts"], i["id"]))
    progress.update({
        "items": len(items),
        "step": "done",
        "done": True,
        "matched": matched,
    })
    return {
        "items": items,
        "stats": {
            "total": len(items),
            "videos": sum(1 for i in items if i["kind"] == "video"),
            "images": sum(1 for i in items if i["kind"] == "image"),
            "bytes": sum(i["size"] for i in items),
            "with_gps": sum(1 for i in items if i["lat"] is not None),
            "with_overlay": sum(1 for i in items if i["overlay"]),
            "first": items[0]["ts"] if items else None,
            "last": items[-1]["ts"] if items else None,
            "metadata_matched": matched,
        },
    }
