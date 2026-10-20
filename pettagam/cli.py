"""pettagam command line.

  pettagam scan SOURCE              read-only inventory (+ duplicate detection)
  pettagam plan --library LIB       decide every file's place; writes report.md + plan.csv
  pettagam apply                    hardlink or copy files into the library
  pettagam verify [--rescan]        check everything is in sync
  pettagam dryrun SOURCE --library LIB   scan + plan + report in one go (changes nothing)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .apply import apply
from .db import connect
from .hashing import find_duplicates
from .plan import SPLIT_AT, plan
from .report import report
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

    d = sub.add_parser("dryrun", help="scan + plan + report; changes nothing on disk")
    d.add_argument("source")
    d.add_argument("--library", required=True)
    d.add_argument("--workers", type=int, default=2)
    d.add_argument("--split-at", type=int, default=SPLIT_AT)
    d.add_argument("--out", default="pettagam-report")

    a = sub.add_parser("apply", help="place files into the library (source is never modified)")
    a.add_argument("--workers", type=int, default=4, help="parallel copies; 2-4 for hard drives")
    a.add_argument("--method", choices=["auto", "link", "copy"], default="auto")
    a.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    v = sub.add_parser("verify", help="check every file made it and matches")
    v.add_argument("--sample", type=int, default=200, help="copies to checksum in full")
    v.add_argument("--rescan", action="store_true", help="also look for changes in the source since the scan")
    v.add_argument("--json", help="write full results to this file")

    args = p.parse_args(argv)
    con = connect(args.db)

    if args.cmd in ("scan", "dryrun"):
        scan(con, args.source, workers=args.workers)
        if not getattr(args, "no_dupes", False):
            find_duplicates(con, workers=args.workers)
    if args.cmd in ("plan", "dryrun"):
        plan(con, args.library, split_at=args.split_at)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
