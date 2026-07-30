"""Local HTTP server (standard library only).

Serves the web interface, the JSON API and the media files (with Range request
support, which video playback and seeking depend on). Binds to 127.0.0.1 only:
nothing ever leaves the machine.
"""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import scan as scanner
from .session import Session

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

state: dict = {"session": None, "autostart": False}
state_lock = threading.Lock()


def get_session() -> Session | None:
    return state["session"]


# --------------------------------------------------------------------------
# System helpers
# --------------------------------------------------------------------------

def list_dir(path: str) -> dict:
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(path):
        path = os.path.expanduser("~")
    dirs, zips = [], []
    try:
        with os.scandir(path) as listing:
            for entry in listing:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        dirs.append({"name": entry.name, "path": entry.path})
                    elif entry.name.lower().endswith(".zip"):
                        zips.append({"name": entry.name, "path": entry.path,
                                     "size": entry.stat().st_size})
                except OSError:
                    continue
    except PermissionError:
        pass
    dirs.sort(key=lambda d: d["name"].lower())
    zips.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(path)
    return {
        "path": path,
        "parent": parent if parent != path else None,
        "dirs": dirs,
        "zips": zips,
        "writable": os.access(path, os.W_OK),
        "free": shutil.disk_usage(path).free if os.path.isdir(path) else 0,
    }


def reveal(path: str):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Snapchat Memories Sorter"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the console quiet
        pass

    # ----------------------------------------------------------- responses

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=400):
        self._send_json({"error": message}, status)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def _send_file(self, path: str, download_name: str | None = None):
        try:
            size = os.path.getsize(path)
        except OSError:
            return self._error("file not found", 404)

        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = 200
        header = self.headers.get("Range")
        if header:
            match = RANGE_RE.match(header.strip())
            if match:
                first, last = match.group(1), match.group(2)
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:                     # suffix form: bytes=-N
                    start = max(0, size - int(last))
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_static(self, rel: str):
        rel = posixpath.normpath(rel).lstrip("/")
        path = os.path.join(WEB_DIR, rel)
        if not os.path.abspath(path).startswith(WEB_DIR) or not os.path.isfile(path):
            # The app owns its URLs (/sort, /new…): anything that is not a real
            # asset falls back to the page, which routes it client-side.
            if "." in posixpath.basename(rel):
                return self._error("not found", 404)
            path = os.path.join(WEB_DIR, "index.html")
        ctype = mimetypes.guess_type(path)[0] or "text/plain"
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # -------------------------------------------------------------- routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if route == "/":
            return self._send_static("index.html")
        if not route.startswith("/api/"):
            return self._send_static(route)

        session = get_session()

        if route == "/api/bootstrap":
            return self._send_json({
                "home": os.path.expanduser("~"),
                "suggestions": scanner.suggest_sources(),
                "session": session.snapshot() if session else None,
                "resumable": bool(session),
                # True when the session came from --source/--dest: the wizard
                # has nothing left to ask.
                "autostart": bool(state.get("autostart")),
            })

        if route == "/api/browse":
            return self._send_json(list_dir((query.get("path") or [os.path.expanduser("~")])[0]))

        if route == "/api/session":
            if not session:
                return self._error("no session", 404)
            return self._send_json(session.snapshot())

        if route == "/api/queue":
            if not session:
                return self._error("no session", 404)
            start = int((query.get("start") or [session.cursor])[0])
            count = min(int((query.get("count") or [6])[0]), 30)
            items, nxt = session.queue(start, count)
            return self._send_json({"start": start, "items": items, "next": nxt,
                                    "total": len(session.order)})

        if route.startswith("/api/media/"):
            if not session:
                return self._error("no session", 404)
            parts = route[len("/api/media/"):].split("/")
            if len(parts) != 2:
                return self._error("bad request", 400)
            item_id, which = urllib.parse.unquote(parts[0]), parts[1]
            item = session.by_id.get(item_id)
            if not item or which not in ("main", "overlay"):
                return self._error("media not found", 404)
            path = session.cache.path_for(item, which) if session.cache else None
            if not path:
                return self._error("media unreadable", 404)
            return self._send_file(path)

        if route == "/api/report":
            if not session:
                return self._error("no session", 404)
            return self._send_json({"markdown": session.report(),
                                    "path": os.path.join(session.dest, "REPORT.md")})

        return self._error("unknown route", 404)

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        body = self._read_json()
        session = get_session()

        if route == "/api/session/start":
            sources = [s for s in body.get("sources", []) if os.path.exists(s)]
            dest = body.get("dest") or ""
            if not sources:
                return self._error("no valid source selected")
            if not dest:
                return self._error("destination missing")
            dest = os.path.abspath(os.path.expanduser(dest))
            for src in sources:
                folder = os.path.abspath(src if os.path.isdir(src) else os.path.dirname(src))
                if dest == folder or dest.startswith(folder + os.sep):
                    return self._error(
                        "the destination sits inside a source: pick a separate folder "
                        "so you never re-read what you have just sorted"
                    )
            try:
                os.makedirs(dest, exist_ok=True)
            except OSError as exc:
                return self._error(f"destination unusable: {exc}")
            if not os.access(dest, os.W_OK):
                return self._error("destination is not writable")
            new = Session(dest)
            new.start(sources, body.get("options") or {})
            with state_lock:
                state["session"] = new
            return self._send_json({"ok": True, "dest": new.dest})

        if route == "/api/session/resume":
            dest = body.get("dest") or ""
            candidate = Session(dest)
            if not candidate.load():
                return self._error("no session found in that folder", 404)
            with state_lock:
                state["session"] = candidate
            return self._send_json({"ok": True, "session": candidate.snapshot()})

        if route == "/api/session/close":
            with state_lock:
                state["session"] = None
            return self._send_json({"ok": True})

        if not session:
            return self._error("no session", 404)

        if route == "/api/decide":
            try:
                session.decide(body.get("id"), body.get("action"), body.get("folder"))
            except (KeyError, ValueError) as exc:
                return self._error(str(exc))
            return self._send_json({"ok": True, "counts": session.counts(),
                                    "cursor": session.cursor, "folders": session.folders})

        if route == "/api/merge":
            try:
                session.merge(body.get("id"))
            except (KeyError, ValueError) as exc:
                return self._error(str(exc))
            return self._send_json({"ok": True, "counts": session.counts(),
                                    "cursor": session.cursor, "folders": session.folders})

        if route == "/api/undo":
            item_id = session.undo()
            return self._send_json({"ok": bool(item_id), "id": item_id,
                                    "counts": session.counts(), "cursor": session.cursor,
                                    "folders": session.folders})

        if route == "/api/replay":
            count = session.replay(body.get("action") or "skip")
            return self._send_json({"ok": True, "count": count, "cursor": session.cursor,
                                    "counts": session.counts(), "folders": session.folders})

        if route == "/api/seek":
            session.seek(int(body.get("index", 0)))
            return self._send_json({"ok": True, "cursor": session.cursor})

        if route == "/api/folders":
            folder = session.add_folder(body.get("name", ""), body.get("key"))
            return self._send_json({"ok": True, "folder": folder, "folders": session.folders})

        if route == "/api/folders/update":
            folder = session.update_folder(body.get("id"), body.get("name"),
                                           body.get("key"), body.get("color"))
            if not folder:
                return self._error("folder not found", 404)
            return self._send_json({"ok": True, "folders": session.folders})

        if route == "/api/folders/delete":
            session.delete_folder(body.get("id"))
            return self._send_json({"ok": True, "folders": session.folders})

        if route == "/api/options":
            options = body.get("options") or {}
            with session.lock:
                session.options.update(options)
                reorder = "order" in options
                if reorder:
                    session._apply_order()
            if reorder:
                session.save_index()
            session.save()
            return self._send_json({"ok": True, "options": session.options,
                                    "cursor": session.cursor})

        if route == "/api/reveal":
            reveal(body.get("path") or session.dest)
            return self._send_json({"ok": True})

        return self._error("unknown route", 404)


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

def find_port(preferred: int = 8765) -> int:
    for port in range(preferred, preferred + 40):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port between 8765 and 8805")


def serve(port: int | None = None, open_browser: bool = True, host: str = "127.0.0.1") -> None:
    port = port or find_port()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    shown = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0") else host
    url = f"http://{shown}:{port}"

    print("\n  \033[1;33m✦ Snapchat Memories Sorter\033[0m — sort your Snapchat memories")
    print(f"  \033[2m↳\033[0m  {url}")
    print("  \033[2mCtrl+C to quit\033[0m\n")

    # Terminal closed or `kill`: we want to go through the `finally` below.
    def _quit(_signum, _frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _quit)
            except (OSError, ValueError):
                pass

    if open_browser:
        import webbrowser

        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  See you soon!\n")
    finally:
        session = get_session()
        if session:
            session.save()  # no decision lost on shutdown
        httpd.shutdown()
