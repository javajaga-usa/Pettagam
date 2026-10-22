"""Plan the move of the library (and the old files) to another computer, such as the Ubuntu server.

    pettagam move-plan --to /srv/ninaivu --host you@server [--write DIR]

Pettagam doesn't copy anything itself: it writes out the exact steps and commands, for you to read and run.
Ubuntu can't write the Mac's APFS drive, so the files travel by `rsync -aH` onto an ext4 drive, which keeps
hardlinks: a library made of hardlinks to the old files stays free of extra space only if both are copied in
the same run, from their common folder. Then `relocate` points the catalog that travelled with the library at
the new places.
"""

import os
import shlex

from .db import get_meta
from .maintenance import CATALOG_COPY


def _common(source: str, library: str) -> str:
    return os.path.commonpath([os.path.abspath(source), os.path.abspath(library)])


def plan(con, to: str, host: str = None, library_only: bool = False) -> dict:
    """Work out what to copy where. Returns the new paths, the size to copy and the steps."""
    source, library = get_meta(con, "source"), get_meta(con, "library")
    if not library:
        raise SystemExit("No library in the catalog yet: run a dry run and place files first.")
    to = to.rstrip("/") or "/"
    if not os.path.isabs(to):
        raise SystemExit("--to must be a full path on the new computer, such as /srv/ninaivu")
    linked = con.execute("SELECT COUNT(*) FROM files WHERE status='done' AND method='link'").fetchone()[0]
    # Hardlinked files must travel in one rsync run with their originals, or they become separate copies.
    together = bool(source) and linked > 0 and not library_only
    if together:
        root = _common(source, library)
        new_source = os.path.join(to, os.path.relpath(source, root))
        new_library = os.path.join(to, os.path.relpath(library, root))
        copy_from = root
        unique = con.execute("SELECT COALESCE(SUM(size), 0) FROM (SELECT MAX(size) AS size FROM files "
                             "GROUP BY dev, ino)").fetchone()[0]
        copied = con.execute("SELECT COALESCE(SUM(size), 0) FROM files WHERE status='done' AND method='copy'"
                             ).fetchone()[0]
    else:
        new_source = None
        new_library = os.path.join(to, os.path.basename(library.rstrip("/")))
        copy_from = library
        unique, copied = 0, con.execute("SELECT COALESCE(SUM(size), 0) FROM files WHERE status='done'"
                                        ).fetchone()[0]
    inbox_bytes = con.execute("SELECT COALESCE(SUM(size), 0) FROM inbox WHERE action='placed'").fetchone()[0]
    nbytes = unique + copied + inbox_bytes
    dest = f"{host}:{new_library if not together else to}" if host else (new_library if not together else to)

    q = shlex.quote
    if together:
        # Both folders in one run (so hardlinks between them survive), and nothing else on the drive:
        # with --relative, the part after "/./" is recreated under the destination.
        what = [os.path.join(root, ".", os.path.relpath(x, root)) for x in (source, library)]
        rsync = ["rsync", "-aH", "--relative", "--partial", "--protect-args", "--info=progress2", *what,
                 dest.rstrip("/") + "/"]
    else:
        rsync = ["rsync", "-aH", "--partial", "--protect-args", "--info=progress2",
                 copy_from.rstrip("/") + "/", dest.rstrip("/") + "/"]
    ssh = (lambda cmd: f"ssh {q(host)} {q(cmd)}") if host else (lambda cmd: cmd)
    mkdir = f"mkdir -p {q(new_library if not together else to)}"
    relocate = ["pettagam", "relocate", "--catalog-from", os.path.join(new_library, CATALOG_COPY),
                "--library", new_library] + (["--source", new_source] if new_source else [])
    steps = [
        {"where": "here", "title": "Check the library is complete and undamaged, and stop background sorting",
         "commands": ["pettagam verify --rescan", "pettagam fixity", "pettagam service off"]},
        {"where": "here", "title": "Check rsync is version 3 (the Mac's own may be older: brew install rsync)",
         "commands": ["rsync --version | head -1"]},
        {"where": "new computer" if host else "here", "title": "Make the folder on the ext4 drive",
         "commands": [ssh(mkdir)]},
        {"where": "here", "title": ("Copy the old files and the library together, so their hardlinks stay "
                                    "hardlinks" if together else "Copy the library")
                                   + " (safe to run again: it carries on where it stopped)",
         "commands": [" ".join(q(a) for a in rsync)]},
        {"where": "new computer", "title": "Set Pettagam up there and point its catalog at the new places",
         "commands": ["./launchers/setup-ubuntu.sh", " ".join(q(a) for a in relocate)]},
        {"where": "new computer", "title": "Prove every file arrived intact",
         "commands": ["pettagam verify --rescan", "pettagam fixity"]},
        {"where": "new computer", "title": "Choose the inbox there and turn background sorting back on",
         "commands": ["pettagam service on --inbox /path/to/Inbox"]},
    ]
    return {"source": source, "library": library, "new_source": new_source, "new_library": new_library,
            "together": together, "linked": linked, "bytes": nbytes, "copy_from": copy_from, "steps": steps,
            "host": host}


def render(p: dict) -> str:
    gb = p["bytes"] / 1e9
    lines = ["Moving the Pettagam library", "",
             f"  library     {p['library']}  ->  {p['new_library']}"]
    if p["new_source"]:
        lines.append(f"  old files   {p['source']}  ->  {p['new_source']}")
    lines += [f"  to copy     about {gb:,.1f} GB" + (" (hardlinked files counted once)" if p["together"] else ""),
              ""]
    if not p["together"] and p["linked"]:
        lines += [f"  Note: {p['linked']:,} library files are hardlinks to the old files. Copying the library alone",
                  "  turns them into full copies on the new drive, which needs the space of all of them.", ""]
    elif not p["together"] and p["source"]:
        lines += ["  The library holds its own copies, so it moves on its own. Copy the old files separately",
                  "  if you want them on the new computer too.", ""]
    for i, s in enumerate(p["steps"], 1):
        lines.append(f"{i}. {s['title']}  [{s['where']}]")
        lines += [f"     {c}" for c in s["commands"]]
        lines.append("")
    lines.append("Pettagam doesn't run any of this for you: read each step, then run it.")
    return "\n".join(lines) + "\n"


def write_scripts(p: dict, directory: str) -> list:
    """Two scripts: what runs here, and what runs on the new computer. Never overwrites."""
    os.makedirs(directory, exist_ok=True)
    out = []
    for name, where in (("move-1-here.sh", ("here",)), ("move-2-new-computer.sh", ("new computer",))):
        path = os.path.join(directory, name)
        if os.path.exists(path):
            raise SystemExit(f"{path} already exists; choose another folder.")
        body = ["#!/bin/sh", f"# Pettagam move plan, part {name[5]}. Read it before running it.", "set -eu", ""]
        for s in p["steps"]:
            if s["where"] in where:
                body += [f"# {s['title']}"] + s["commands"] + [""]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body))
        os.chmod(path, 0o755)
        out.append(path)
    return out
