"""Sync checks: did every file make it, is every copy identical, has the source changed since the scan."""

import os
import random

from .db import get_meta
from .hashing import hash_file
from .scan import walk


def verify(con, sample: int = 200, rescan: bool = False, progress=print) -> dict:
    """sample: how many copied files to checksum in full; -1 checks every one (hardlinks need no reading)."""
    source, library = get_meta(con, "source"), get_meta(con, "library")
    problems = {"unplaced": [], "missing": [], "mismatch": [], "changed": [], "deleted": [], "new": []}

    # 1. Accounting: every source file is placed, a duplicate of a placed file, or deliberate junk.
    total = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    counts = dict(con.execute(
        "SELECT CASE WHEN action='place' AND status='done' THEN 'placed' "
        "WHEN action='duplicate' THEN 'duplicate' WHEN action='skip' THEN 'junk' ELSE 'unplaced' END, COUNT(*) "
        "FROM files GROUP BY 1").fetchall())
    problems["unplaced"] = [r[0] for r in con.execute(
        "SELECT path FROM files WHERE NOT (action='place' AND status='done') "
        "AND action NOT IN ('duplicate','skip')")]
    progress(f"Accounting: {total:,} source files = {counts.get('placed', 0):,} placed + "
             f"{counts.get('duplicate', 0):,} duplicates + {counts.get('junk', 0):,} junk"
             + (f" + {counts['unplaced']:,} NOT PLACED" if counts.get("unplaced") else ""))

    # 2. Each placed file matches its source.
    placed = con.execute(
        "SELECT path, dest, method, size, mtime FROM files WHERE action='place' AND status='done'").fetchall()
    copies = []
    for r in placed:
        src, dst = os.path.join(source, r["path"]), os.path.join(library, r["dest"])
        try:
            d = os.stat(dst)
        except OSError:
            problems["missing"].append(r["dest"])
            continue
        if r["method"] == "link":
            try:
                s = os.stat(src)
                if (s.st_dev, s.st_ino) != (d.st_dev, d.st_ino):
                    problems["mismatch"].append(r["dest"])
            except OSError:
                pass  # source removed later; reported under 'deleted' with --rescan
        else:
            if d.st_size != r["size"] or int(d.st_mtime) != int(r["mtime"]):
                problems["mismatch"].append(r["dest"])
            else:
                copies.append((src, dst, r["dest"]))
    n = len(copies) if sample < 0 else min(sample, len(copies))
    for src, dst, rel in random.sample(copies, n):
        try:
            if hash_file(src) != hash_file(dst):
                problems["mismatch"].append(rel)
        except OSError:
            pass
    progress(f"Integrity: {len(placed):,} placed files checked, {len(problems['missing']):,} missing, "
             f"{len(problems['mismatch']):,} differ" + (f" (checksummed {n:,} of {len(copies):,} copies)" if copies else ""))

    # 3. Source drift since the scan.
    if rescan:
        known = {r["path"]: (r["size"], r["mtime"]) for r in con.execute("SELECT path, size, mtime FROM files")}
        seen = set()
        for rel, st in walk(source):
            seen.add(rel)
            if rel not in known:
                problems["new"].append(rel)
            elif (st.st_size, int(st.st_mtime)) != (known[rel][0], int(known[rel][1])):
                problems["changed"].append(rel)
        problems["deleted"] = [p for p in known if p not in seen]
        progress(f"Source drift: {len(problems['new']):,} new, {len(problems['changed']):,} changed, "
                 f"{len(problems['deleted']):,} deleted since the scan")

    ok = not any(problems.values())
    progress("RESULT: everything is in sync." if ok else "RESULT: differences found (see report).")
    return {"ok": ok, "counts": counts, "total": total, "problems": problems}
