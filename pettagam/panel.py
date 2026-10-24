"""Point-and-click panel: a small local web page for choosing folders and running each step.

Stdlib only. Binds to 127.0.0.1 by default, so only this computer can open it. Requests must name this
computer as the host (which stops other websites reaching it through DNS rebinding), and on the network
every request needs the secret token from the start-up link.
"""

import contextlib
import errno
import hashlib
import json
import mimetypes
import os
import secrets
import shutil
import socket
import ssl
import string
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse, urlsplit

from . import __version__, library
from .apply import apply
from .db import connect as _connect, get_meta
from .errors import explain, is_bug, save_details
from .hashing import find_duplicates
from .plan import SPLIT_AT, plan
from .progress import Progress, Stopped, keep_awake
from .report import report
from .rules import default_path, load_rules
from .safeio import private_dir
from .scan import scan
from .verify import verify

HOME = os.path.join(os.path.expanduser("~"), ".pettagam")
SETTINGS = os.path.join(HOME, "panel.json")
DEFAULTS = {"source": "", "library": "", "inbox": "", "db": os.path.join(HOME, "pettagam.db"),
            "report": os.path.join(HOME, "report"), "workers": 2, "split_at": SPLIT_AT}
LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}
MAX_BODY = 2 * 1024 * 1024  # the largest request the page sends is a rules file
# The page itself: only its own scripts and files, and it can't be framed by another site.
PAGE_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self'; frame-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'")


def clean_settings(body: dict, s: dict) -> dict:
    """Apply settings sent by the page, after checking them. Raises ValueError with a plain message."""
    out = dict(s)
    for k in ("source", "library", "inbox"):
        if k in body:
            v = body[k]
            if not isinstance(v, str) or "\x00" in v:
                raise ValueError(f"{k} must be a folder path")
            out[k] = os.path.abspath(os.path.expanduser(v)) if v.strip() else ""
    for k, lo, hi in (("workers", 1, 32), ("split_at", 100, 1_000_000)):
        if k in body:
            try:
                v = int(body[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a whole number") from None
            if not lo <= v <= hi:
                raise ValueError(f"{k} must be between {lo:,} and {hi:,}")
            out[k] = v
    return out


def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s: dict) -> None:
    private_dir(HOME)
    with open(SETTINGS + ".tmp", "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    os.replace(SETTINGS + ".tmp", SETTINGS)


# Every catalog connection a request or a step opens is closed when it is done. Python only frees an unused
# SQLite connection when its garbage collector next runs, which in the panel can take hours, so without this
# each page refresh left two files open until the panel hit the system's limit (256 on macOS) and SQLite
# failed with "unable to open database file".
_opened = threading.local()


def connect(path: str):
    con = _connect(path)
    _opened.__dict__.setdefault("cons", []).append(con)
    return con


def close_connections() -> None:
    """Close the catalog connections this thread opened."""
    for con in _opened.__dict__.pop("cons", []):
        with contextlib.suppress(Exception):
            con.close()


def more_open_files(want: int = 10240) -> None:
    """Raise this process's open-file limit towards WANT (macOS starts programs at 256)."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        new = want if hard == resource.RLIM_INFINITY else min(want, hard)
        if soft != resource.RLIM_INFINITY and soft < new:
            resource.setrlimit(resource.RLIMIT_NOFILE, (new, hard))
    except (ImportError, ValueError, OSError):
        pass


STEP_NAMES = {"dryrun": "Dry run", "apply": "Placing files", "verify": "Sync check", "inbox": "Sorting the inbox",
              "views": "Building views", "tag": "AI tagging", "undo": "Undo", "fixity": "Damage check",
              "bench": "Measuring speed"}
# What stopping part-way leaves behind, said in the log.
STOPPED_NOTES = {
    "dryrun": "The dry run was stopped before it finished, so the plan is incomplete. Start it again when you're ready.",
    "apply": "Files placed so far are kept. Place files again to carry on where it stopped.",
    "verify": "The sync check was stopped before it finished. Nothing was changed.",
    "tag": "Tags added so far are kept, and nothing more is sent. Add AI tags again to carry on.",
    "fixity": "Files checked so far are recorded. Check for damage again to carry on.",
    "inbox": "Files filed so far stay in the library; the rest are still in the inbox.",
}
NOTIFY_AFTER = 60  # seconds: a step that ran longer says on the desktop when it ends


class Job:
    """One step at a time, run in the background, with a log the page polls."""

    def __init__(self):
        self.lock = threading.Lock()
        self.name = None
        self.running = False
        self.log: list[str] = []
        self.started = self.finished = None
        self.result = None
        self.progress = None

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

        self.progress = Progress(sink=self.say)

        def run():
            try:
                with keep_awake():
                    self.result = fn(self.progress)
                self.say("Finished.")
            except Stopped:
                self.result = {"stopped": True}
                self.say("Stopped. " + STOPPED_NOTES.get(name, ""))
            except SystemExit as e:
                self.result = {"error": str(e)}
                self.say(f"Stopped: {e}")
            except Exception as e:  # report anything to the page instead of dying silently
                self.result = {"error": explain(e)}
                self.say(f"Error: {explain(e)}")
                if is_bug(e):
                    where = save_details(e, f"panel step: {name}")
                    self.say(f"This looks like a bug in Pettagam. The details are in {where}." if where
                             else f"Details: {e!r}")
            finally:
                close_connections()
                with self.lock:
                    self.running, self.finished = False, time.time()
                self._tell_desktop()

        threading.Thread(target=run, daemon=True).start()
        return True

    def stop(self) -> bool:
        """Ask the running step to stop at its next progress line (within about 10 seconds)."""
        with self.lock:
            if not self.running or not self.progress:
                return False
            self.progress.stop()
            self.log.append(f"{time.strftime('%H:%M:%S')}  Stopping…")
            return True

    def _tell_desktop(self) -> None:
        """After a long step, a desktop notification, so nobody has to keep watching the page."""
        if not (self.started and self.finished and self.finished - self.started >= NOTIFY_AFTER):
            return
        from .notify import notify
        r, step = self.result or {}, STEP_NAMES.get(self.name, self.name)
        text = (f"{step} stopped." if r.get("stopped") else f"{step} failed: {r['error']}" if r.get("error")
                else f"{step} finished.")
        notify("Pettagam", text[:200])

    def snapshot(self) -> dict:
        with self.lock:
            prog = self.progress
            return {"name": self.name, "running": self.running, "log": list(self.log),
                    "started": self.started, "finished": self.finished, "result": self.result,
                    "percent": prog.percent if prog and self.running else None,
                    "eta": prog.eta if prog and self.running else None,
                    "stopping": bool(prog and self.running and prog.stop_event.is_set())}


JOB = Job()
BENCH_SEARCH_ROWS = 200_000  # the panel's speed check keeps the made-up catalog small, so it finishes quickly


def _steps(action: str, s: dict):
    def dryrun(say):
        con = connect(s["db"])
        try:
            scan(con, s["source"], workers=int(s["workers"]), progress=say)
            find_duplicates(con, workers=int(s["workers"]), progress=say)
            rules = load_rules(default_path(s["library"]))
            if rules:
                say(f"Using {len(rules)} of your rules from {default_path(s['library'])}")
            counts = plan(con, s["library"], split_at=int(s["split_at"]), rules=rules, progress=say)
            report(con, s["report"], progress=say)
            return counts
        finally:
            con.close()  # a step that fails must not keep the catalog locked

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

    def do_undo(say):
        from .maintenance import undo
        return undo(connect(s["db"]), progress=say)

    def do_fixity(say):
        from .fixity import check
        res = check(connect(s["db"]), s["library"], minutes=60, progress=say)
        return {k: (v[:200] if isinstance(v, list) else v) for k, v in res.items()}

    def do_bench(say):
        from . import bench
        source = s["source"] if os.path.isdir(s["source"] or "") else None
        lib = s["library"] if os.path.isdir(s["library"] or "") else None
        res = bench.run(source, lib, files=2000, workers=int(s["workers"]), search_rows=BENCH_SEARCH_ROWS, progress=say)
        from .db import set_meta
        set_meta(connect(s["db"]), "bench_last", json.dumps({"at": time.time(), **res}))
        lines = bench.verdicts(res)
        for name, got, target, ok in lines:
            say(f"  {'' if ok is None else 'OK  ' if ok else 'SLOW'} {name}: {got} (target {target})")
        return {"lines": [{"name": n, "got": g, "target": t, "ok": ok} for n, g, t, ok in lines]}

    steps = {"dryrun": dryrun, "apply": do_apply, "verify": do_verify, "inbox": do_inbox,
             "views": do_views, "tag": do_tag, "undo": do_undo, "fixity": do_fixity, "bench": do_bench}

    def with_backup(say):
        result = steps[action](say)
        if action in ("apply", "inbox", "tag", "undo"):
            from .maintenance import backup_catalog
            try:
                backup_catalog(connect(s["db"]))
            except Exception as e:  # a missing copy must not turn a good result into an error
                say(f"Note: could not save the catalog copy in the library: {e}")
        return result

    return with_backup


# --- Updating and restarting ------------------------------------------------------

RESTART: list = []  # how this panel was started, so it can start itself again (set by serve)
STARTED = {"stamp": 0.0}  # when the newest Pettagam file was changed, as the panel started
UPDATE_CHECK: dict = {}  # the last look for updates on GitHub, kept for an hour
UPDATE_EVERY = 3600


def app_state() -> dict:
    """The running version, and whether newer code is on disk (after a git pull) so a restart would use it."""
    from . import update
    on_disk = update.version_on_disk()
    newer = bool(STARTED["stamp"]) and update.code_stamp() > STARTED["stamp"] + 1
    return {"version": __version__, "on_disk": on_disk or __version__, "restart_needed": newer,
            "can_restart": bool(RESTART), "update": UPDATE_CHECK.get("result"), "platform": sys.platform}


def update_check(force: bool = False) -> dict:
    from . import update
    if force or not UPDATE_CHECK or time.time() - UPDATE_CHECK["at"] > UPDATE_EVERY:
        UPDATE_CHECK.update(at=time.time(), result=update.check())
    return UPDATE_CHECK["result"]


def restart_soon(delay: float = 0.5) -> None:
    """Start this panel again in the same process and Terminal window, after the reply has gone out."""
    def again():
        time.sleep(delay)
        from .update import CODE
        print("\nRestarting the panel with the updated Pettagam…", flush=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = CODE + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        os.execve(sys.executable, [sys.executable, "-m", "pettagam", *RESTART], env)  # the server's socket closes on exec

    threading.Thread(target=again, daemon=True).start()


MAX_SELECTED = 100_000  # files changed by one bulk tag edit


def explore(s: dict, q: str = "", category: str = "", tag: str = "", year: str = "",
            offset: int = 0, limit: int = 120, day: str = "", folder: str = "", sort: str = "newest") -> dict:
    """Search the finished library: by words in the name, folder, tags or description."""
    if not os.path.exists(s["db"]):
        if sort not in library.SORTS:
            raise ValueError(f"sort must be one of {', '.join(library.SORTS)}")
        library.clean_folder(folder)
        return {"items": [], "total": 0}
    return library.search(connect(s["db"]), q, category, tag, year, offset, limit, day, folder, sort)


def search_args(q: dict) -> dict:
    """The search in a query string or request body, as keyword arguments for library.search."""
    return {k: str(q.get(k, "") or "") for k in ("q", "category", "tag", "year", "day", "folder")} | \
        {"sort": str(q.get("sort") or "newest")}


def folders(s: dict, folder: str) -> dict:
    if not os.path.exists(s["db"]):
        return {"folder": library.clean_folder(folder), "folders": [], "files_here": 0, "bytes_here": 0,
                "files": 0, "bytes": 0}
    return library.folders(connect(s["db"]), folder)


def bulk_tags(s: dict, body: dict) -> tuple:
    """Add or remove tags on many files at once: the ones chosen in Explore (ITEMS), or every file a search
    finds (MATCH). Returns (status, reply)."""
    add, remove, items, match = body.get("add", []), body.get("remove", []), body.get("items"), body.get("match")
    words = lambda v: isinstance(v, list) and all(isinstance(t, str) for t in v)
    if not (words(add) and words(remove)) or not (add or remove) or (items is None) == (match is None):
        return 400, {"error": "bad request"}
    if items is not None and not (isinstance(items, list) and len(items) <= MAX_SELECTED and all(
            isinstance(i, dict) and i.get("src") in library.IN_LIBRARY and type(i.get("id")) is int for i in items)):
        return 400, {"error": "bad request"}
    if match is not None and not isinstance(match, dict):
        return 400, {"error": "bad request"}
    if not os.path.exists(s["db"]):
        return 404, {"error": "not found"}
    con = connect(s["db"])
    if match is not None:
        try:
            total = library.search(con, **search_args(match), limit=0)["total"]
            if total > MAX_SELECTED:
                return 400, {"error": f"That is {total:,} files; narrow the search to {MAX_SELECTED:,} or fewer."}
            found = library.search(con, **search_args(match), limit=max(total, 1))["items"]
        except ValueError as e:
            return 400, {"error": str(e)}
        pairs = [(r["src"], r["id"]) for r in found]
    else:
        pairs = [(i["src"], i["id"]) for i in items]
    return 200, {"changed": library.edit_tags(con, pairs, add=add, remove=remove), "files": len(pairs)}


REVEAL = subprocess.run  # replaced in tests


def reveal(s: dict, rel: str) -> tuple:
    """Show a library file in Finder (macOS) or open its folder in the file manager (Linux)."""
    p = library_file(s, rel)
    if not p:
        return 404, {"error": "not found"}
    if sys.platform == "darwin":
        cmd = ["open", "-R", p]
    elif shutil.which("xdg-open"):
        cmd = ["xdg-open", os.path.dirname(p)]
    else:
        return 400, {"error": "No file manager to open on this computer."}
    REVEAL(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 200, {"shown": p}


def edit_tags(s: dict, body: dict) -> tuple:
    """Tags and description typed in Explore's file details. Returns (status, reply)."""
    if "items" in body or "match" in body:
        return bulk_tags(s, body)
    tags, desc = body.get("tags"), body.get("description")
    if body.get("src") not in library.IN_LIBRARY or not isinstance(body.get("id"), int) \
            or not (isinstance(tags, list) and all(isinstance(t, str) for t in tags)) \
            or not (desc is None or isinstance(desc, str)):
        return 400, {"error": "bad request"}
    if not os.path.exists(s["db"]):
        return 404, {"error": "not found"}
    con = connect(s["db"])
    library.edit_tags(con, [(body["src"], body["id"])], tags=tags, description=desc)
    row = con.execute(f"SELECT tags, description FROM {body['src']} WHERE id=? AND "
                      f"{library.IN_LIBRARY[body['src']]}", (body["id"],)).fetchone()
    if not row:
        return 404, {"error": "not found"}
    return 200, {"tags": row[0] or "", "description": row[1] or ""}


def facets(s: dict) -> dict:
    if not os.path.exists(s["db"]):
        return {"categories": [], "tags": [], "years": []}
    return library.facets(connect(s["db"]))


def rules_file(s: dict) -> dict:
    """The rules file for the editor: its text, or the starter examples when there is none yet."""
    from .rules import STARTER
    path = default_path(s.get("library"))
    if not path:
        return {"path": None, "text": "", "exists": False}
    try:
        with open(path, encoding="utf-8") as f:
            return {"path": path, "text": f.read(), "exists": True}
    except OSError:
        return {"path": path, "text": json.dumps(STARTER, indent=2) + "\n", "exists": False}


def check_rules(s: dict, text: str, save: bool = False) -> dict:
    """Validate rules text, count what each rule would place, and save it when asked and valid."""
    from .plan import rule_counts
    from .rules import RuleError, parse_rules
    try:
        data = json.loads(text)
    except ValueError as e:
        return {"ok": False, "error": f"Not valid JSON: {e}"}
    try:
        rules = parse_rules(data)
    except RuleError as e:
        return {"ok": False, "error": str(e)}
    counts = rule_counts(connect(s["db"]), rules) if os.path.exists(s["db"]) else {}
    off = len(data["rules"]) - len(rules)
    res = {"ok": True, "off": off, "rules": [{"name": r.name, "then": r.then, "files": counts.get(r.name, 0)}
                                             for r in rules]}
    if save:
        path = default_path(s.get("library"))
        if not path:
            return {"ok": False, "error": "Choose the library folder first."}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        os.replace(path + ".tmp", path)
        res["saved"] = path
    return res


def library_file(s: dict, rel: str):
    """Absolute path of a file inside the library, or None (never anything outside it)."""
    lib = s.get("library")
    if not lib or not rel:
        return None
    root = os.path.realpath(lib)
    p = os.path.realpath(os.path.join(root, rel))
    return p if p.startswith(root + os.sep) and os.path.isfile(p) else None


# Small pictures for Explore's grid, so a page of photos doesn't read every full-size file off the drive. macOS
# makes them with sips (which also turns HEIC and TIFF into JPEG, which every browser can show); elsewhere the
# file itself is sent. They are kept in ~/.pettagam/thumbs, oldest dropped past THUMB_CACHE bytes.
THUMB_SIZES = (320, 1600)
THUMB_CACHE = 1 << 30
THUMB_SLOTS = threading.BoundedSemaphore(2)  # a spinning drive reads one or two files at a time well
_thumbs_made = [0]


def _sips(src: str, out: str, size: int) -> bool:
    r = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "75", "-Z", str(size), src,
                        "--out", out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
    return r.returncode == 0 and os.path.getsize(out) > 0


MAKE_THUMB = _sips if sys.platform == "darwin" and shutil.which("sips") else None


def thumbnail(s: dict, rel: str, size: int = THUMB_SIZES[0]) -> str | None:
    """A small JPEG of a library picture (made once, then kept), or the file itself when no small one can be
    made. None when REL is not a library file."""
    p = library_file(s, rel)
    if not p or size not in THUMB_SIZES:
        return None
    if not MAKE_THUMB:
        return p
    st = os.stat(p)
    key = hashlib.sha256(f"{p}\0{st.st_size}\0{st.st_mtime_ns}\0{size}".encode()).hexdigest()[:40]
    folder = os.path.join(HOME, "thumbs", key[:2])
    out = os.path.join(folder, key + ".jpg")
    if os.path.exists(out):
        return out
    private_dir(os.path.join(HOME, "thumbs"))
    os.makedirs(folder, exist_ok=True)
    tmp = f"{out}.{threading.get_ident()}.tmp.jpg"
    with THUMB_SLOTS:
        try:
            ok = MAKE_THUMB(p, tmp, size)
        except (OSError, subprocess.SubprocessError):
            ok = False
        if not ok:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            return p
        os.replace(tmp, out)
    _thumbs_made[0] += 1
    if _thumbs_made[0] % 500 == 0:
        prune_thumbs()
    return out


def prune_thumbs(limit: int = None) -> int:
    """Drop the oldest small pictures until the cache is under LIMIT bytes. Returns how many went."""
    limit = THUMB_CACHE if limit is None else limit
    found = []
    for root, _, names in os.walk(os.path.join(HOME, "thumbs")):
        for n in names:
            with contextlib.suppress(OSError):
                st = os.stat(os.path.join(root, n))
                found.append((st.st_mtime, st.st_size, os.path.join(root, n)))
    total, gone = sum(f[1] for f in found), 0
    for _, size, path in sorted(found):
        if total <= limit:
            break
        with contextlib.suppress(OSError):
            os.remove(path)
            total -= size
            gone += 1
    return gone


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
    try:
        verified = json.loads(get_meta(con, "verified") or "{}")
    except ValueError:
        verified = {}
    return {"planned": True, "source": get_meta(con, "source"), "library": get_meta(con, "library"),
            "total": total, "bytes": size, "actions": actions, "folders": folders,
            "placed": placed, "errors": errors, "verified": verified,
            "applied_at": float(get_meta(con, "applied_at") or 0)}


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
    secure = False  # served over HTTPS

    def _authorized(self, query: dict) -> bool:
        """On this computer, the request must be addressed to this computer by name. On the network,
        every request needs the secret from the start-up link (kept in a cookie)."""
        if not self.token:
            try:
                host = urlsplit("//" + self.headers.get("Host", "")).hostname
            except ValueError:
                return False
            return host in LOCAL_NAMES
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
            self.send_header("Set-Cookie", f"pettagam={self.token}; HttpOnly; SameSite=Strict; Path=/"
                             + ("; Secure" if self.secure else ""))
        self.send_header("Content-Type", f"{ctype}; charset=utf-8" if ctype.startswith(("text/", "application/json"))
                         else ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._safety_headers(PAGE_CSP)
        self.end_headers()
        self.wfile.write(data)

    def _safety_headers(self, csp: str, frame: str = "DENY") -> None:
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", frame)
        self.send_header("Referrer-Policy", "no-referrer")

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
        # A library file may be a web page or an SVG with scripts in it. Shown on its own, it runs in a
        # sandbox with no access to the panel. PDFs are left out: browsers won't show them sandboxed.
        is_pdf = path.lower().endswith(".pdf")
        self._safety_headers("frame-ancestors 'self'" if is_pdf else "sandbox; frame-ancestors 'self'", "SAMEORIGIN")
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
        try:
            self._get()
        finally:
            close_connections()

    def do_POST(self):
        try:
            self._post()
        finally:
            close_connections()

    def _get(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/hello":  # lets a second start find this panel instead of failing; says nothing else
            return self._send(200, {"app": "pettagam", "version": __version__})
        if not self._authorized(q):
            return self._send(403, "Open the panel with the link printed when it started.", "text/plain")
        s = load_settings()
        if u.path == "/":
            html = resources.files("pettagam").joinpath("panel.html").read_text(encoding="utf-8")
            return self._send(200, html, "text/html", cookie=True)
        if u.path == "/api/state":
            return self._send(200, {"settings": s, "job": JOB.snapshot(), "summary": summary(s),
                                    "local": not self.token, "app": app_state()})
        if u.path == "/api/update":
            if self.token:
                return self._send(403, {"error": "Only from the computer running the panel."})
            return self._send(200, update_check(force=q.get("force") == "1"))
        if u.path == "/api/browse":
            return self._send(200, browse(q.get("path", "")))
        if u.path == "/api/plan":
            return self._send(200, plan_rows(s, q.get("q", ""), q.get("folder", "")))
        if u.path == "/api/explore":
            try:
                a = search_args(q)
                return self._send(200, explore(s, a.pop("q"), a.pop("category"), a.pop("tag"), a.pop("year"),
                                               int(q.get("offset", "0") or 0), **a))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if u.path == "/api/folders":
            try:
                return self._send(200, folders(s, q.get("folder", "")))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if u.path == "/api/copies":
            if not (os.path.exists(s["db"]) and q.get("id", "").isdigit()):
                return self._send(200, {"copies": [], "total": 0})
            return self._send(200, library.copies_of(connect(s["db"]), q.get("src", ""), int(q["id"])))
        if u.path == "/api/rules":
            return self._send(200, rules_file(s))
        if u.path == "/api/stats":
            if not os.path.exists(s["db"]):
                return self._send(200, {"files": 0})
            return self._send(200, library.stats(connect(s["db"])))
        if u.path == "/api/facets":
            return self._send(200, facets(s))
        if u.path == "/api/doctor":
            from .doctor import run_checks
            con = connect(s["db"]) if os.path.exists(s["db"]) else None
            return self._send(200, run_checks(con, s["source"] or None, s["library"] or None,
                                              s.get("inbox") or None, s["db"]))
        if u.path == "/api/service":
            from . import service
            return self._send(200, {**service.status(), "log": service.last_log()})
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
        if u.path == "/api/thumb":
            p = thumbnail(s, q.get("path", ""), int(q["size"]) if q.get("size", "").isdigit() else THUMB_SIZES[0])
            if not p:
                return self._send(404, {"error": "not found"})
            return self._send_file(p)
        if u.path == "/api/report":
            p = os.path.join(s["report"], "report.md")
            return self._send(200, open(p, encoding="utf-8").read() if os.path.exists(p) else "", "text/plain")
        self._send(404, {"error": "not found"})

    def _post(self):
        # Only accept requests from the page itself, not from other websites.
        origin = self.headers.get("Origin", "")
        if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
            return self._send(403, {"error": "forbidden"})
        if not self._authorized({}):
            return self._send(403, {"error": "forbidden"})
        # Plain web forms can't send JSON, so this also turns away forms posted from other websites.
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self._send(415, {"error": "send JSON"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad request"})
        if length > MAX_BODY or length < 0:
            return self._send(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad request"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "bad request"})
        u = urlparse(self.path)
        s = load_settings()
        if u.path == "/api/settings":
            try:
                s = clean_settings(body, s)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            save_settings(s)
            return self._send(200, {"settings": s})
        if u.path == "/api/rules":
            if JOB.running and body.get("save"):
                return self._send(409, {"error": "Wait for the current step to finish."})
            return self._send(200, check_rules(s, str(body.get("text", "")), bool(body.get("save"))))
        if u.path == "/api/service":
            from . import service
            try:
                if body.get("on"):
                    if not (os.path.isdir(s.get("inbox") or "") and s["library"]):
                        return self._send(400, {"error": "Choose the inbox and library folders first."})
                    service.install(s["db"], s["inbox"], s["library"], every=int(body.get("every") or 300))
                else:
                    service.uninstall()
            except SystemExit as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, service.status())
        if u.path == "/api/tags":
            return self._send(*edit_tags(s, body))
        if u.path == "/api/reveal":
            if self.token:  # the file manager would open on the computer running the panel, not the viewer's
                return self._send(403, {"error": "Only from the computer running the panel."})
            return self._send(*reveal(s, str(body.get("path", ""))))
        if u.path == "/api/stop":
            return self._send(200, {"stopping": JOB.stop()})
        if u.path in ("/api/update", "/api/restart"):
            if self.token:
                return self._send(403, {"error": "Only from the computer running the panel."})
            if JOB.running:
                return self._send(409, {"error": "Wait for the current step to finish (or stop it) first."})
            res = {}
            if u.path == "/api/update":
                from . import update
                try:
                    res = update.update()
                except SystemExit as e:
                    return self._send(400, {"error": str(e)})
                UPDATE_CHECK.clear()
                if not res["updated"]:
                    return self._send(200, res)
            if not RESTART:
                return self._send(400, {"error": "Close the panel's Terminal window and open Pettagam again."})
            restart_soon()
            return self._send(200, {**res, "restarting": True})
        if u.path == "/api/run":
            action = body.get("action")
            if action not in ("dryrun", "apply", "verify", "inbox", "views", "tag", "undo", "fixity", "bench"):
                return self._send(400, {"error": "unknown action"})
            if action == "inbox" and not os.path.isdir(s.get("inbox") or ""):
                return self._send(400, {"error": "Choose the inbox folder first."})
            if action in ("inbox", "views", "tag", "fixity") and not s["library"]:
                return self._send(400, {"error": "Choose the library folder first."})
            if action == "dryrun" and not (os.path.isdir(s["source"]) and s["library"]):
                return self._send(400, {"error": "Choose the old-files folder and the library folder first."})
            if action in ("apply", "verify", "views", "tag", "undo") and not summary(s).get("planned"):
                return self._send(400, {"error": "Run the dry run first."})
            if not JOB.start(action, _steps(action, s)):
                return self._send(409, {"error": "Another step is still running."})
            return self._send(200, {"started": action})
        self._send(404, {"error": "not found"})


# --- HTTPS for network mode ------------------------------------------------------

def self_signed(directory: str = None) -> tuple:
    """A certificate and key for this computer in ~/.pettagam/tls/, made once with openssl and kept,
    so the browser's fingerprint stays the same between runs."""
    directory = directory or os.path.join(HOME, "tls")
    cert, key = os.path.join(directory, "panel.crt"), os.path.join(directory, "panel.key")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    openssl = shutil.which("openssl")
    if not openssl:
        raise SystemExit("HTTPS needs the openssl command to make a certificate (sudo apt install openssl), "
                         "or give your own with --tls-cert and --tls-key.")
    private_dir(directory)
    name = socket.gethostname() or "localhost"
    # DNS labels are at most 63 characters; some computers (and CI machines) have longer names.
    names = sorted(n for n in {"localhost", name, name.split(".")[0] + ".local"}
                   if all(0 < len(part) <= 63 for part in n.split(".")))
    san = ",".join([f"DNS:{n}" for n in names] + ["IP:127.0.0.1"])
    old = os.umask(0o077)  # the key is readable by you only
    try:
        base = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "825", "-nodes",
                "-keyout", key, "-out", cert, "-subj", "/CN=Pettagam panel"]  # names go in the SAN
        r = subprocess.run(base + ["-addext", f"subjectAltName={san}"], capture_output=True, text=True, timeout=60)
        if r.returncode:  # an older LibreSSL without -addext: the name alone still works with the warning
            r = subprocess.run(base, capture_output=True, text=True, timeout=60)
    finally:
        os.umask(old)
    if r.returncode:
        for p in (cert, key):
            if os.path.exists(p):
                os.remove(p)
        raise SystemExit("Could not make a certificate: " + (r.stderr.strip() or "openssl failed"))
    return cert, key


def fingerprint(cert: str) -> str:
    with open(cert, encoding="ascii") as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


def tls_context(cert: str, key: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as e:
        raise SystemExit(f"Could not use the certificate {cert}: {e}") from None
    return ctx


class TLSServer(ThreadingHTTPServer):
    """HTTPS. The handshake happens in the request's own thread, so a slow or broken client can't hold up others."""
    context = None

    def finish_request(self, request, client_address):
        request.settimeout(30)
        try:
            conn = self.context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):  # plain HTTP, a scanner, or a client that gave up
            request.close()
            return
        try:
            super().finish_request(conn, client_address)
        finally:
            conn.close()


def already_running(scheme: str, port: int) -> bool:
    """Is a Pettagam panel answering on this port? (A second double-click should open it, not fail.)"""
    import urllib.request
    ctx = ssl._create_unverified_context() if scheme == "https" else None  # our own certificate, on this computer
    try:
        with urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/api/hello", timeout=3, context=ctx) as r:
            return json.loads(r.read()).get("app") == "pettagam"
    except (OSError, ValueError):
        return False


def make_server(host: str, port: int, cert: str = None, key: str = None):
    if not cert:
        return ThreadingHTTPServer((host, port), Handler)
    httpd = TLSServer((host, port), Handler)
    httpd.context = tls_context(cert, key)
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8484, open_browser: bool = True, tls: bool = False,
          cert: str = None, key: str = None) -> None:
    private_dir(HOME)
    more_open_files()
    local = host in ("127.0.0.1", "localhost", "::1")
    Handler.token = None if local else secrets.token_urlsafe(16)
    if (cert is None) != (key is None):
        raise SystemExit("Give both --tls-cert and --tls-key.")
    if tls and not cert:
        cert, key = self_signed()
    Handler.secure = bool(cert)
    scheme = "https" if cert else "http"
    try:
        httpd = make_server(host, port, cert, key)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        if already_running(scheme, port):
            url = f"{scheme}://localhost:{port}/"
            print(f"Pettagam is already open at {url}" + (" — opening it in your browser." if open_browser else ""))
            if open_browser and local:
                webbrowser.open(url)
            return
        raise SystemExit(f"Another program is using port {port}. Start the panel on another one, for example: "
                         f"pettagam panel --port {port + 1}") from None
    from . import update
    STARTED["stamp"] = update.code_stamp()
    RESTART[:] = ["panel", "--no-browser", "--host", host, "--port", str(port)] + (
        ["--tls-cert", cert, "--tls-key", key] if cert else [])
    name = "localhost" if local or host == "0.0.0.0" else host
    url = f"{scheme}://{name}:{port}/" + ("" if local else f"?token={Handler.token}")
    print(f"Pettagam panel running at {url}  (press Ctrl+C to stop)")
    if not local:
        print("Reachable from your network. Open the link above (with its token) from another computer,"
              " replacing 'localhost' with this machine's name or address.")
        if not cert:
            print("The connection is not encrypted. On a network you don't fully trust, use --tls or an SSH tunnel.")
    if cert:
        print(f"HTTPS certificate: {cert}\n  SHA-256 fingerprint {fingerprint(cert)}\n"
              "  A certificate made by Pettagam is not known to browsers: the first time, the browser warns."
              " Check that it shows this fingerprint, then continue.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nThe panel has stopped. You can close this window.")
