"""pettagam command line.

  pettagam scan SOURCE              read-only inventory (+ duplicate detection)
  pettagam plan --library LIB       decide every file's place; writes report.md + plan.csv
  pettagam apply                    hardlink or copy files into the library
  pettagam verify [--rescan]        check everything is in sync
  pettagam dryrun SOURCE --library LIB   scan + plan + report in one go (changes nothing)
  pettagam panel                    open the point-and-click panel (and library explorer) in your browser
  pettagam rules [--init]           check your placement rules and see what they match
  pettagam inbox INBOX [--watch N]  file new files from an inbox folder into the library
  pettagam views [--init]           rebuild the automatic views (by year, by camera, ...) as hardlinks
  pettagam tag                      add AI tags and descriptions to photos and documents (optional, paid)
  pettagam doctor                   check drives, hardlinks, free space and setup
  pettagam service on|off|status|refresh  sort the inbox automatically in the background
  pettagam undo                     take placed files back out of the library (old files untouched)
  pettagam relocate                 follow the files after the drive is mounted somewhere else
  pettagam find WORDS [--tag T]     search the library by name, folder, tags, camera, type or year
  pettagam stats                    what is in the library: by type, year, camera and tag
  pettagam duplicates [--csv F]     list the identical copies found in the old files
  pettagam export DEST WORDS        copy the files that match a search to another folder
  pettagam fixity [--minutes N]     check the library for silent damage (bit rot)

The catalog is $PETTAGAM_DB if set, else ./pettagam.db if it exists, else the panel's
catalog in ~/.pettagam/pettagam.db, so the panel and the command line share one catalog.
"""

import argparse
import json
import os
import sqlite3
import sys
import time

from . import __version__
from .apply import apply
from .db import connect, get_meta
from .hashing import find_duplicates
from .plan import SPLIT_AT, explain, plan, rule_counts
from .report import report
from .rules import RuleError, default_path, load_rules, write_starter
from .scan import scan
from .verify import verify


def default_db() -> str:
    env = os.environ.get("PETTAGAM_DB")
    if env:
        return env
    if os.path.exists("pettagam.db"):
        return "pettagam.db"
    shared = os.path.join(os.path.expanduser("~"), ".pettagam", "pettagam.db")
    older = os.path.join(os.path.expanduser("~"), "pettagam.db")  # where the 0.2 README put it
    return older if os.path.exists(older) and not os.path.exists(shared) else shared


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pettagam", description="Organize a messy file tree into a smart library.")
    p.add_argument("--version", action="version", version=f"pettagam {__version__}")
    p.add_argument("--db", default=default_db(),
                   help="catalog file; keep it on an SSD (default: $PETTAGAM_DB, ./pettagam.db, or the panel's)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="read-only inventory of SOURCE")
    s.add_argument("source")
    s.add_argument("--workers", type=int, default=2, help="parallel readers; 2 for a hard drive, 8+ for SSD or network")
    s.add_argument("--no-dupes", action="store_true", help="skip duplicate detection")

    pl = sub.add_parser("plan", help="plan placements and write the dry-run report")
    pl.add_argument("--library", required=True)
    pl.add_argument("--split-at", type=int, default=SPLIT_AT, help="files per folder before it splits")
    pl.add_argument("--out", default="pettagam-report")
    pl.add_argument("--rules", help="your rules file (default: <library>/_meta/rules.json)")

    d = sub.add_parser("dryrun", help="scan + plan + report; changes nothing on disk")
    d.add_argument("source")
    d.add_argument("--library", required=True)
    d.add_argument("--workers", type=int, default=2)
    d.add_argument("--split-at", type=int, default=SPLIT_AT)
    d.add_argument("--out", default="pettagam-report")
    d.add_argument("--rules", help="your rules file (default: <library>/_meta/rules.json)")

    a = sub.add_parser("apply", help="place files into the library (source is never modified)")
    a.add_argument("--workers", type=int, default=4, help="parallel copies; 2-4 for hard drives")
    a.add_argument("--method", choices=["auto", "link", "copy"], default="auto")
    a.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    v = sub.add_parser("verify", help="check every file made it and matches")
    v.add_argument("--sample", type=int, default=200, help="copies to checksum in full")
    v.add_argument("--full", action="store_true", help="checksum every copied file, not just a sample")
    v.add_argument("--rescan", action="store_true", help="also look for changes in the source since the scan")
    v.add_argument("--json", help="write full results to this file")

    pn = sub.add_parser("panel", help="open the point-and-click panel in your browser")
    pn.add_argument("--port", type=int, default=8484)
    pn.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = this computer only; 0.0.0.0 = reachable from your network")
    pn.add_argument("--no-browser", action="store_true", help="do not open a browser window")

    r = sub.add_parser("rules", help="check your placement rules and count what each one matches")
    r.add_argument("--library", help="default: the library from the last dry run")
    r.add_argument("--rules", help="default: <library>/_meta/rules.json")
    r.add_argument("--init", action="store_true", help="create a starter rules file with examples switched off")
    r.add_argument("--explain", nargs="+", metavar="FILE", help="show where these files would go, and why")

    ib = sub.add_parser("inbox", help="file new files from an inbox folder into the library")
    ib.add_argument("inbox")
    ib.add_argument("--library", help="default: the library from the last dry run")
    ib.add_argument("--rules", help="default: <library>/_meta/rules.json")
    ib.add_argument("--watch", type=float, metavar="SECONDS", help="keep running, checking every SECONDS")
    ib.add_argument("--settle", type=float, default=10, help="leave files changed in the last N seconds (default 10)")
    ib.add_argument("--dry-run", action="store_true", help="only show where files would go")
    ib.add_argument("--notify", action="store_true", help="show a desktop notification when files were filed")

    vw = sub.add_parser("views", help="rebuild the automatic views under <library>/views/")
    vw.add_argument("--library", help="default: the library from the last dry run")
    vw.add_argument("--init", action="store_true", help="write the built-in views to _meta/views.json to edit them")

    tg = sub.add_parser("tag", help="add AI tags and descriptions to photos and documents (needs an API key)")
    tg.add_argument("--model", default=None, help="Claude model (default: claude-opus-5-5)")
    tg.add_argument("--limit", type=int, default=0, help="tag at most this many files this run")
    tg.add_argument("--only", help="only files under this library folder, e.g. images/photos")
    tg.add_argument("--retag", action="store_true", help="also redo files that already have tags")
    tg.add_argument("--workers", type=int, default=4)
    tg.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    dc = sub.add_parser("doctor", help="check drives, hardlinks, free space and setup")
    dc.add_argument("--source")
    dc.add_argument("--library")
    dc.add_argument("--inbox")

    sv = sub.add_parser("service", help="sort the inbox automatically in the background (macOS, Linux)")
    sv.add_argument("action", choices=["on", "off", "status", "refresh"],
                    help="refresh: make a job that is on use this Python (setup does this)")
    sv.add_argument("--inbox", help="the inbox folder (needed for 'on')")
    sv.add_argument("--library", help="default: the library from the last dry run")
    sv.add_argument("--every", type=int, default=300, help="also check every N seconds (default 300)")
    sv.add_argument("--quiet", action="store_true", help="no desktop notification after files are filed")

    un = sub.add_parser("undo", help="take placed files back out of the library; the old files are untouched")
    un.add_argument("--dry-run", action="store_true", help="only show what would be removed")
    un.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    rl = sub.add_parser("relocate", help="point the catalog at the drive's new location (e.g. after moving to Ubuntu)")
    rl.add_argument("--source", help="where the old files are now")
    rl.add_argument("--library", help="where the library is now")
    rl.add_argument("--catalog-from", help="start from the catalog copy in <library>/_meta/catalog.db")

    fd = sub.add_parser("find", help="search the library")
    fd.add_argument("words", nargs="*", help="words in the name, folder, tags, description or camera")
    fd.add_argument("--type", help="category, e.g. images or documents")
    fd.add_argument("--tag")
    fd.add_argument("--year")
    fd.add_argument("--limit", type=int, default=50)
    fd.add_argument("--json", action="store_true", help="print JSON instead of paths")

    st = sub.add_parser("stats", help="what is in the library")
    st.add_argument("--json", action="store_true")

    du = sub.add_parser("duplicates", help="list identical copies found in the old files (read-only)")
    du.add_argument("--csv", help="write every group to this CSV file")
    du.add_argument("--top", type=int, default=15, help="groups to show")

    ex = sub.add_parser("export", help="copy the files that match a search to another folder (e.g. to share)")
    ex.add_argument("dest", help="folder to copy into; it is created if needed")
    ex.add_argument("words", nargs="*")
    ex.add_argument("--type")
    ex.add_argument("--tag")
    ex.add_argument("--year")
    ex.add_argument("--flat", action="store_true", help="put every file directly in DEST instead of keeping folders")
    ex.add_argument("--dry-run", action="store_true")

    fx = sub.add_parser("fixity", help="check the library for silent damage")
    fx.add_argument("--minutes", type=float, default=0, help="stop after this long; the next run carries on")
    fx.add_argument("--limit", type=int, default=0, help="read at most this many files")
    fx.add_argument("--library", help="default: the library in the catalog")
    fx.add_argument("--notify", action="store_true", help="show a desktop notification if damage is found")

    args = p.parse_args(argv)
    if args.db and os.path.dirname(os.path.abspath(args.db)):
        folder = os.path.dirname(os.path.abspath(args.db))
        if folder == os.path.join(os.path.expanduser("~"), ".pettagam"):
            from .safeio import private_dir
            private_dir(folder)
        else:
            os.makedirs(folder, exist_ok=True)
    if args.cmd in ("inbox", "fixity") and not sys.stdout.isatty():
        from .service import rotate_log  # run by the background job, which appends to its log
        rotate_log()
    if args.cmd == "relocate":
        from .maintenance import relocate
        if not (args.source or args.library or args.catalog_from):
            raise SystemExit("Give --source, --library and/or --catalog-from.")
        relocate(args.db, args.source, args.library, args.catalog_from)
        return 0
    if args.cmd == "panel":
        from .panel import serve
        serve(args.host, args.port, open_browser=not args.no_browser)
        return 0
    con = connect(args.db)
    try:
        code = _run(con, args)
    except RuleError as e:
        print(f"Problem in your rules: {e}", file=sys.stderr)
        return 3
    if args.cmd in ("apply", "inbox", "tag", "undo") and not getattr(args, "dry_run", False):
        from .maintenance import backup_catalog
        try:
            backup_catalog(con)
        except (OSError, sqlite3.Error) as e:
            print(f"Note: could not save the catalog copy in the library: {e}", file=sys.stderr)
    return code


def _library(con, args) -> str:
    lib = getattr(args, "library", None) or get_meta(con, "library")
    if not lib:
        raise SystemExit("Say where the library is with --library (or run a dry run first).")
    return lib


def _rules_path(con, args):
    return getattr(args, "rules", None) or default_path(getattr(args, "library", None) or get_meta(con, "library"))


def _run(con, args) -> int:
    if args.cmd in ("scan", "dryrun"):
        scan(con, args.source, workers=args.workers)
        if not getattr(args, "no_dupes", False):
            find_duplicates(con, workers=args.workers)
    if args.cmd in ("plan", "dryrun"):
        rules = load_rules(args.rules or default_path(args.library))
        if rules:
            print(f"Using {len(rules)} of your rules")
        plan(con, args.library, split_at=args.split_at, rules=rules)
        report(con, args.out)
    if args.cmd == "apply":
        n = con.execute("SELECT COUNT(*) FROM files WHERE action='place' AND (status IS NULL OR status='error')").fetchone()[0]
        if not args.yes and input(f"Place {n:,} files into the library? The source is not changed. [y/N] ").lower() != "y":
            print("Cancelled.")
            return 1
        res = apply(con, workers=args.workers, method=args.method)
        return 1 if res["errors"] else 0
    if args.cmd == "verify":
        res = verify(con, sample=-1 if args.full else args.sample, rescan=args.rescan)
        for k, items in res["problems"].items():
            for item in items[:10]:
                print(f"  {k}: {item}")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(res, f, indent=2)
        return 0 if res["ok"] else 2
    if args.cmd == "rules":
        path = _rules_path(con, args)
        if not path:
            raise SystemExit("Say where the library is with --library, or give the file with --rules.")
        if args.init:
            print(("Created " if write_starter(path) else "Already exists: ") + path)
            print("Open it in a text editor, change the examples, and remove \"off\": true to switch one on.")
            return 0
        if not os.path.exists(path) and not args.explain:
            print(f"No rules file yet at {path}. Create a starter one with: pettagam rules --init")
            return 0
        rules = load_rules(path)
        if args.explain:
            for f in args.explain:
                try:
                    e = explain(f, rules)
                except OSError as err:
                    print(f"{f}: {err.strerror or err}")
                    continue
                if e["junk"]:
                    print(f"{f}\n  skipped as junk: {e['junk']}")
                    continue
                why = f"your rule '{e['rule']}'" if e["rule"] else f"smart type: {e['reason']}"
                print(f"{f}\n  -> {e['folder']}/  ({why})")
                print(f"  sees: {e['category']}/{e['subtype']}, .{e['ext']}, {e['size']:,} bytes, "
                      f"{e['date']} ({e['date_from']})" + (f", camera {e['camera']}" if e["camera"] else ""))
            return 0
        print(f"{path}: {len(rules)} rules switched on, all valid")
        counts = rule_counts(con, rules)
        for rl in rules:
            print(f"  {rl.name}: {counts[rl.name]:,} files -> {rl.then}/")
        return 0
    if args.cmd == "inbox":
        from .inbox import sort_inbox, watch
        lib = _library(con, args)
        path = _rules_path(con, args)
        if args.watch:
            watch(con, args.inbox, lib, path, every=args.watch, settle=args.settle, load=load_rules)
            return 0
        if not sys.stdout.isatty():
            print(time.strftime("%Y-%m-%d %H:%M:%S") + " sorting the inbox")
        res = sort_inbox(con, args.inbox, lib, load_rules(path), settle=args.settle, dry_run=args.dry_run)
        if args.notify and not args.dry_run and not res.get("busy"):
            from .notify import inbox_message, notify
            text = inbox_message(res)
            if text:
                notify("Pettagam", text)
        return 1 if res["error"] else 0
    if args.cmd == "views":
        from .views import build_views, write_starter as views_starter
        lib = _library(con, args)
        if args.init:
            print("Views file: " + views_starter(lib))
            return 0
        build_views(con, lib)
        return 0
    if args.cmd == "tag":
        from .tag import MODEL, estimate, tag_library
        est = estimate(con, args.retag, args.only)
        n = min(est["files"], args.limit) if args.limit else est["files"]
        if not n:
            print("Nothing to tag: every photo and document already has tags.")
            return 0
        model = args.model or MODEL
        cost = est["usd"] * n / est["files"]
        note = f" (roughly ${cost:,.2f} on {MODEL})" if model == MODEL else ""
        if not args.yes and input(f"Send {n:,} files to Claude ({model}) for tagging{note}? [y/N] ").lower() != "y":
            print("Cancelled.")
            return 1
        res = tag_library(con, _library(con, args), workers=args.workers, limit=args.limit,
                          retag=args.retag, only=args.only, model=model)
        return 1 if res["errors"] else 0
    if args.cmd == "doctor":
        from .doctor import print_checks, run_checks
        svc = None
        try:
            from .service import status
            svc = status().get("inbox")
        except Exception:
            pass
        return print_checks(run_checks(con, args.source, args.library, args.inbox or svc, args.db))
    if args.cmd == "service":
        from . import service
        if args.action == "status":
            print(service.status()["text"])
            log = service.last_log()
            if log:
                print("Last lines of the log:\n" + log)
            return 0
        if args.action == "off":
            print("Automatic inbox sorting stopped." if service.uninstall() else "It was not on.")
            return 0
        if args.action == "refresh":
            st = service.refresh()
            print(f"Automatic inbox sorting now uses {st['python']}." if st else "It is not on; nothing to refresh.")
            return 0
        if not args.inbox:
            raise SystemExit("Say which folder is the inbox: pettagam service on --inbox FOLDER")
        st = service.install(args.db, args.inbox, _library(con, args), every=args.every, notify=not args.quiet)
        print(service.status()["text"] + ("" if st["started"] else
              " (written, but it could not be started now; it starts at your next login)"))
        print(f"Log: {st['log']}")
        return 0
    if args.cmd == "undo":
        from .maintenance import undo
        if not args.dry_run and not args.yes:
            preview = undo(con, dry_run=True, progress=lambda m: None)
            if input(f"Remove {preview['removed']:,} files from the library and return {preview['returned']:,} to "
                     "the inbox? The old files are not touched. [y/N] ").lower() != "y":
                print("Cancelled.")
                return 1
        undo(con, dry_run=args.dry_run)
        return 0
    if args.cmd == "find":
        from .library import search
        res = search(con, " ".join(args.words), args.type or "", args.tag or "", args.year or "", limit=args.limit)
        lib = get_meta(con, "library") or ""
        if args.json:
            print(json.dumps(res, indent=2, default=str))
            return 0
        for it in res["items"]:
            extra = f"  [{it['tags']}]" if it["tags"] else ""
            print(os.path.join(lib, it["dest"]) + extra)
        more = f" (showing {len(res['items'])}; use --limit)" if res["total"] > len(res["items"]) else ""
        print(f"{res['total']:,} found{more}", file=sys.stderr)
        return 0 if res["total"] else 1
    if args.cmd == "stats":
        from .library import print_stats, stats
        res = stats(con)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print_stats(res)
        return 0
    if args.cmd == "duplicates":
        from .library import duplicate_groups, write_duplicates_csv
        groups = duplicate_groups(con)
        if not groups:
            print("No duplicates found (or duplicate detection was skipped during the scan).")
            return 0
        wasted = sum(g["wasted"] for g in groups)
        print(f"{len(groups):,} sets of identical files; the extra copies take {wasted / 1e9:,.2f} GB. "
              "Only one copy of each went into the library. Nothing was deleted.")
        for g in groups[:args.top]:
            print(f"\n  {g['size'] / 1e6:,.1f} MB x {len(g['copies']) + 1}: keep {g['keep']}")
            for c in g["copies"][:5]:
                print(f"      same as: {c}")
            if len(g["copies"]) > 5:
                print(f"      ... and {len(g['copies']) - 5} more")
        if args.csv:
            write_duplicates_csv(con, args.csv)
            print(f"\nAll groups written to {args.csv}")
        return 0
    if args.cmd == "export":
        from .library import export
        if not (args.words or args.type or args.tag or args.year):
            raise SystemExit("Say what to export, e.g. pettagam export ~/Desktop/beach --tag beach")
        res = export(con, args.dest, " ".join(args.words), args.type or "", args.tag or "", args.year or "",
                     flat=args.flat, dry_run=args.dry_run)
        return 1 if res["errors"] else 0
    if args.cmd == "fixity":
        from .fixity import check
        res = check(con, args.library, minutes=args.minutes, limit=args.limit)
        if args.notify and res["damaged"]:
            from .notify import notify
            notify("Pettagam: damaged files", f"{len(res['damaged'])} library files changed without being edited. "
                   "The drive may be failing: run pettagam fixity to see which.")
        return 2 if res["damaged"] else (1 if res["missing"] or res["unreadable"] else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
