"""Health check: is everything set up so Pettagam can do its job well?

Each check gives ok / warn / fail with one plain sentence and, when something is off, what to do.
Nothing is changed, except that a tiny test file is written to the library folder and removed again
to see whether hardlinks work there.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile

from .db import get_meta


def _check(status: str, name: str, text: str, fix: str = "") -> dict:
    return {"status": status, "name": name, "text": text, "fix": fix}


def _existing_parent(path: str) -> str:
    path = os.path.abspath(path)
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path


def _gb(n: float) -> str:
    return f"{n / 1e9:,.1f} GB"


def hardlinks_work(folder: str) -> bool:
    """Try making a hardlink in folder (some drives, like exFAT, can't)."""
    try:
        fd, a = tempfile.mkstemp(prefix=".pettagam-check-", dir=folder)
        os.close(fd)
    except OSError:
        return False
    b = a + "-link"
    try:
        os.link(a, b)
        return True
    except OSError:
        return False
    finally:
        for p in (a, b):
            try:
                os.remove(p)
            except OSError:
                pass


def run_checks(con=None, source: str = None, library: str = None, inbox: str = None,
               db_path: str = None) -> list:
    source = source or (get_meta(con, "source") if con else None)
    library = library or (get_meta(con, "library") if con else None)
    out = []

    v = sys.version_info
    out.append(_check("ok" if v >= (3, 9) else "fail", "Python", f"Python {v.major}.{v.minor}.{v.micro}",
                      "" if v >= (3, 9) else "Install Python 3.9 or newer."))

    if not source:
        out.append(_check("warn", "Old files", "Not chosen yet.", "Run a dry run, or choose the folder in the panel."))
    elif not os.path.isdir(source):
        out.append(_check("fail", "Old files", f"{source} is not there.",
                          "Plug in the drive. If it is mounted somewhere else now, use `pettagam relocate`."))
    else:
        out.append(_check("ok", "Old files", f"{source} is reachable."))

    lib_ok = False
    if not library:
        out.append(_check("warn", "Library", "Not chosen yet.", "Choose where the new library goes."))
    else:
        where = _existing_parent(library)
        if not os.access(where, os.W_OK):
            out.append(_check("fail", "Library", f"Can't write to {where}.",
                              "Pick a folder you can write to, or check the drive isn't read-only."))
        else:
            lib_ok = True
            out.append(_check("ok", "Library", f"{library} can be written to."))

    if lib_ok:
        where = _existing_parent(library)
        same = bool(source) and os.path.isdir(source) and os.stat(source).st_dev == os.stat(where).st_dev
        links = hardlinks_work(where)
        if same and links:
            out.append(_check("ok", "Placement", "Same drive as the old files and hardlinks work: placing is instant "
                                                 "and uses no extra space."))
        elif same:
            out.append(_check("warn", "Placement", "Same drive, but this drive can't make hardlinks (exFAT or FAT?). "
                                                   "Files will be copied, using extra space.",
                              "Reformat the drive as APFS (Mac only) or ext4 (Ubuntu) to save space, if that suits you."))
        elif source:
            out.append(_check("warn", "Placement", "The library is on a different drive from the old files, so every "
                                                   "file will be copied.",
                              "Put the library on the same drive as the old files to place them instantly."))
        free = shutil.disk_usage(where).free
        need = 0
        if con is not None and source and not same:
            need = con.execute("SELECT COALESCE(SUM(size),0) FROM files WHERE action='place' "
                               "AND (status IS NULL OR status='error')").fetchone()[0]
        if need and free < need * 1.05:
            out.append(_check("fail", "Free space", f"{_gb(free)} free, but copying needs about {_gb(need)}.",
                              "Free up space or use a bigger drive."))
        else:
            out.append(_check("ok" if free > 5e9 else "warn", "Free space", f"{_gb(free)} free on the library drive"
                              + (f" (copying needs {_gb(need)})." if need else "."),
                              "" if free > 5e9 else "Less than 5 GB left; the inbox and views need a little room."))

    if db_path:
        dbdir = _existing_parent(os.path.dirname(os.path.abspath(db_path)))
        on_lib = bool(library) and lib_ok and os.stat(dbdir).st_dev == os.stat(_existing_parent(library)).st_dev
        if on_lib and source and os.path.isdir(source) and os.stat(dbdir).st_dev == os.stat(source).st_dev:
            out.append(_check("warn", "Catalog", f"{db_path} is on the same drive as the files.",
                              "Keep the catalog on the computer's internal SSD; it's much faster."))
        else:
            out.append(_check("ok", "Catalog", f"{db_path}"))

    if inbox:
        if not os.path.isdir(inbox):
            out.append(_check("warn", "Inbox", f"{inbox} is not there.", "Create it, or choose another folder."))
        else:
            out.append(_check("ok", "Inbox", f"{inbox} is ready."))
    try:
        from .service import status as service_status
        st = service_status()
        out.append(_check("ok", "Automatic inbox", st["text"]) if st["installed"] else
                   _check("ok", "Automatic inbox", "Off. The inbox is sorted when you ask."))
    except Exception:  # never let the health check itself fail
        pass

    has_sdk = importlib.util.find_spec("anthropic") is not None
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                   or os.path.isdir(os.path.expanduser("~/.config/anthropic")))
    if has_sdk and has_key:
        out.append(_check("ok", "AI tagging", "Ready (the anthropic library and a key or login are present)."))
    else:
        missing = [m for m, ok in (("python3 -m pip install anthropic", has_sdk),
                                   ("set ANTHROPIC_API_KEY or run `ant auth login`", has_key)) if not ok]
        out.append(_check("ok", "AI tagging", "Optional, not set up.", "To use it: " + "; ".join(missing) + "."))
    return out


def print_checks(checks: list, progress=print) -> int:
    marks = {"ok": "✓", "warn": "!", "fail": "✗"}
    for c in checks:
        progress(f"  {marks[c['status']]} {c['name']}: {c['text']}")
        if c["fix"] and c["status"] != "ok":
            progress(f"      → {c['fix']}")
    worst = "fail" if any(c["status"] == "fail" for c in checks) else (
        "warn" if any(c["status"] == "warn" for c in checks) else "ok")
    progress({"ok": "All good.", "warn": "Works, with the notes above.", "fail": "Fix the ✗ items first."}[worst])
    return {"ok": 0, "warn": 0, "fail": 2}[worst]
