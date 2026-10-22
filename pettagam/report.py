"""Dry-run outputs: a readable summary (report.md) and the full placement list (plan.csv)."""

import csv
import os
import posixpath

from .db import get_meta


def _gb(n) -> str:
    n = n or 0
    return f"{n / 1e9:,.2f} GB" if n >= 1e8 else f"{n / 1e6:,.1f} MB"


def write_plan_csv(con, path: str) -> None:
    cols = ["path", "dest", "action", "category", "subtype", "confidence", "reason", "rule", "size", "junk",
            "dup_of_path"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in con.execute(
                "SELECT f.*, d.path AS dup_of_path FROM files f LEFT JOIN files d ON d.id=f.dup_of "
                "ORDER BY f.action, f.dest, f.path"):
            w.writerow([r[c] for c in cols])


def report(con, out_dir: str, progress=print) -> str:
    os.makedirs(out_dir, exist_ok=True)
    write_plan_csv(con, os.path.join(out_dir, "plan.csv"))
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    total, size = q("SELECT COUNT(*), SUM(size) FROM files")[0]
    lines = [
        "# Pettagam dry-run report", "",
        f"Source: `{get_meta(con, 'source')}`  ",
        f"Library: `{get_meta(con, 'library')}`", "",
        f"**{total:,} files, {_gb(size)}.** Nothing has been copied or changed yet; "
        "`plan.csv` lists every file with its planned location.", "",
        "## Where files will go", "",
        "| Folder | Files | Size |", "| --- | ---: | ---: |",
    ]
    for r in q("SELECT folder, COUNT(*) AS n, SUM(size) AS b, COUNT(DISTINCT rtrim(dest, replace(dest, '/', ''))) AS parts "
               "FROM files WHERE action='place' GROUP BY folder ORDER BY folder"):
        split = f" (split into {r['parts']} subfolders)" if r["parts"] > 1 else ""
        lines.append(f"| `{r['folder']}/`{split} | {r['n']:,} | {_gb(r['b'])} |")

    ruled = q("SELECT rule, COUNT(*) AS n, SUM(size) AS b FROM files WHERE action='place' AND rule IS NOT NULL "
              "GROUP BY rule ORDER BY n DESC")
    if ruled:
        lines += ["", "## Your rules", "", "| Rule | Files | Size |", "| --- | ---: | ---: |"]
        lines += [f"| {r['rule']} | {r['n']:,} | {_gb(r['b'])} |" for r in ruled]

    dup_n, dup_b = q("SELECT COUNT(*), SUM(size) FROM files WHERE action='duplicate'")[0]
    junk = q("SELECT junk, COUNT(*) AS n, SUM(size) AS b FROM files WHERE action='skip' GROUP BY junk ORDER BY n DESC")
    lines += ["", "## Duplicates", "",
              f"{dup_n:,} files are exact copies of another file ({_gb(dup_b)}). "
              "Each is stored once; the manifest records every original path.", ""]
    for r in q("SELECT d.path AS keep, COUNT(*) AS n, SUM(f.size) AS b FROM files f JOIN files d ON d.id=f.dup_of "
               "GROUP BY f.dup_of ORDER BY b DESC LIMIT 10"):
        lines.append(f"- `{r['keep']}`: {r['n']} extra copies, {_gb(r['b'])}")

    lines += ["", "## Skipped as junk", ""]
    lines += [f"- {r['junk']}: {r['n']:,} files ({_gb(r['b'])})" for r in junk] or ["- none"]

    review = q("SELECT reason, COUNT(*) AS n FROM files WHERE action='place' AND folder LIKE 'review/%' "
               "GROUP BY reason ORDER BY n DESC")
    lines += ["", "## Needs review", "",
              f"{sum(r['n'] for r in review):,} files had weak clues and go to `review/` instead of being guessed.", ""]
    lines += [f"- {r['reason']}: {r['n']:,}" for r in review]

    renamed = q("SELECT path, dest FROM files WHERE action='place'")
    changed = [(r["path"], r["dest"]) for r in renamed
               if posixpath.basename(r["dest"]) != os.path.basename(r["path"])]
    lines += ["", "## Renamed", "",
              f"{len(changed):,} files get a new name (lowercase extension, characters Windows cannot open, "
              "missing or wrong extension, or a __2 suffix for a clash)." + (" Examples:" if changed else ""), ""]
    lines += [f"- `{os.path.basename(a)}` → `{posixpath.basename(b)}`" for a, b in changed[:15]]

    other = q("SELECT subtype, COUNT(*) AS n FROM files WHERE action='place' AND category='other' "
              "GROUP BY subtype ORDER BY n DESC LIMIT 15")
    if other:
        lines += ["", "## Unrecognised types (other/)", ""]
        lines += [f"- `.{r['subtype']}`: {r['n']:,}" for r in other]

    path = os.path.join(out_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    progress(f"Wrote {path} and {os.path.join(out_dir, 'plan.csv')}")
    return path
