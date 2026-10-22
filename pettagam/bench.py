"""Measure how fast Pettagam is on this computer and drive, against the targets in docs/REQUIREMENTS.md.

    pettagam bench "/Volumes/N300/Old files" --library /Volumes/N300/Pettagam

Four timings, each against its target:
  scan     examine files (stat + the first 64 KB) in the old files, read only        PERF-1, 1,000 files/s
  read     read whole files in sequence, as duplicate checks do                     (no target; the drive's speed)
  place    make hardlinks in the library's _meta folder, then remove them           PERF-2, 2,000 files/s
  search   search a made-up catalog of a million files in memory                    PERF-4, under 1 s

The old files are only read. Placement uses files it makes itself in <library>/_meta/bench-*, and
removes that folder after. Nothing else is written.
"""

import os
import shutil
import sqlite3
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .hashing import CHUNK
from .scan import _scan_dir

TARGETS = {"scan": 1000, "place": 2000, "search": 1.0}


def bench_scan(source: str, files: int = 5000, workers: int = 2) -> dict:
    """Examine up to `files` files the way the scan does, folder by folder in parallel."""
    source = os.path.abspath(source)
    if not os.path.isdir(source):
        raise SystemExit(f"Folder not found: {source}")
    rows = []
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(_scan_dir, source, source)}
        while pending and len(rows) < files:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                found, subdirs = fut.result()
                rows += found
                if len(rows) < files:
                    pending |= {pool.submit(_scan_dir, source, d) for d in subdirs}
        for fut in pending:
            fut.cancel()
    seconds = time.perf_counter() - t0
    n = len(rows)
    return {"files": n, "seconds": seconds, "rate": n / seconds if seconds and n else 0.0,
            "sample": [(r[0], r[2]) for r in rows]}


def bench_read(source: str, sample: list, megabytes: int = 512) -> dict:
    """Read the biggest files of the sample in full, up to `megabytes`, and report MB/s."""
    done = 0
    t0 = time.perf_counter()
    for rel, _ in sorted(sample, key=lambda r: -r[1]):
        if done >= megabytes * 1024 * 1024:
            break
        try:
            with open(os.path.join(source, rel), "rb") as f:
                for block in iter(lambda f=f: f.read(CHUNK), b""):
                    done += len(block)
        except OSError:
            continue
    seconds = time.perf_counter() - t0
    return {"bytes": done, "seconds": seconds, "rate": done / 1e6 / seconds if seconds and done else 0.0}


def bench_place(library: str, files: int = 2000) -> dict:
    """Time hardlinking `files` small files within <library>/_meta, then remove everything it made."""
    meta = os.path.join(os.path.abspath(library), "_meta")
    if not os.path.isdir(os.path.dirname(meta)):
        raise SystemExit(f"Library folder not found: {library}")
    os.makedirs(meta, exist_ok=True)
    work = tempfile.mkdtemp(prefix="bench-", dir=meta)
    try:
        src, dst = os.path.join(work, "src"), os.path.join(work, "dst")
        os.mkdir(src)
        os.mkdir(dst)
        for i in range(files):
            with open(os.path.join(src, f"{i}.bin"), "wb") as f:
                f.write(b"x")
        t0 = time.perf_counter()
        try:
            for i in range(files):
                os.link(os.path.join(src, f"{i}.bin"), os.path.join(dst, f"{i}.bin"))
        except OSError as e:
            return {"files": 0, "seconds": 0.0, "rate": 0.0, "error": f"no hardlinks on this drive ({e.strerror})"}
        seconds = time.perf_counter() - t0
        return {"files": files, "seconds": seconds, "rate": files / seconds if seconds else 0.0}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def bench_search(rows: int = 1_000_000, searches=("", "beach 2019", "IMG_12345")) -> dict:
    """Build a made-up catalog of `rows` placed files in memory and time Explore over it: the opening view
    (newest first), a common word and a rare one. The slowest counts."""
    from . import library
    from .db import SCHEMA, ensure_search
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cats = ["images", "documents", "audio", "video", "archives"]
    tags = ["beach,sunset", "receipt", "birthday,family", "passport", "", "beach,family"]
    con.executemany(
        "INSERT INTO files(path, name, size, mtime, dest, category, tags, camera, action, status) "
        "VALUES (?,?,?,?,?,?,?,?, 'place', 'done')",
        ((f"old/{i}.jpg", f"IMG_{i}.jpg", 1000 + i, 1.2e9 + i * 300, f"{cats[i % 5]}/{2000 + i % 25}/IMG_{i}.jpg",
          cats[i % 5], tags[i % 6] or None, "Apple iPhone" if i % 3 else None) for i in range(rows)))
    con.commit()
    ensure_search(con)  # as a real catalog has it (built once, not timed)
    each = {}
    for words in searches:
        t0 = time.perf_counter()
        library.search(con, words)
        each[words or "(newest first)"] = time.perf_counter() - t0
    con.close()
    return {"rows": rows, "seconds": max(each.values()), "each": each}


def run(source: str = None, library: str = None, files: int = 5000, workers: int = 2,
        search_rows: int = 1_000_000, progress=print) -> dict:
    """Run the timings that apply (scan and read need the old files, place needs the library)."""
    out = {}
    if source:
        progress(f"Scanning up to {files:,} files in {source} ...")
        out["scan"] = bench_scan(source, files, workers)
        progress("Reading the largest of them in full ...")
        out["read"] = bench_read(os.path.abspath(source), out["scan"].pop("sample"))
    if library:
        progress(f"Placing {min(files, 2000):,} test hardlinks in {library}/_meta ...")
        out["place"] = bench_place(library, min(files, 2000))
    if search_rows:
        progress(f"Searching a made-up catalog of {search_rows:,} files ...")
        out["search"] = bench_search(search_rows)
    return out


def verdicts(res: dict) -> list:
    """One line per timing: (name, measured, target, ok or None when there is no target)."""
    lines = []
    if "scan" in res:
        r = res["scan"]
        lines.append(("Scan", f"{r['rate']:,.0f} files/s ({r['files']:,} files)", f"{TARGETS['scan']:,} files/s",
                      r["rate"] >= TARGETS["scan"] if r["files"] else None))
    if "read" in res:
        r = res["read"]
        lines.append(("Read", f"{r['rate']:,.0f} MB/s ({r['bytes'] / 1e6:,.0f} MB)", "-", None))
    if "place" in res:
        r = res["place"]
        lines.append(("Place", r.get("error") or f"{r['rate']:,.0f} files/s", f"{TARGETS['place']:,} files/s",
                      r["rate"] >= TARGETS["place"] if not r.get("error") else False))
    if "search" in res:
        r = res["search"]
        lines.append(("Search", f"{r['seconds']:.2f} s at worst over {r['rows']:,} files", f"under {TARGETS['search']:g} s",
                      r["seconds"] < TARGETS["search"]))
    return lines
