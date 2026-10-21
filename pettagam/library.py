"""Questions about the finished library: search it, summarize it, and list the duplicate copies in the old files.

The panel's Explore tab and the `find`, `stats` and `duplicates` commands share these.
"""
from __future__ import annotations

import csv
import os
import time

from .db import get_meta

# Every file in the library: placed from the old files, or sorted from the inbox.
ITEMS_SQL = """
SELECT 'files' AS src, id, dest, name, size, mtime, taken, camera, width, height, category, subtype, rule,
       tags, description, path AS original FROM files WHERE action='place' AND status='done'
UNION ALL
SELECT 'inbox', id, dest, replace(dest, rtrim(dest, replace(dest, '/', '')), ''), size, mtime, taken, camera,
       width, height, category, subtype, rule, tags, description, original FROM inbox WHERE action='placed'
"""
YEAR = "strftime('%Y', COALESCE(taken, mtime), 'unixepoch')"


def search(con, q: str = "", category: str = "", tag: str = "", year: str = "",
           offset: int = 0, limit: int = 120) -> dict:
    """Search by words in the name, folder, tags, description or camera, plus optional filters."""
    where, args = ["1=1"], []
    for word in q.split():
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
    w = " AND ".join(where)
    total = con.execute(f"SELECT COUNT(*) FROM ({ITEMS_SQL}) WHERE {w}", args).fetchone()[0]
    rows = con.execute(f"SELECT * FROM ({ITEMS_SQL}) WHERE {w} ORDER BY COALESCE(taken, mtime) DESC, dest "
                       f"LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


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
