"""Looking after the library over time: undo a placement, keep a copy of the catalog with the library,
and follow the files when the drive is mounted somewhere else (for example after moving to Ubuntu).
"""
from __future__ import annotations

import os
import sqlite3

from .db import connect, get_meta, set_meta
from .safeio import BUSY, library_lock, move_file

CATALOG_COPY = os.path.join("_meta", "catalog.db")


# --- undo ------------------------------------------------------------------

def _safe_to_remove(src: str, dst: str, method: str, size: int, mtime: float):
    """(ok, why not). A library file may go only if the old file still holds the same content."""
    try:
        d = os.lstat(dst)
    except OSError:
        return False, "already gone"
    try:
        s = os.stat(src)
    except OSError:
        return False, "the old file is gone, so this is now the only copy"
    if (s.st_dev, s.st_ino) == (d.st_dev, d.st_ino):
        return True, ""
    if method == "copy" and d.st_size == s.st_size == size and int(d.st_mtime) == int(s.st_mtime):
        return True, ""
    return False, "it no longer matches the old file (changed after placing?)"


def undo(con, dry_run: bool = False, progress=print) -> dict:
    """Take placed files back out of the library, leaving the library as it was before `apply`.

    Only removes a library file when the old file is still there with the same content, so nothing is
    ever lost. Files sorted from the inbox are moved back into the inbox. Views are removed. Your
    rules, the manifest and anything Pettagam didn't put there are left alone.
    """
    source, library = get_meta(con, "source"), get_meta(con, "library")
    if not library:
        raise SystemExit("Nothing to undo: no library in this catalog.")
    lock = None if dry_run else library_lock(library)
    if lock is False:
        raise SystemExit(BUSY)
    try:
        return _undo(con, source, library, dry_run, progress)
    finally:
        if lock:
            lock.close()


def _undo(con, source, library, dry_run, progress) -> dict:
    counts = {"removed": 0, "kept": 0, "returned": 0}
    kept = []
    for r in con.execute("SELECT id, path, dest, method, size, mtime FROM files "
                         "WHERE action='place' AND status='done'").fetchall():
        dst = os.path.join(library, r["dest"])
        ok, why = _safe_to_remove(os.path.join(source, r["path"]), dst, r["method"], r["size"], r["mtime"])
        if ok:
            if not dry_run:
                os.remove(dst)
                con.execute("UPDATE files SET status=NULL, method=NULL, error=NULL WHERE id=?", (r["id"],))
            counts["removed"] += 1
        elif why != "already gone":
            counts["kept"] += 1
            kept.append(f"{r['dest']}: kept, {why}")
        elif not dry_run:
            con.execute("UPDATE files SET status=NULL, method=NULL WHERE id=?", (r["id"],))

    for r in con.execute("SELECT id, original, dest FROM inbox WHERE action='placed'").fetchall():
        dst = os.path.join(library, r["dest"])
        if not os.path.exists(dst):
            continue
        if os.path.lexists(r["original"]):
            counts["kept"] += 1
            kept.append(f"{r['dest']}: kept, something new is at {r['original']}")
            continue
        if not dry_run:
            try:
                move_file(dst, r["original"])  # never replaces; across drives it is checked before removal
            except OSError as e:
                counts["kept"] += 1
                kept.append(f"{r['dest']}: kept, could not return it to the inbox: {e.strerror or e}")
                continue
            con.execute("UPDATE inbox SET action='returned' WHERE id=?", (r["id"],))
        counts["returned"] += 1

    if not dry_run:
        con.commit()
        from .views import _remove_old
        _remove_old(library)
        for d, _, _ in os.walk(library, topdown=False):
            if d != library and os.path.basename(d) != "_meta" and not os.listdir(d):
                try:
                    os.rmdir(d)
                except OSError:
                    pass
    for line in kept[:20]:
        progress(f"  {line}")
    verb = "Would remove" if dry_run else "Removed"
    progress(f"{verb} {counts['removed']:,} files from the library, returned {counts['returned']:,} to the inbox, "
             f"kept {counts['kept']:,} that are not safe to remove. The old files were not touched.")
    return counts


# --- catalog copy ------------------------------------------------------------

def backup_catalog(con, library: str = None) -> str:
    """Keep an up-to-date copy of the catalog in <library>/_meta/, so it travels with the drive."""
    library = library or get_meta(con, "library")
    if not library or not os.path.isdir(library):
        return ""
    path = os.path.join(library, CATALOG_COPY)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    dest = sqlite3.connect(tmp)
    try:
        con.commit()
        con.backup(dest)
    finally:
        dest.close()
    os.replace(tmp, path)
    return path


# --- relocate ----------------------------------------------------------------

def relocate(db_path: str, source: str = None, library: str = None, catalog_from: str = None,
             progress=print) -> dict:
    """Point the catalog at new locations, e.g. /Volumes/N300/... on the Mac -> /srv/ninaivu/... on Ubuntu.

    With catalog_from (usually <new library>/_meta/catalog.db), start from the copy that travelled with
    the drive. Paths inside the catalog are relative, so only the two roots change.
    """
    if catalog_from:
        if os.path.exists(db_path):
            raise SystemExit(f"{db_path} already exists. Choose a new --db, or relocate without --catalog-from.")
        src = sqlite3.connect(catalog_from)
        dst = sqlite3.connect(db_path)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        progress(f"Catalog restored from {catalog_from}")
    con = connect(db_path)
    old = {"source": get_meta(con, "source"), "library": get_meta(con, "library")}
    if source:
        set_meta(con, "source", os.path.abspath(source))
    if library:
        set_meta(con, "library", os.path.abspath(library))
    new = {"source": get_meta(con, "source"), "library": get_meta(con, "library")}
    for k in ("source", "library"):
        if old[k] != new[k]:
            progress(f"{k}: {old[k]} -> {new[k]}")
    # Spot-check that files are where the catalog now expects them.
    found = missing = 0
    for r in con.execute("SELECT path, dest, status FROM files WHERE action='place' ORDER BY random() LIMIT 50"):
        for root, rel in ((new["source"], r["path"]), (new["library"] if r["status"] == "done" else None, r["dest"])):
            if root:
                if os.path.exists(os.path.join(root, rel)):
                    found += 1
                else:
                    missing += 1
    progress(f"Spot check: {found} files found, {missing} missing"
             + (". If many are missing, check the paths." if missing else ". Looks right."))
    return {"old": old, "new": new, "found": found, "missing": missing}
