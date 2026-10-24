"""Phase 1: read-only inventory of the source tree."""

import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import classify as cl
from .db import get_meta, set_meta
from .sniff import sniff_file

PROGRESS_EVERY = 10  # seconds between progress lines in long steps


def walk(root: str):
    """Yield (relpath, stat) for every regular file, skipping system folders. Never follows symlinks."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() not in cl.JUNK_DIRS:
                        stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    yield os.path.relpath(e.path, root), e.stat(follow_symlinks=False)
            except OSError:
                continue


def _sqlite_int(n: int) -> int:
    """A device or inode number as SQLite can store it. SQLite integers are signed 64-bit, but some drivers
    hand out numbers past 2**63 (macOS's FSKit exFAT driver gives some files inodes just under 2**64), so
    those wrap around to negative. Every number still maps to exactly one stored value, so files that share
    an inode still match each other."""
    return n - (1 << 64) if n >= 1 << 63 else n


def _examine(root: str, rel: str, st) -> tuple:
    name = os.path.basename(rel)
    junk = cl.is_junk(name, st.st_size)
    s = None if junk else sniff_file(os.path.join(root, rel))
    name_ext = cl.normalize_ext(name)
    ext = cl.effective_ext(name_ext, s.kind if s else None)
    return (rel, name, st.st_size, st.st_mtime, _sqlite_int(st.st_dev), _sqlite_int(st.st_ino), ext,
            s.kind if s else None, s.width if s else None, s.height if s else None,
            s.camera if s else None, int(bool(s and s.has_exif)), int(bool(s and s.has_tags)), junk,
            s.taken if s else None)


def _scan_dir(root: str, d: str):
    """Examine one directory: returns (rows for its files, its subdirectories)."""
    rows, subdirs = [], []
    try:
        entries = list(os.scandir(d))
    except OSError:
        return rows, subdirs
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                if e.name.lower() not in cl.JUNK_DIRS:
                    subdirs.append(e.path)
            elif e.is_file(follow_symlinks=False):
                rows.append(_examine(root, os.path.relpath(e.path, root), e.stat(follow_symlinks=False)))
        except OSError:
            continue
    return rows, subdirs


def scan(con, source: str, workers: int = 2, progress=print) -> int:
    source = os.path.abspath(source)
    if not os.path.isdir(source):
        raise SystemExit(f"Source folder not found: {source}")
    set_meta(con, "source", source)
    try:
        return _scan(con, source, workers, progress)
    except BaseException:
        # Drop the unfinished batch and let go of the write lock, so a failed scan doesn't leave the catalog
        # locked for the panel and the command line ("database is locked"). Run the scan again to finish it.
        con.rollback()
        raise


def _scan(con, source: str, workers: int, progress) -> int:
    # Keep AI tags across a fresh scan: they are matched back by path, size and date.
    kept = {(r["path"], r["size"], r["mtime"]): (r["tags"], r["description"]) for r in con.execute(
        "SELECT path, size, mtime, tags, description FROM files WHERE tags IS NOT NULL")}
    # The last scan of this folder says roughly how many files to expect, for the percentage and time left.
    expect = int(get_meta(con, "scan_count") or 0) if get_meta(con, "scan_source") == source else 0
    con.execute("DELETE FROM files")
    t0, n, last = time.time(), 0, 0
    said = t0
    sql = ("INSERT INTO files(path,name,size,mtime,dev,ino,ext,kind,width,height,camera,has_exif,"
           "has_tags,junk,taken) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    # One task per directory keeps overhead low while still reading several folders at once,
    # which is what helps on hard drives and network shares.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(_scan_dir, source, source)}
        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    rows, subdirs = fut.result()
                    pending |= {pool.submit(_scan_dir, source, d) for d in subdirs}
                    if rows:
                        con.executemany(sql, rows)
                        n += len(rows)
                # Say how far it got every 10 seconds, so a slow drive doesn't look stuck; save every 20,000 files.
                if n - last >= 20000 or time.time() - said >= PROGRESS_EVERY:
                    con.commit()
                    last, said = n, time.time()
                    of = f" of about {expect:,}" if expect else ""
                    progress(f"  scanned {n:,}{of} files ({n / (time.time() - t0):,.0f}/s), "
                             f"{len(pending):,} folders still to read")
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)  # stopping: don't read the folders still queued
            raise
    con.commit()
    if kept:
        con.executemany("UPDATE files SET tags=?, description=? WHERE path=? AND size=? AND mtime=?",
                        [(t, d, p, sz, m) for (p, sz, m), (t, d) in kept.items()])
        con.commit()
    set_meta(con, "scanned_at", time.time())
    set_meta(con, "scan_source", source)
    set_meta(con, "scan_count", n)
    progress(f"Scanned {n:,} files in {time.time() - t0:,.1f}s")
    return n
