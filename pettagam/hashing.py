"""Exact-duplicate detection. Only files sharing a size are read, first a 64 KB sample, then in full."""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

from .db import get_meta

try:
    import xxhash

    def _hasher():
        return xxhash.xxh3_128()
    ALGO = "xxh3-128"
except ImportError:  # stdlib fallback, slower
    def _hasher():
        return hashlib.blake2b(digest_size=16)
    ALGO = "blake2b-128"

CHUNK = 1024 * 1024
SAMPLE = 64 * 1024


def hash_file(path: str, limit: int = 0) -> str:
    h = _hasher()
    with open(path, "rb") as f:
        if limit:
            h.update(f.read(limit))
        else:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
    return h.hexdigest()


def _run(pool, root, rows, limit):
    def one(row):
        try:
            return row["id"], hash_file(os.path.join(root, row["path"]), limit)
        except OSError:
            return row["id"], None
    return list(pool.map(one, rows))


def find_duplicates(con, workers: int = 2, progress=print) -> dict:
    root = get_meta(con, "source")
    con.execute("UPDATE files SET quick_hash=NULL, full_hash=NULL, dup_of=NULL")
    base = "junk IS NULL"
    same_size = con.execute(
        f"SELECT id, path, size FROM files WHERE {base} AND size IN "
        f"(SELECT size FROM files WHERE {base} GROUP BY size HAVING COUNT(*) > 1)").fetchall()
    progress(f"  {len(same_size):,} files share a size with another file; sampling them")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fid, h in _run(pool, root, same_size, SAMPLE):
            con.execute("UPDATE files SET quick_hash=? WHERE id=?", (h, fid))
        con.commit()
        # A file no bigger than the sample was read in full already.
        con.execute(f"UPDATE files SET full_hash=quick_hash WHERE size <= {SAMPLE} AND quick_hash IS NOT NULL")
        candidates = con.execute(
            f"SELECT id, path FROM files WHERE size > {SAMPLE} AND {base} AND quick_hash IS NOT NULL AND (size, quick_hash) IN "
            f"(SELECT size, quick_hash FROM files WHERE quick_hash IS NOT NULL GROUP BY size, quick_hash "
            f"HAVING COUNT(*) > 1)").fetchall()
        progress(f"  {len(candidates):,} candidates after sampling; hashing them in full ({ALGO})")
        for fid, h in _run(pool, root, candidates, 0):
            con.execute("UPDATE files SET full_hash=? WHERE id=?", (h, fid))
        con.commit()

    groups = con.execute(
        "SELECT full_hash FROM files WHERE full_hash IS NOT NULL GROUP BY full_hash, size HAVING COUNT(*) > 1").fetchall()
    dup_files = saved = 0
    for (h,) in groups:
        # Canonical copy: the oldest file, then the shortest path.
        members = con.execute(
            "SELECT id, size FROM files WHERE full_hash=? ORDER BY mtime, length(path), path", (h,)).fetchall()
        keep = members[0]["id"]
        for m in members[1:]:
            con.execute("UPDATE files SET dup_of=? WHERE id=?", (keep, m["id"]))
            dup_files += 1
            saved += m["size"]
    con.commit()
    progress(f"Found {len(groups):,} duplicate groups: {dup_files:,} extra copies, {saved / 1e9:,.2f} GB")
    return {"groups": len(groups), "extra_copies": dup_files, "bytes": saved}
