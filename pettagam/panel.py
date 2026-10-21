"""Point-and-click panel: a small local web page for choosing folders and running each step.

Stdlib only. Binds to 127.0.0.1 by default, so only this computer can open it.
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import string
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from .apply import apply
from .db import connect, get_meta
from .hashing import find_duplicates
from .plan import SPLIT_AT, plan
from .report import report
from .rules import default_path, load_rules
from .scan import scan
from .verify import verify

HOME = os.path.join(os.path.expanduser("~"), ".pettagam")
SETTINGS = os.path.join(HOME, "panel.json")
DEFAULTS = {"source": "", "library": "", "inbox": "", "db": os.path.join(HOME, "pettagam.db"),
            "report": os.path.join(HOME, "report"), "workers": 2, "split_at": SPLIT_AT}


def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s: dict) -> None:
    os.makedirs(HOME, exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


class Job:
    """One step at a time, run in the background, with a log the page polls."""

    def __init__(self):
        self.lock = threading.Lock()
        self.name = None
        self.running = False
        self.log: list[str] = []
        self.started = self.finished = None
        self.result = None

    def say(self, msg: str) -> None:
        with self.lock:
            self.log.append(f"{time.strftime('%H:%M:%S')}  {msg}")
            del self.log[:-500]

    def start(self, name: str, fn) -> bool:
        with self.lock:
            if self.running:
                return False
            self.name, self.running, self.log = name, True, []
            self.started, self.finished, self.result = time.time(), None, None

        def run():
            try:
                self.result = fn(self.say)
                self.say("Finished.")
            except SystemExit as e:
                self.result = {"error": str(e)}
                self.say(f"Stopped: {e}")
            except Exception as e:  # report anything to the page instead of dying silently
                self.result = {"error": repr(e)}
                self.say(f"Error: {e!r}")
            finally:
                with self.lock:
                    self.running, self.finished = False, time.time()

        threading.Thread(target=run, daemon=True).start()
        return True

    def snapshot(self) -> dict:
        with self.lock:
            return {"name": self.name, "running": self.running, "log": list(self.log),
                    "started": self.started, "finished": self.finished, "result": self.result}


JOB = Job()


def _steps(action: str, s: dict):
    def dryrun(say):
        con = connect(s["db"])
        scan(con, s["source"], workers=int(s["workers"]), progress=say)
        find_duplicates(con, workers=int(s["workers"]), progress=say)
        rules = load_rules(default_path(s["library"]))
        if rules:
            say(f"Using {len(rules)} of your rules from {default_path(s['library'])}")
        counts = plan(con, s["library"], split_at=int(s["split_at"]), rules=rules, progress=say)
        report(con, s["report"], progress=say)
        return counts

    def do_apply(say):
        con = connect(s["db"])
        return apply(con, workers=int(s["workers"]), progress=say)

    def do_verify(say):
        con = connect(s["db"])
        res = verify(con, rescan=True, progress=say)
        res["problems"] = {k: v[:200] for k, v in res["problems"].items()}
        return res

    def do_inbox(say):
        from .inbox import sort_inbox
        con = connect(s["db"])
        return sort_inbox(con, s["inbox"], s["library"], load_rules(default_path(s["library"])), progress=say)

    def do_views(say):
        from .views import build_views
        return build_views(connect(s["db"]), s["library"], progress=say)

    def do_tag(say):
        from .tag import tag_library
        return tag_library(connect(s["db"]), s["library"], workers=int(s["workers"]) * 2, progress=say)

    return {"dryrun": dryrun, "apply": do_apply, "verify": do_verify, "inbox": do_inbox,
            "views": do_views, "tag": do_tag}[action]


EXPLORE_SQL = """
SELECT 'files' AS src, id, dest, name, size, mtime, taken, camera, width, height, category, subtype, rule,
       tags, description, path AS original FROM files WHERE action='place' AND status='done'
UNION ALL
SELECT 'inbox', id, dest, replace(dest, rtrim(dest, replace(dest, '/', '')), ''), size, mtime, taken, camera,
       width, height, category, subtype, rule, tags, description, original FROM inbox WHERE action='placed'
"""


def explore(s: dict, q: str = "", category: str = "", tag: str = "", year: str = "",
            offset: int = 0, limit: int = 120) -> dict:
    """Search the finished library: by words in the name, folder, tags or description."""
    if not os.path.exists(s["db"]):
        return {"items": [], "total": 0}
    con = connect(s["db"])
    where, args = ["1=1"], []
    for word in q.split():
        where.append("(dest LIKE ? OR tags LIKE ? OR description LIKE ? OR camera LIKE ?)")
        args += [f"%{word}%"] * 4
    if category:
        where.append("(category=? OR dest LIKE ?)")
        args += [category, f"{category}/%"]
    if tag:
        where.append("(','||tags||',') LIKE ?")
        args.append(f"%,{tag},%")
    if year:
        where.append("strftime('%Y', COALESCE(taken, mtime), 'unixepoch') = ?")
        args.append(year)
    w = " AND ".join(where)
    total = con.execute(f"SELECT COUNT(*) FROM ({EXPLORE_SQL}) WHERE {w}", args).fetchone()[0]
    rows = con.execute(f"SELECT * FROM ({EXPLORE_SQL}) WHERE {w} ORDER BY COALESCE(taken, mtime) DESC, dest "
                       f"LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


def facets(s: dict) -> dict:
    if not os.path.exists(s["db"]):
        return {"categories": [], "tags": [], "years": []}
    con = connect(s["db"])
    base = f"({EXPLORE_SQL})"
    cats = [list(r) for r in con.execute(
        f"SELECT category, COUNT(*) FROM {base} WHERE category IS NOT NULL GROUP BY category ORDER BY 2 DESC")]
    years = [r[0] for r in con.execute(
        f"SELECT DISTINCT strftime('%Y', COALESCE(taken, mtime), 'unixepoch') AS y FROM {base} ORDER BY y DESC")]
    counts = {}
    for (tags,) in con.execute(f"SELECT tags FROM {base} WHERE tags IS NOT NULL AND tags != ''"):
        for t in tags.split(","):
            counts[t] = counts.get(t, 0) + 1
    tags = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:200]
    return {"categories": cats, "tags": [list(t) for t in tags], "years": years}


def library_file(s: dict, rel: str):
    """Absolute path of a file inside the library, or None (never anything outside it)."""
    lib = s.get("library")
    if not lib or not rel:
        return None
    root = os.path.realpath(lib)
    p = os.path.realpath(os.path.join(root, rel))
    return p if p.startswith(root + os.sep) and os.path.isfile(p) else None


def summary(s: dict) -> dict:
    if not os.path.exists(s["db"]):
        return {"planned": False}
    con = connect(s["db"])
    if not con.execute("SELECT 1 FROM files LIMIT 1").fetchone():
        return {"planned": False}
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    total, size = q("SELECT COUNT(*), COALESCE(SUM(size),0) FROM files")[0]
    actions = {r[0] or "unplanned": {"files": r[1], "bytes": r[2]} for r in
               q("SELECT action, COUNT(*), COALESCE(SUM(size),0) FROM files GROUP BY action")}
    folders = [{"folder": r[0], "files": r[1], "bytes": r[2], "done": r[3]} for r in q(
        "SELECT folder, COUNT(*), SUM(size), SUM(status='done') FROM files WHERE action='place' "
        "GROUP BY folder ORDER BY folder")]
    placed = q("SELECT COUNT(*) FROM files WHERE action='place' AND status='done'")[0][0]
    errors = q("SELECT COUNT(*) FROM files WHERE status='error'")[0][0]
    return {"planned": True, "source": get_meta(con, "source"), "library": get_meta(con, "library"),
            "total": total, "bytes": size, "actions": actions, "folders": folders,
            "placed": placed, "errors": errors}


def plan_rows(s: dict, query: str, folder: str, limit: int = 200) -> list:
    if not os.path.exists(s["db"]):
        return []
    con = connect(s["db"])
    where, args = ["1=1"], []
    if folder == "__duplicates":
        where.append("action='duplicate'")
    elif folder == "__junk":
        where.append("action='skip'")
    elif folder:
        where.append("folder=?")
        args.append(folder)
    if query:
        where.append("(path LIKE ? OR dest LIKE ?)")
        args += [f"%{query}%", f"%{query}%"]
    rows = con.execute(
        f"SELECT path, dest, action, folder, confidence, reason, rule, size, junk, status FROM files "
        f"WHERE {' AND '.join(where)} ORDER BY dest, path LIMIT ?", (*args, limit)).fetchall()
    return [dict(r) for r in rows]


def browse(path: str) -> dict:
    if not path:
        roots = ["/Volumes"] if os.path.isdir("/Volumes") else []
        roots += [p for p in ("/srv", "/mnt", "/media", os.path.expanduser("~")) if os.path.isdir(p)]
        if os.name == "nt":
            roots = [f"{c}:\\" for c in string.ascii_uppercase if os.path.isdir(f"{c}:\\")]
        return {"path": "", "parent": None, "dirs": [{"name": r, "path": r} for r in roots]}
    path = os.path.abspath(path)
    try:
        names = sorted((e.name for e in os.scandir(path) if e.is_dir() and not e.name.startswith(".")),
                       key=str.lower)
    except OSError as e:
        return {"path": path, "parent": os.path.dirname(path), "dirs": [], "error": str(e)}
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else "",
            "dirs": [{"name": n, "path": os.path.join(path, n)} for n in names]}


class Handler(BaseHTTPRequestHandler):
    server_version = "Pettagam"
    token = None  # set when the panel is reachable from the network

    def _authorized(self, query: dict) -> bool:
        """On the network, every request needs the secret from the start-up link (kept in a cookie)."""
        if not self.token:
            return True
        if secrets.compare_digest(query.get("token", ""), self.token):
            return True
        cookies = dict(c.strip().split("=", 1) for c in self.headers.get("Cookie", "").split(";") if "=" in c)
        return secrets.compare_digest(cookies.get("pettagam", ""), self.token)

    def log_message(self, *a):  # keep the terminal quiet
        pass

    def _send(self, code: int, body, ctype="application/json", cookie=False):
        data = body if isinstance(body, bytes) else (
            body.encode() if isinstance(body, str) else json.dumps(body).encode())
        self.send_response(code)
        if cookie and self.token:
            self.send_header("Set-Cookie", f"pettagam={self.token}; HttpOnly; SameSite=Strict; Path=/")
        self.send_header("Content-Type", f"{ctype}; charset=utf-8" if ctype.startswith(("text/", "application/json"))
                         else ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: str):
        """Stream a library file, with byte ranges so videos can be played and scrubbed."""
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes=") and "," not in rng:
            a, _, b = rng[6:].partition("-")
            try:
                if a:
                    start, end = int(a), min(int(b), size - 1) if b else size - 1
                elif b:
                    start = max(0, size - int(b))
            except ValueError:
                start, end = 0, size - 1
        if start > end and size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(206 if rng and (start, end) != (0, size - 1) else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._authorized(q):
            return self._send(403, "Open the panel with the link printed when it started.", "text/plain")
        s = load_settings()
        if u.path == "/":
            html = resources.files("pettagam").joinpath("panel.html").read_text(encoding="utf-8")
            return self._send(200, html, "text/html", cookie=True)
        if u.path == "/api/state":
            return self._send(200, {"settings": s, "job": JOB.snapshot(), "summary": summary(s)})
        if u.path == "/api/browse":
            return self._send(200, browse(q.get("path", "")))
        if u.path == "/api/plan":
            return self._send(200, plan_rows(s, q.get("q", ""), q.get("folder", "")))
        if u.path == "/api/explore":
            return self._send(200, explore(s, q.get("q", ""), q.get("category", ""), q.get("tag", ""),
                                           q.get("year", ""), int(q.get("offset", "0") or 0)))
        if u.path == "/api/facets":
            return self._send(200, facets(s))
        if u.path == "/api/estimate":
            from .tag import estimate
            if not os.path.exists(s["db"]):
                return self._send(200, {"files": 0, "usd": 0, "by_kind": {}})
            return self._send(200, estimate(connect(s["db"])))
        if u.path == "/api/file":
            p = library_file(s, q.get("path", ""))
            if not p:
                return self._send(404, {"error": "not found"})
            return self._send_file(p)
        if u.path == "/api/report":
            p = os.path.join(s["report"], "report.md")
            return self._send(200, open(p, encoding="utf-8").read() if os.path.exists(p) else "", "text/plain")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # Only accept requests from the page itself, not from other websites.
        origin = self.headers.get("Origin", "")
        if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
            return self._send(403, {"error": "forbidden"})
        if not self._authorized({}):
            return self._send(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad request"})
        u = urlparse(self.path)
        s = load_settings()
        if u.path == "/api/settings":
            for k in ("source", "library", "inbox", "workers", "split_at"):
                if k in body:
                    s[k] = body[k]
            save_settings(s)
            return self._send(200, {"settings": s})
        if u.path == "/api/run":
            action = body.get("action")
            if action not in ("dryrun", "apply", "verify", "inbox", "views", "tag"):
                return self._send(400, {"error": "unknown action"})
            if action == "inbox" and not os.path.isdir(s.get("inbox") or ""):
                return self._send(400, {"error": "Choose the inbox folder first."})
            if action in ("inbox", "views", "tag") and not s["library"]:
                return self._send(400, {"error": "Choose the library folder first."})
            if action == "dryrun" and not (os.path.isdir(s["source"]) and s["library"]):
                return self._send(400, {"error": "Choose the old-files folder and the library folder first."})
            if action in ("apply", "verify", "views", "tag") and not summary(s).get("planned"):
                return self._send(400, {"error": "Run the dry run first."})
            if not JOB.start(action, _steps(action, s)):
                return self._send(409, {"error": "Another step is still running."})
            return self._send(200, {"started": action})
        self._send(404, {"error": "not found"})


def serve(host: str = "127.0.0.1", port: int = 8484, open_browser: bool = True) -> None:
    os.makedirs(HOME, exist_ok=True)
    local = host in ("127.0.0.1", "localhost", "::1")
    Handler.token = None if local else secrets.token_urlsafe(16)
    httpd = ThreadingHTTPServer((host, port), Handler)
    name = "localhost" if local or host == "0.0.0.0" else host
    url = f"http://{name}:{port}/" + ("" if local else f"?token={Handler.token}")
    print(f"Pettagam panel running at {url}  (press Ctrl+C to stop)")
    if not local:
        print("Reachable from your network. Open the link above (with its token) from another computer,"
              " replacing 'localhost' with this machine's name or address.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
