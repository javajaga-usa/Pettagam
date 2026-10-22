"""Automatic views: extra folders under <library>/views/ that show the same files arranged another way,
such as photos by year or by camera. Each entry is a hardlink, so views take no extra space, and
deleting a view never deletes the file in the library.

Views use the same conditions as rules (see rules.py) and are read from <library>/_meta/views.json;
without that file the built-in views below are used. Unlike rules, a file appears in every view it
matches. The views folder is Pettagam's: it is rebuilt each time, so don't keep your own files there.
"""

import json
import os
import re

from .classify import normalize_ext
from .rules import Rule, RuleError, _check_template, compile_cond, facts, matches, render

VIEWS_DIR = "views"
VIEWS_FILE = os.path.join("_meta", "views.json")
LIST_FILE = os.path.join("_meta", "views-links.txt")

BUILT_IN = {
    "views": [
        {"name": "Photos and videos by year",
         "if": {"category": ["images", "videos"], "subtype": ["photos", "raw", "recordings"]},
         "then": "by-year/{year}/{month}"},
        {"name": "By camera", "if": {"camera": True}, "then": "by-camera/{camera}"},
        {"name": "Recent", "if": {"newer_than": "30d"}, "then": "recent/{category}"},
        {"name": "Large files", "if": {"size_over": "500MB"}, "then": "large-files/{category}"},
        {"name": "By tag", "if": {"tags": True}, "then": "by-tag/{tag}"},
    ]
}


def parse_views(data) -> list:
    if not isinstance(data, dict) or not isinstance(data.get("views"), list):
        raise RuleError('the file must look like {"views": [ ... ]}')
    out = []
    for i, v in enumerate(data["views"], 1):
        if not isinstance(v, dict):
            raise RuleError(f"view #{i}: expected {{...}}")
        name = str(v.get("name") or f"view {i}")
        where = f"view '{name}'"
        then = v.get("then")
        # {tag} is only for views: one folder per tag the file has.
        checked = _check_template(then.replace("{tag}", "{category}") if isinstance(then, str) else then, where)
        if not v.get("off"):
            out.append(Rule(name, compile_cond(v.get("if"), where), then.strip("/") if "{tag}" in then else checked))
    return out


def load_views(library: str, path: str = None) -> list:
    path = path or os.path.join(library, VIEWS_FILE)
    if not os.path.exists(path):
        return parse_views(BUILT_IN)
    try:
        with open(path, encoding="utf-8") as f:
            return parse_views(json.load(f))
    except ValueError as e:
        raise RuleError(f"{path}: {e}") from None


def write_starter(library: str) -> str:
    path = os.path.join(library, VIEWS_FILE)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(BUILT_IN, f, indent=2)
            f.write("\n")
    return path


def library_items(con):
    """Every file in the library, as facts the conditions can test, with its library path."""
    for row in con.execute("SELECT * FROM files WHERE action='place' AND status='done'"):
        f = facts(row, row["category"], row["subtype"], row["confidence"])
        f["path"] = row["dest"]
        yield row["dest"], f
    for row in con.execute("SELECT * FROM inbox WHERE action='placed'"):
        name = os.path.basename(row["dest"])
        f = {"name": name, "path": row["dest"], "ext": normalize_ext(name), "kind": "", "size": row["size"],
             "mtime": row["mtime"], "taken": row["taken"], "camera": row["camera"] or "",
             "category": row["category"] or "", "subtype": row["subtype"] or "", "confidence": row["confidence"],
             "tags": set(t for t in (row["tags"] or "").split(",") if t)}
        yield row["dest"], f


def _folders(view: Rule, f: dict) -> list:
    if "{tag}" not in view.then:
        return [render(view.then, f)]
    out = []
    for tag in sorted(f["tags"]):
        safe = re.sub(r"[^\w .-]", "_", tag).strip(" .") or "_"
        out.append(render(view.then.replace("{tag}", safe), f))
    return out


def _link(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:  # a drive without hardlinks (exFAT): fall back to a shortcut
        os.symlink(os.path.relpath(src, os.path.dirname(dst)), dst)


def _remove_old(library: str) -> int:
    path = os.path.join(library, LIST_FILE)
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            old = [line.rstrip("\n") for line in f if line.strip()]
    except OSError:
        return 0
    root = os.path.join(library, VIEWS_DIR)
    for rel in old:
        p = os.path.join(root, rel)
        if os.path.lexists(p) and not os.path.isdir(p):
            os.remove(p)  # only a link Pettagam made; the library file stays
            n += 1
    for d, _, _ in os.walk(root, topdown=False):
        if d != root:
            try:
                os.rmdir(d)  # only empty folders
            except OSError:
                pass
    return n


def build_views(con, library: str, views=None, now=None, progress=print) -> dict:
    """Rebuild <library>/views/ from the catalog. Returns links made per view."""
    library = os.path.abspath(library)
    views = load_views(library) if views is None else views
    removed = _remove_old(library)
    root = os.path.join(library, VIEWS_DIR)
    made, counts, used = [], {v.name: 0 for v in views}, {}
    for dest, f in library_items(con):
        src = os.path.join(library, dest)
        if not os.path.exists(src):
            continue
        name = os.path.basename(dest)
        for v in views:
            if not matches(v.cond, f, now):
                continue
            for folder in _folders(v, f):
                taken = used.setdefault(folder.lower(), set())
                candidate, k = name, 1
                while candidate.lower() in taken:
                    k += 1
                    dot = name.rfind(".")
                    candidate = f"{name[:dot]}__{k}{name[dot:]}" if dot > 0 else f"{name}__{k}"
                taken.add(candidate.lower())
                rel = f"{folder}/{candidate}"
                if os.path.lexists(os.path.join(root, rel)):
                    continue  # something that isn't ours; leave it alone
                _link(src, os.path.join(root, rel))
                made.append(rel)
                counts[v.name] += 1
    os.makedirs(os.path.join(library, "_meta"), exist_ok=True)
    with open(os.path.join(library, LIST_FILE), "w", encoding="utf-8") as f:
        f.write("\n".join(made) + ("\n" if made else ""))
    progress(f"Built {len(made):,} view entries in {root} (removed {removed:,} old ones)")
    for name, n in counts.items():
        progress(f"  {name}: {n:,}")
    return counts
