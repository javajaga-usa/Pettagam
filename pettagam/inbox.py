"""Inbox sorting: files dropped into an inbox folder are filed into the library, by your rules first
and then by the smart types, so the library stays tidy after the one-time cleanup.

Unlike the old-files folder, the inbox is emptied: each file is moved into the library (a rename on
the same drive, otherwise copy, flush to disk, check in full, then remove). Exact copies of files the library already has
go to <inbox>/_duplicates/ instead, so nothing is ever deleted.
"""
from __future__ import annotations

import csv
import os
import time

from . import classify as cl
from .db import get_meta
from .hashing import hash_file
from .plan import _bucket_key, decide, safe_name
from .safeio import library_lock, move_file, move_no_clobber
from .scan import _examine, walk

DUPES = "_duplicates"
PARTIAL = {"crdownload", "part", "partial", "download", "tmp", "opdownload"}
SETTLE = 10  # seconds a file must be unchanged, so half-copied downloads are left alone


def _taken_names(con, library: str, folder: str) -> set:
    """Names already used in a library folder: on disk, planned by the catalog, or filed earlier."""
    names = set()
    try:
        names |= {n.lower() for n in os.listdir(os.path.join(library, folder))}
    except OSError:
        pass
    like = folder.replace("%", r"\%").replace("_", r"\_") + "/%"
    for table in ("files", "inbox"):
        for (d,) in con.execute(f"SELECT dest FROM {table} WHERE dest LIKE ? ESCAPE '\\'", (like,)):
            rest = d[len(folder) + 1:]
            if "/" not in rest:
                names.add(rest.lower())
    return names


def _free_name(name: str, taken: set) -> str:
    candidate, k = name, 1
    while candidate.lower() in taken:
        k += 1
        dot = name.rfind(".")
        candidate = f"{name[:dot]}__{k}{name[dot:]}" if dot > 0 else f"{name}__{k}"
    return candidate


def _subfolder(library: str, folder: str, name: str) -> str:
    """Follow the letter folders a big folder was split into (photos/i/img/...), if any."""
    sub, depth = folder, 0
    while depth < 4:
        key = _bucket_key(name, depth)
        if not os.path.isdir(os.path.join(library, sub, key)):
            break
        sub, depth = f"{sub}/{key}", depth + 1
    return sub


def _existing_copy(con, library: str, path: str, size: int):
    """Library path of an identical file already filed, else None. Only same-size files are read."""
    h = None
    candidates = con.execute(
        "SELECT dest, full_hash FROM files WHERE action='place' AND status='done' AND size=? "
        "UNION ALL SELECT dest, full_hash FROM inbox WHERE action='placed' AND size=?", (size, size)).fetchall()
    for dest, known in candidates:
        h = h or hash_file(path)
        try:
            other = known if known and len(known) == len(h) else hash_file(os.path.join(library, dest))
        except OSError:
            continue
        if other == h:
            return dest, h
    return None, h


def _log(library: str, rows: list) -> None:
    meta = os.path.join(library, "_meta")
    os.makedirs(meta, exist_ok=True)
    path = os.path.join(meta, "inbox-log.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["when", "inbox file", "action", "library path", "rule", "reason"])
        w.writerows(rows)


def sort_inbox(con, inbox: str, library: str = None, rules=(), settle: float = SETTLE,
               dry_run: bool = False, progress=print) -> dict:
    inbox = os.path.abspath(inbox)
    library = os.path.abspath(library or get_meta(con, "library") or "")
    if not os.path.isdir(inbox):
        raise SystemExit(f"Inbox folder not found: {inbox}")
    if not library or library == os.path.abspath(""):
        raise SystemExit("Say where the library is with --library (or run a dry run first).")
    a, b = inbox + os.sep, library + os.sep
    if a.startswith(b) or b.startswith(a):
        raise SystemExit("The inbox must be outside the library (and vice versa).")
    source = get_meta(con, "source")
    if source:
        c = os.path.abspath(source) + os.sep
        if a.startswith(c) or c.startswith(a):
            raise SystemExit("The inbox and the old-files folder must be separate: the old files are never moved.")

    lock = None if dry_run else library_lock(library)
    if lock is False:
        progress("Another Pettagam step is writing to this library; skipping this inbox pass.")
        return {"placed": 0, "duplicate": 0, "waiting": 0, "error": 0, "ignored": 0, "busy": 1}
    try:
        return _sort(con, inbox, library, rules, settle, dry_run, progress)
    finally:
        if lock:
            lock.close()


def _sort(con, inbox, library, rules, settle, dry_run, progress) -> dict:
    now = time.time()
    counts = {"placed": 0, "duplicate": 0, "waiting": 0, "error": 0, "ignored": 0}
    log = []
    for rel, st in sorted(walk(inbox)):
        if rel.split(os.sep)[0] == DUPES:
            continue
        name = os.path.basename(rel)
        if cl.is_junk(name, st.st_size) or cl.normalize_ext(name) in PARTIAL:
            counts["ignored"] += 1
            continue
        if now - st.st_mtime < settle:
            counts["waiting"] += 1
            continue
        e = _examine(inbox, rel, st)
        row = dict(zip(("path", "name", "size", "mtime", "dev", "ino", "ext", "kind", "width", "height",
                        "camera", "has_exif", "has_tags", "junk", "taken"), e))
        src = os.path.join(inbox, rel)
        r, folder, rule = decide(row, rules)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            dup, h = _existing_copy(con, library, src, row["size"])
            if dup:
                counts["duplicate"] += 1
                progress(f"  {rel}: already in the library as {dup}")
                if not dry_run:
                    target = os.path.join(inbox, DUPES, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    move_no_clobber(src, _free_target(target))
                    _record(con, src, dup, "duplicate", row, r, rule, h, None)
                    log.append([stamp, rel, "duplicate", dup, rule or "", "already in the library"])
                continue
            folder = _subfolder(library, folder, name)
            fname = _free_name(safe_name(name, row["ext"]), _taken_names(con, library, folder))
            dest = f"{folder}/{fname}"
            progress(f"  {rel} -> {dest}" + (f"  (your rule '{rule}')" if rule else f"  ({r.reason})"))
            if not dry_run:
                move_file(src, os.path.join(library, dest))
                _record(con, src, dest, "placed", row, r, rule, h, None)
                log.append([stamp, rel, "placed", dest, rule or "", r.reason])
            counts["placed"] += 1
        except OSError as err:
            counts["error"] += 1
            progress(f"  {rel}: could not file it: {err}")
            if not dry_run:
                _record(con, src, None, "error", row, r, rule, None, str(err))
                log.append([stamp, rel, "error", "", rule or "", str(err)])
    if not dry_run:
        con.commit()
        if log:
            _log(library, log)
        _remove_empty_dirs(inbox)
    verb = "Would file" if dry_run else "Filed"
    progress(f"{verb} {counts['placed']:,} files, {counts['duplicate']:,} already in the library"
             + (f", {counts['waiting']:,} still being written (next pass)" if counts["waiting"] else "")
             + (f", {counts['error']:,} errors" if counts["error"] else ""))
    return counts


def _free_target(path: str) -> str:
    base, k = path, 1
    while os.path.lexists(path):
        k += 1
        root, ext = os.path.splitext(base)
        path = f"{root}__{k}{ext}"
    return path


def _record(con, original, dest, action, row, r, rule, h, err):
    con.execute(
        "INSERT INTO inbox(at, original, dest, action, size, full_hash, category, subtype, confidence, reason, "
        "rule, taken, mtime, camera, width, height, error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), original, dest, action, row["size"], h, r.category, r.subtype, r.confidence, r.reason,
         rule, row["taken"], row["mtime"], row["camera"], row["width"], row["height"], err))


def _remove_empty_dirs(root: str) -> None:
    for d, subdirs, files in os.walk(root, topdown=False):
        if d != root and not os.listdir(d):
            try:
                os.rmdir(d)
            except OSError:
                pass


def watch(con, inbox: str, library: str = None, rules_path: str = None, every: float = 60,
          settle: float = SETTLE, progress=print, load=None) -> None:
    """Sort the inbox every few seconds until stopped. Rules are re-read each pass, so edits apply."""
    progress(f"Watching {inbox} every {every:g}s. Press Ctrl+C to stop.")
    rules = ()
    try:
        while True:
            if load:
                try:
                    rules = load(rules_path)
                except ValueError as e:  # a half-edited rules file: keep the last good rules
                    progress(f"Rules not reloaded: {e}")
            res = sort_inbox(con, inbox, library, rules, settle=settle, progress=lambda m: None)
            if res["placed"] or res["duplicate"] or res["error"]:
                progress(time.strftime("%H:%M:%S ") + f"filed {res['placed']}, duplicates {res['duplicate']}, "
                         f"errors {res['error']}")
            time.sleep(every)
    except KeyboardInterrupt:
        progress("Stopped watching.")
