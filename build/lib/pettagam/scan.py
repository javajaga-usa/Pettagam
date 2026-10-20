"""Phase 1: read-only inventory of the source tree."""
from __future__ import annotations

import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import classify as cl
from .db import set_meta
from .sniff import sniff_file


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


def _examine(root: str, rel: str, st) -> tuple:
    name = os.path.basename(rel)
    junk = cl.is_junk(name, st.st_size)
    s = None if junk else sniff_file(os.path.join(root, rel))
    name_ext = cl.normalize_ext(name)
    ext = cl.effective_ext(name_ext, s.kind if s else None)
    return (rel, name, st.st_size, st.st_mtime, st.st_dev, st.st_ino, ext,
            s.kind if s else None, s.width if s else None, s.height if s else None,
            s.camera if s else None, int(bool(s and s.has_exif)), int(bool(s and s.has_tags)), junk)


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
    con.execute("DELETE FROM files")
    t0, n, last = time.time(), 0, 0
    sql = ("INSERT INTO files(path,name,size,mtime,dev,ino,ext,kind,width,height,camera,has_exif,"
           "has_tags,junk) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    # One task per directory keeps overhead low while still reading several folders at once,
    # which is what helps on hard drives and network shares.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(_scan_dir, source, source)}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                rows, subdirs = fut.result()
                pending |= {pool.submit(_scan_dir, source, d) for d in subdirs}
                if rows:
                    con.executemany(sql, rows)
                    n += len(rows)
            if n - last >= 20000:
                con.commit()
                last = n
                progress(f"  scanned {n:,} files ({n / (time.time() - t0):,.0f}/s)")
    con.commit()
    set_meta(con, "scanned_at", time.time())
    progress(f"Scanned {n:,} files in {time.time() - t0:,.1f}s")
    return n
