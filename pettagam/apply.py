"""Phase 3: place files into the library. The source is only ever read.

Same filesystem: hardlink (instant, no extra space). Otherwise: copy to a .part file, flush it to disk,
check its size, then give it its name. Never replaces a file already in the library.
Resumable: files already marked done are skipped; an existing identical destination counts as done.
"""
from __future__ import annotations

import csv
import os
import time
from concurrent.futures import ThreadPoolExecutor

from .db import get_meta
from .safeio import BUSY, durable_copy, library_lock


def choose_method(source: str, library: str) -> str:
    os.makedirs(library, exist_ok=True)
    return "link" if os.stat(source).st_dev == os.stat(library).st_dev else "copy"


def _place(src: str, dst: str, method: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        s, d = os.stat(src), os.lstat(dst)
        if (s.st_dev, s.st_ino) == (d.st_dev, d.st_ino):
            return  # already linked
        if method == "copy" and d.st_size == s.st_size and int(d.st_mtime) == int(s.st_mtime):
            return  # already copied
        raise FileExistsError(f"destination exists and differs: {dst}")
    if method == "link":
        os.link(src, dst)
    else:
        durable_copy(src, dst, check="size")


def apply(con, workers: int = 4, method: str = "auto", progress=print) -> dict:
    source, library = get_meta(con, "source"), get_meta(con, "library")
    if not library:
        raise SystemExit("Run 'pettagam plan' first.")
    if method == "auto":
        method = choose_method(source, library)
    lock = library_lock(library)
    if lock is False:
        raise SystemExit(BUSY)
    try:
        return _apply(con, source, library, workers, method, progress)
    finally:
        if lock:
            lock.close()


def _apply(con, source, library, workers, method, progress) -> dict:
    progress(f"Placing files by {'hardlink' if method == 'link' else 'copy'} with {workers} workers")
    todo = con.execute(
        "SELECT id, path, dest FROM files WHERE action='place' AND (status IS NULL OR status='error') ORDER BY dest"
    ).fetchall()

    def one(row):
        try:
            _place(os.path.join(source, row["path"]), os.path.join(library, row["dest"]), method)
            return row["id"], "done", None
        except OSError as e:
            return row["id"], "error", str(e)

    t0, done, errors = time.time(), 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (fid, status, err) in enumerate(pool.map(one, todo), 1):
            con.execute("UPDATE files SET status=?, method=?, error=? WHERE id=?", (status, method, err, fid))
            done += status == "done"
            errors += status == "error"
            if i % 2000 == 0:
                con.commit()
                progress(f"  {i:,}/{len(todo):,} placed ({i / (time.time() - t0):,.0f}/s)")
    con.commit()
    write_manifest(con, library)
    progress(f"Placed {done:,} files in {time.time() - t0:,.1f}s, {errors:,} errors")
    return {"done": done, "errors": errors, "method": method}


def write_manifest(con, library: str) -> str:
    """CSV of every source file and what happened to it, kept in <library>/_meta/."""
    meta = os.path.join(library, "_meta")
    os.makedirs(meta, exist_ok=True)
    path = os.path.join(meta, "manifest.csv")
    cols = ["path", "dest", "action", "status", "method", "category", "subtype", "confidence",
            "reason", "rule", "tags", "size", "mtime", "full_hash", "dup_of_path", "junk", "error"]
    with open(path + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in con.execute(
                "SELECT f.*, d.path AS dup_of_path FROM files f LEFT JOIN files d ON d.id=f.dup_of ORDER BY f.path"):
            w.writerow([r[c] for c in cols])
    os.replace(path + ".tmp", path)
    return path
