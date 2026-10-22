"""Check the library for silent damage (bit rot) on a drive that holds files for years.

The first check records a fingerprint of every library file. Later checks read each file again and
compare. A file whose content changed while its size and date did not is reported as damaged: that
is what a failing disk looks like, as opposed to a file someone edited. Checks can run in slices
(--minutes), oldest-checked first, so a large drive gets covered over a few nights.
"""

import os
import time

from .db import get_meta, set_meta
from .hashing import hash_file

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixity (
    dest     TEXT PRIMARY KEY,  -- relative to the library root
    hash     TEXT NOT NULL,
    size     INTEGER NOT NULL,
    mtime    REAL NOT NULL,
    checked  REAL NOT NULL,     -- last time it was read and compared
    status   TEXT NOT NULL      -- ok | damaged | changed
);
"""


def _library_dests(con) -> list:
    return [r[0] for r in con.execute(
        "SELECT dest FROM files WHERE action='place' AND status='done' "
        "UNION SELECT dest FROM inbox WHERE action='placed'")]


def check(con, library: str = None, minutes: float = 0, limit: int = 0, progress=print) -> dict:
    """Read library files, oldest-checked first, and compare with their fingerprints."""
    library = library or get_meta(con, "library")
    if not library or not os.path.isdir(library):
        raise SystemExit(f"Library not found: {library or '(not set)'}")
    con.executescript(SCHEMA)
    dests = _library_dests(con)
    known = {r["dest"]: r for r in con.execute("SELECT * FROM fixity")}
    current = set(dests)
    gone = [d for d in known if d not in current]
    con.executemany("DELETE FROM fixity WHERE dest=?", [(d,) for d in gone])  # undone or removed by Pettagam
    order = sorted(dests, key=lambda d: (d in known, known[d]["checked"] if d in known else 0, d))
    if limit:
        order = order[:limit]
    deadline = time.time() + minutes * 60 if minutes else 0
    res = {"recorded": 0, "ok": 0, "damaged": [], "changed": [], "missing": [], "unreadable": [],
           "remaining": 0, "bytes": 0}
    for i, dest in enumerate(order):
        if deadline and time.time() > deadline:
            res["remaining"] = len(order) - i
            break
        path = os.path.join(library, dest)
        try:
            st = os.stat(path)
            h = hash_file(path)
        except FileNotFoundError:
            res["missing"].append(dest)
            continue
        except OSError as e:
            res["unreadable"].append(f"{dest}: {e.strerror or e}")
            continue
        res["bytes"] += st.st_size
        now = time.time()
        old = known.get(dest)
        if old is None:
            status = "ok"
            res["recorded"] += 1
        elif old["hash"] == h:
            status = "ok"
            res["ok"] += 1
        elif old["size"] == st.st_size and int(old["mtime"]) == int(st.st_mtime):
            status = "damaged"
            res["damaged"].append(dest)
        else:
            status = "changed"
            res["changed"].append(dest)
        if status == "damaged":
            # Keep the good fingerprint, so the file stays flagged until it is restored.
            con.execute("UPDATE fixity SET checked=?, status='damaged' WHERE dest=?", (now, dest))
        else:
            con.execute("INSERT OR REPLACE INTO fixity VALUES (?,?,?,?,?,?)",
                        (dest, h, st.st_size, st.st_mtime, now, status))
        if (i + 1) % 500 == 0:
            con.commit()
            progress(f"  {i + 1:,} of {len(order):,} files read")
    con.commit()
    set_meta(con, "fixity_last", time.time())
    for k in ("damaged", "missing", "unreadable"):
        for d in res[k][:20]:
            progress(f"  {k.upper()}: {d}")
    progress(f"Checked {res['recorded'] + res['ok'] + len(res['damaged']) + len(res['changed']):,} files "
             f"({res['bytes'] / 1e9:,.2f} GB): {res['recorded']:,} recorded for the first time, {res['ok']:,} unchanged, "
             f"{len(res['changed']):,} edited, {len(res['damaged']):,} DAMAGED, {len(res['missing']):,} missing"
             + (f"; {res['remaining']:,} left for next time" if res["remaining"] else ""))
    if res["damaged"]:
        progress("Damaged files changed without being edited: the drive may be failing. Restore them from the "
                 "old files or a backup, and copy everything you can to another drive.")
    return res


def summary(con) -> dict:
    """For the health check: when the library was last checked and what was found."""
    try:
        rows = dict(con.execute("SELECT status, COUNT(*) FROM fixity GROUP BY status").fetchall())
        oldest = con.execute("SELECT MIN(checked) FROM fixity").fetchone()[0]
    except Exception:  # no fixity table yet
        rows, oldest = {}, None
    last = get_meta(con, "fixity_last")
    return {"last": float(last) if last else None, "oldest": oldest, "damaged": rows.get("damaged", 0),
            "fingerprinted": sum(rows.values())}
