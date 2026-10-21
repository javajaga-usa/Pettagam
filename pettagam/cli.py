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
  pettagam service on|off|status    sort the inbox automatically in the background
  pettagam undo                     take placed files back out of the library (old files untouched)
  pettagam relocate                 follow the files after the drive is mounted somewhere else
"""
from __future__ import annotations

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
from .plan import SPLIT_AT, decide, plan
from .report import report
from .rules import RuleError, default_path, load_rules, write_starter
from .scan import scan
from .verify import verify


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pettagam", description="Organize a messy file tree into a smart library.")
    p.add_argument("--version", action="version", version=f"pettagam {__version__}")
    p.add_argument("--db", default="pettagam.db", help="catalog file; keep it on an SSD (default: ./pettagam.db)")
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

    ib = sub.add_parser("inbox", help="file new files from an inbox folder into the library")
    ib.add_argument("inbox")
    ib.add_argument("--library", help="default: the library from the last dry run")
    ib.add_argument("--rules", help="default: <library>/_meta/rules.json")
    ib.add_argument("--watch", type=float, metavar="SECONDS", help="keep running, checking every SECONDS")
    ib.add_argument("--settle", type=float, default=10, help="leave files changed in the last N seconds (default 10)")
    ib.add_argument("--dry-run", action="store_true", help="only show where files would go")

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
    sv.add_argument("action", choices=["on", "off", "status"])
    sv.add_argument("--inbox", help="the inbox folder (needed for 'on')")
    sv.add_argument("--library", help="default: the library from the last dry run")
    sv.add_argument("--every", type=int, default=300, help="also check every N seconds (default 300)")

    un = sub.add_parser("undo", help="take placed files back out of the library; the old files are untouched")
    un.add_argument("--dry-run", action="store_true", help="only show what would be removed")
    un.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    rl = sub.add_parser("relocate", help="point the catalog at the drive's new location (e.g. after moving to Ubuntu)")
    rl.add_argument("--source", help="where the old files are now")
    rl.add_argument("--library", help="where the library is now")
    rl.add_argument("--catalog-from", help="start from the catalog copy in <library>/_meta/catalog.db")

    args = p.parse_args(argv)
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
        res = verify(con, sample=args.sample, rescan=args.rescan)
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
        if not os.path.exists(path):
            print(f"No rules file yet at {path}. Create a starter one with: pettagam rules --init")
            return 0
        rules = load_rules(path)
        print(f"{path}: {len(rules)} rules switched on, all valid")
        counts = {rl.name: 0 for rl in rules}
        for row in con.execute("SELECT * FROM files WHERE junk IS NULL AND dup_of IS NULL"):
            _, _, name = decide(row, rules)
            if name:
                counts[name] += 1
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
        if not args.inbox:
            raise SystemExit("Say which folder is the inbox: pettagam service on --inbox FOLDER")
        st = service.install(args.db, args.inbox, _library(con, args), every=args.every)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
