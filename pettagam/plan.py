"""Phase 2: decide where every file goes. Writes only to the catalog, never to disk."""

import os
import re
import time
from collections import defaultdict

from . import classify as cl
from .db import get_meta, set_meta
from .rules import facts, first_match
from .scan import PROGRESS_EVERY

BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')  # not allowed by Windows/Samba clients
SPLIT_AT = 5000


def safe_name(name: str, ext: str) -> str:
    """Original name, cleaned for every client, with a lowercase canonical extension."""
    dot = name.rfind(".")
    stem = name[:dot] if dot > 0 else name
    if name.lower().endswith(".tar.gz"):
        stem = name[:-7]
    stem = BAD_CHARS.sub("_", stem).rstrip(" .") or "unnamed"
    return f"{stem}.{ext}" if ext else stem


def folder_for(r: cl.Result, ext: str) -> str:
    if r.confidence < cl.REVIEW_BELOW and r.category != "other":
        return f"review/{r.category}"
    if r.category == "other":
        return f"other/{r.subtype}"
    return f"{r.category}/{r.subtype}" if r.subtype else r.category


def decide(row, rules=(), now=None):
    """(classification, folder, rule name) for one catalog row. Your rules win over the smart types."""
    r = cl.classify(row["name"], row["ext"], row["size"], row["width"], row["height"],
                    row["camera"], bool(row["has_exif"]), bool(row["has_tags"]))
    rule, folder = first_match(rules, facts(row, r.category, r.subtype, r.confidence), now)
    if rule:
        return r, folder, rule.name
    return r, folder_for(r, row["ext"]), None


def rule_counts(con, rules) -> dict:
    """How many of the old files each rule would place (duplicates and junk aside)."""
    counts = {rl.name: 0 for rl in rules}
    for row in con.execute("SELECT * FROM files WHERE junk IS NULL AND dup_of IS NULL"):
        _, _, name = decide(row, rules)
        if name:
            counts[name] += 1
    return counts


def explain(path: str, rules=(), now=None) -> dict:
    """Where one file on disk would go, and why: the facts Pettagam sees and the rule or smart type that decides."""
    import time

    from .scan import _examine
    st = os.stat(path)
    e = _examine(os.path.dirname(os.path.abspath(path)), os.path.basename(path), st)
    row = dict(zip(("path", "name", "size", "mtime", "dev", "ino", "ext", "kind", "width", "height",
                    "camera", "has_exif", "has_tags", "junk", "taken"), e, strict=True))
    if row["junk"]:
        return {"file": path, "junk": row["junk"], "folder": None, "rule": None}
    r, folder, rule = decide(row, rules, now)
    f = facts(row, r.category, r.subtype, r.confidence)
    when = row["taken"] or row["mtime"]
    return {"file": path, "junk": None, "folder": folder, "rule": rule, "category": r.category,
            "subtype": r.subtype, "confidence": r.confidence, "reason": r.reason, "kind": row["kind"],
            "ext": row["ext"], "size": row["size"], "camera": f.get("camera") or "",
            "date": time.strftime("%Y-%m-%d", time.gmtime(when) if row["taken"] else time.localtime(when)),
            "date_from": "photo's capture date" if row["taken"] else "file's modified date"}


def _dedupe_names(names):
    """Give clashing names a __2, __3 suffix. Case-insensitive, so Mac and Windows clients agree."""
    seen, out = set(), []
    for n in names:
        candidate, k = n, 1
        while candidate.lower() in seen:
            k += 1
            dot = n.rfind(".")
            candidate = f"{n[:dot]}__{k}{n[dot:]}" if dot > 0 else f"{n}__{k}"
        seen.add(candidate.lower())
        out.append(candidate)
    return out


def _bucket_key(name: str, depth: int) -> str:
    """Folder for a name at a split level: 'a', '0-9', '_' at the top, then 'sa', 'sam' deeper."""
    if depth == 0:
        c = name[0].lower() if name else "_"
        return "0-9" if c.isdigit() else (c if c.isalnum() else "_")
    return "".join(c if c.isalnum() else "_" for c in name[:depth + 1].lower()).ljust(depth + 1, "_")


def _ranges(names, split_at):
    """Last resort for names sharing a long prefix (IMG_0001...): sorted chunks named like
    dictionary guide words, e.g. 'img_0001.jpg--img_4999.jpg'."""
    ordered = sorted(names, key=str.lower)
    chunks = -(-len(ordered) // split_at)
    size = -(-len(ordered) // chunks)
    out = {}
    for i in range(0, len(ordered), size):
        part = ordered[i:i + size]
        label = f"{part[0][:24]}--{part[-1][:24]}".lower()
        label = "".join(c if c.isalnum() or c in "._-" else "_" for c in label)
        out.update({n: f"{label}/" for n in part})
    return out


def _split(names, split_at, depth=0):
    """Map each name to a sub-path like 'a/' or 's/sa/', only for folders over the limit."""
    if len(names) <= split_at:
        return {n: "" for n in names}
    groups = defaultdict(list)
    for n in names:
        groups[_bucket_key(n, depth)].append(n)
    if depth >= 3 or (len(groups) == 1 and depth > 0):  # prefixes stopped helping
        return _ranges(names, split_at)
    out = {}
    for key, members in groups.items():
        for n, sub in _split(members, split_at, depth + 1).items():
            out[n] = f"{key}/{sub}"
    return out


def plan(con, library: str, split_at: int = SPLIT_AT, rules=(), progress=print) -> dict:
    source = get_meta(con, "source")
    library = os.path.abspath(library)
    src = os.path.abspath(source) + os.sep
    lib = library + os.sep
    if lib.startswith(src) or src.startswith(lib):
        raise SystemExit("The library must be outside the source folder (and vice versa).")
    set_meta(con, "library", library)
    set_meta(con, "split_at", split_at)

    con.execute("UPDATE files SET action='skip', dest=NULL, category=NULL, subtype=NULL, rule=NULL WHERE junk IS NOT NULL")
    con.execute("UPDATE files SET action='duplicate', dest=NULL WHERE junk IS NULL AND dup_of IS NOT NULL")

    folders = defaultdict(list)  # folder -> [(id, name)]
    rows = con.execute(
        "SELECT * FROM files WHERE junk IS NULL AND dup_of IS NULL ORDER BY path").fetchall()
    by_rule = defaultdict(int)
    said = time.time()
    for i, row in enumerate(rows, 1):
        if time.time() - said >= PROGRESS_EVERY:
            said = time.time()
            progress(f"  planned {i:,} of {len(rows):,} files")
        r, folder, rule = decide(row, rules)
        con.execute("UPDATE files SET category=?, subtype=?, confidence=?, reason=?, rule=? WHERE id=?",
                    (r.category, r.subtype, r.confidence, r.reason, rule, row["id"]))
        folders[folder].append((row["id"], safe_name(row["name"], row["ext"])))
        if rule:
            by_rule[rule] += 1

    for folder, items in folders.items():
        names = _dedupe_names([n for _, n in items])
        subs = _split(names, split_at)
        for (fid, _), n in zip(items, names, strict=True):
            con.execute("UPDATE files SET folder=?, dest=?, action='place', status=NULL WHERE id=?",
                        (folder, f"{folder}/{subs[n]}{n}", fid))
    con.commit()

    # Duplicates inherit the classification of their canonical copy, for reports.
    con.execute("""UPDATE files SET category=(SELECT c.category FROM files c WHERE c.id=files.dup_of),
                                     subtype=(SELECT c.subtype FROM files c WHERE c.id=files.dup_of),
                                     rule=(SELECT c.rule FROM files c WHERE c.id=files.dup_of)
                   WHERE action='duplicate'""")
    con.commit()
    counts = dict(con.execute("SELECT action, COUNT(*) FROM files GROUP BY action").fetchall())
    progress(f"Planned {counts.get('place', 0):,} placements, {counts.get('duplicate', 0):,} duplicates, "
             f"{counts.get('skip', 0):,} junk files skipped, into {len(folders):,} folders")
    for name, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        progress(f"  your rule '{name}': {n:,} files")
    return counts
