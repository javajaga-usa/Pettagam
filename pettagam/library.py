"""Questions about the finished library: search it, summarize it, and list the duplicate copies in the old files.

The panel's Explore tab and the `find`, `stats` and `duplicates` commands share these.
"""

import csv
import heapq
import itertools
import math
import os
import re
import time

from .db import get_meta, has_search
from .safeio import durable_copy

# Every file in the library: placed from the old files, or sorted from the inbox. The WHERE clauses match
# the partial indexes files_recent and inbox_recent (db.py), which list the newest first without sorting.
PLACED_SQL = """
SELECT 'files' AS src, id, dest, name, size, mtime, taken, camera, width, height, category, subtype, rule,
       tags, description, path AS original FROM files WHERE action='place' AND status='done'"""
INBOX_SQL = """
SELECT 'inbox' AS src, id, dest, replace(dest, rtrim(dest, replace(dest, '/', '')), '') AS name, size, mtime,
       taken, camera, width, height, category, subtype, rule, tags, description, original
       FROM inbox WHERE action='placed'"""
ITEMS_SQL = PLACED_SQL + "\nUNION ALL" + INBOX_SQL
NEWEST = "ORDER BY COALESCE(taken, mtime) DESC, dest"
YEAR = "strftime('%Y', COALESCE(taken, mtime), 'unixepoch')"
DAY = "strftime('%m-%d', COALESCE(taken, mtime), 'unixepoch')"
# Which rows of each table are library files.
IN_LIBRARY = {"files": "action='place' AND status='done'", "inbox": "action='placed'"}
MAX_TAGS = 20  # by hand; AI tagging gives at most 8


def search(con, q: str = "", category: str = "", tag: str = "", year: str = "",
           offset: int = 0, limit: int = 120, day: str = "") -> dict:
    """Search by words in the name, folder, tags, description or camera, plus optional filters.
    DAY ("MM-DD") keeps the files from that day of the year, in any year: "on this day"."""
    where, args = ["1=1"], []
    indexed = has_search(con)
    fts = []
    for word in q.split():
        if indexed and len(word) >= 3:  # the trigram index needs 3 letters
            fts.append('"' + word.replace('"', '""') + '"')
            continue
        where.append("(dest LIKE ? OR tags LIKE ? OR description LIKE ? OR camera LIKE ?)")
        args += [f"%{word}%"] * 4
    if category:
        where.append("(category=? OR dest LIKE ?)")
        args += [category, f"{category}/%"]
    if tag:
        where.append("(','||tags||',') LIKE ?")
        args.append(f"%,{tag},%")
    if year:
        where.append(f"{YEAR} = ?")
        args.append(str(year))
    if day:
        if not re.fullmatch(r"(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", day):
            raise ValueError("day must look like 10-22 (month-day)")
        where.append(f"{DAY} = ?")
        args.append(day)
    # Each table on its own, so its index gives the newest files without sorting the whole library;
    # then the two short lists are merged.
    total, parts = 0, []
    for part, table, odd in ((PLACED_SQL, "files", 0), (INBOX_SQL, "inbox", 1)):
        cond, a = list(where), list(args)
        if fts:
            cond.append("id IN (SELECT rowid >> 1 FROM search WHERE search MATCH ? AND rowid & 1 = ?)")
            a += [" AND ".join(fts), odd]
        sql = f"{part} AND {' AND '.join(cond)}"
        n = con.execute(f"SELECT COUNT(*) FROM ({sql})", a).fetchone()[0]
        total += n
        if not n:
            continue
        if n < 3 * (offset + limit):  # few matches: reading the table and sorting them beats walking the index
            sql = sql.replace(f"FROM {table} WHERE", f"FROM {table} NOT INDEXED WHERE")
        parts.append([dict(r) for r in con.execute(f"{sql} {NEWEST} LIMIT ?", (*a, offset + limit))])
    def newest_first(r):  # as NEWEST orders them: a file without a date comes last
        when = r["taken"] if r["taken"] is not None else r["mtime"]
        return (-when if when is not None else math.inf, r["dest"])
    newest = heapq.merge(*parts, key=newest_first)
    return {"items": list(itertools.islice(newest, offset, offset + limit)), "total": total}


def find_item(con, path: str) -> tuple | None:
    """The library file at PATH (a full path, or one inside the library) as (table, id), or None."""
    library = get_meta(con, "library") or ""
    rel = path
    if os.path.isabs(path) and library:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(library))
    rel = rel.replace(os.sep, "/")
    for table, cond in IN_LIBRARY.items():
        r = con.execute(f"SELECT id FROM {table} WHERE dest=? AND {cond}", (rel,)).fetchone()
        if r:
            return table, r[0]
    return None


def edit_tags(con, items, tags=None, add=(), remove=(), description: str | None = None) -> int:
    """Change tags by hand on library files, given as (table, id): replace them all (TAGS), or ADD and REMOVE
    some; DESCRIPTION replaces the description. Returns how many files changed. Search sees it at once."""
    from .tag import clean_tags
    drop = set(clean_tags(list(remove), MAX_TAGS))
    changed = 0
    for table, fid in items:
        if table not in IN_LIBRARY:
            raise ValueError(f"unknown table {table!r}")
        row = con.execute(f"SELECT tags, description FROM {table} WHERE id=? AND {IN_LIBRARY[table]}",
                          (fid,)).fetchone()
        if not row:
            continue
        now = list(tags) if tags is not None else (row[0] or "").split(",")
        new = clean_tags([t for t in clean_tags(now + list(add), 10 * MAX_TAGS) if t not in drop], MAX_TAGS)
        desc = row[1] if description is None else " ".join(str(description).split())[:300]
        after = (",".join(new) or None, desc or None)
        if after != (row[0] or None, row[1] or None):
            con.execute(f"UPDATE {table} SET tags=?, description=? WHERE id=?", (*after, fid))
            changed += 1
    con.commit()
    return changed


def copies_of(con, table: str, fid: int, limit: int = 50) -> dict:
    """The identical copies of a placed file that stayed in the old files (only one went into the library),
    as full paths, at most LIMIT of them, and how many there are."""
    if table != "files":
        return {"copies": [], "total": 0}
    source = get_meta(con, "source") or ""
    total = con.execute("SELECT COUNT(*) FROM files WHERE dup_of=?", (fid,)).fetchone()[0]
    rows = con.execute("SELECT path FROM files WHERE dup_of=? ORDER BY path LIMIT ?", (fid, limit))
    return {"copies": [os.path.join(source, r[0]) for r in rows], "total": total}


def tag_counts(con) -> list:
    counts = {}
    for (tags,) in con.execute(f"SELECT tags FROM ({ITEMS_SQL}) WHERE tags IS NOT NULL AND tags != ''"):
        for t in tags.split(","):
            if t:
                counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def facets(con) -> dict:
    base = f"({ITEMS_SQL})"
    cats = [list(r) for r in con.execute(
        f"SELECT category, COUNT(*) FROM {base} WHERE category IS NOT NULL GROUP BY category ORDER BY 2 DESC")]
    years = [r[0] for r in con.execute(f"SELECT DISTINCT {YEAR} AS y FROM {base} ORDER BY y DESC")]
    return {"categories": cats, "tags": [list(t) for t in tag_counts(con)[:200]], "years": years}


def stats(con) -> dict:
    """What is in the library: totals, by type, by year, top cameras and tags, and space saved."""
    base = f"({ITEMS_SQL})"
    q = lambda sql: con.execute(sql).fetchall()
    files, size = q(f"SELECT COUNT(*), COALESCE(SUM(size), 0) FROM {base}")[0]
    by_type = [{"category": r[0] or "other", "files": r[1], "bytes": r[2]} for r in q(
        f"SELECT category, COUNT(*), SUM(size) FROM {base} GROUP BY category ORDER BY 3 DESC")]
    by_year = [{"year": r[0], "files": r[1], "bytes": r[2]} for r in q(
        f"SELECT {YEAR} AS y, COUNT(*), SUM(size) FROM {base} GROUP BY y ORDER BY y")]
    cameras = [{"camera": r[0], "files": r[1]} for r in q(
        f"SELECT camera, COUNT(*) FROM {base} WHERE camera IS NOT NULL AND camera != '' "
        f"GROUP BY camera ORDER BY 2 DESC LIMIT 10")]
    dupes, dupe_bytes = q("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE dup_of IS NOT NULL")[0]
    linked = q("SELECT COALESCE(SUM(size), 0) FROM files WHERE status='done' AND method='link'")[0][0]
    inbox_dupes = q("SELECT COUNT(*) FROM inbox WHERE action='duplicate'")[0][0]
    tagged = q(f"SELECT COUNT(*) FROM {base} WHERE tags IS NOT NULL AND tags != ''")[0][0]
    return {"library": get_meta(con, "library"), "files": files, "bytes": size, "by_type": by_type,
            "by_year": by_year, "cameras": cameras, "tags": [{"tag": t, "files": n} for t, n in tag_counts(con)[:15]],
            "tagged": tagged, "duplicates": {"files": dupes, "bytes": dupe_bytes, "from_inbox": inbox_dupes},
            "hardlinked_bytes": linked}


def _gb(n: int) -> str:
    return f"{n / 1e9:,.2f} GB" if n >= 1e8 else f"{n / 1e6:,.1f} MB"


def print_stats(st: dict, out=print) -> None:
    out(f"Library: {st['library'] or '(not set)'}")
    out(f"  {st['files']:,} files, {_gb(st['bytes'])}; {st['tagged']:,} have AI tags")
    if st["hardlinked_bytes"]:
        out(f"  {_gb(st['hardlinked_bytes'])} of it is hardlinked, so it takes no extra space on the drive")
    d = st["duplicates"]
    if d["files"] or d["from_inbox"]:
        out(f"  Duplicates kept out of the library: {d['files']:,} extra copies in the old files "
            f"({_gb(d['bytes'])}), {d['from_inbox']:,} from the inbox")
    if st["by_type"]:
        out("\nBy type")
        for r in st["by_type"]:
            out(f"  {r['category']:<14} {r['files']:>9,} files  {_gb(r['bytes']):>12}")
    if st["by_year"]:
        out("\nBy year")
        top = max(r["files"] for r in st["by_year"])
        for r in st["by_year"]:
            out(f"  {r['year'] or '?':<6} {r['files']:>9,}  " + "#" * max(1, round(30 * r["files"] / top)))
    if st["cameras"]:
        out("\nCameras")
        for r in st["cameras"]:
            out(f"  {r['camera']:<30} {r['files']:>7,}")
    if st["tags"]:
        out("\nTop tags: " + ", ".join(f"{t['tag']} ({t['files']})" for t in st["tags"]))


def duplicate_groups(con, limit: int = 0) -> list:
    """Sets of identical files in the old files, biggest waste first. The first path is the one kept."""
    rows = con.execute(
        "SELECT k.id, k.path, k.size, COUNT(f.id) AS extra FROM files f JOIN files k ON k.id=f.dup_of "
        "GROUP BY k.id ORDER BY k.size * COUNT(f.id) DESC, k.path" + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()
    out = []
    for r in rows:
        copies = [c[0] for c in con.execute("SELECT path FROM files WHERE dup_of=? ORDER BY path", (r["id"],))]
        out.append({"keep": r["path"], "copies": copies, "size": r["size"], "wasted": r["size"] * r["extra"]})
    return out


def write_duplicates_csv(con, path: str) -> int:
    source = get_meta(con, "source") or ""
    groups = duplicate_groups(con)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "role", "path", "size", "modified"])
        for i, g in enumerate(groups, 1):
            for role, p in [("keep", g["keep"])] + [("copy", c) for c in g["copies"]]:
                try:
                    m = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.stat(os.path.join(source, p)).st_mtime))
                except OSError:
                    m = ""
                w.writerow([i, role, os.path.join(source, p), g["size"], m])
    return len(groups)


def export(con, dest: str, q: str = "", category: str = "", tag: str = "", year: str = "", flat: bool = False,
           dry_run: bool = False, progress=print) -> dict:
    """Copy the library files that match a search into DEST. The library is only read; nothing is overwritten."""
    library = get_meta(con, "library")
    total = search(con, q, category, tag, year, limit=0)["total"]
    items = search(con, q, category, tag, year, limit=max(total, 1))["items"]
    res = {"copied": 0, "same": 0, "renamed": 0, "errors": 0, "bytes": 0}
    for it in items:
        src = os.path.join(library, it["dest"])
        rel = os.path.basename(it["dest"]) if flat else it["dest"]
        out = os.path.join(dest, rel)
        if os.path.exists(out):
            try:
                a, b = os.stat(src), os.stat(out)
            except OSError:
                a = b = None
            if a and a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime):
                res["same"] += 1
                continue
            stem, ext = os.path.splitext(out)
            n = 2
            while os.path.exists(f"{stem} ({n}){ext}"):
                n += 1
            out = f"{stem} ({n}){ext}"
            res["renamed"] += 1
        if dry_run:
            progress(f"  {it['dest']} -> {out}")
        else:
            try:
                os.makedirs(os.path.dirname(out), exist_ok=True)
                durable_copy(src, out, check="size")
            except OSError as e:
                res["errors"] += 1
                progress(f"  could not copy {it['dest']}: {e.strerror or e}")
                continue
        res["copied"] += 1
        res["bytes"] += it["size"] or 0
    verb = "Would copy" if dry_run else "Copied"
    progress(f"{verb} {res['copied']:,} of {total:,} matching files ({res['bytes'] / 1e6:,.1f} MB) to {dest}"
             + (f"; {res['same']:,} were already there" if res["same"] else "")
             + (f"; {res['errors']:,} errors" if res["errors"] else "") + ". The library was not changed.")
    return res
