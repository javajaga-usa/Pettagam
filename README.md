# Pettagam (பெட்டகம்)

Pettagam turns a messy, deeply nested folder into a clean library organized by what each file actually is: `images/photos`, `images/screenshots`, `documents/invoices`, `audio/voice` and so on.

- **The originals are never changed.** Pettagam only reads the old folder.
- **Placement is fast.** When the library is on the same drive as the old files, each file is hardlinked. That's instant, uses no extra space, and still leaves the original where it was. On a different drive, files are copied instead.
- **You see everything before it happens.** A dry run writes a report plus a spreadsheet of every file's planned location.
- **It proves the result.** `verify` checks that every file made it and matches its original.

This is milestone 1: the scanner, smart-type rules, planner, placement and sync checks. The web explorer, automatic views, inbox sorting and AI tagging come in later milestones.

## Requirements

- Python 3.9 or newer. It's already on macOS (run `python3 --version`; if asked, install the Command Line Tools) and on Ubuntu.
- No other packages are needed. For faster duplicate checks on big libraries you can optionally run `python3 -m pip install xxhash`.

## Install

```
cd pettagam
python3 -m pip install --user .
```

You now have a `pettagam` command. If your shell can't find it, run `python3 -m pettagam` from this folder instead.

## Easy launcher (panel)

Prefer buttons to typing? Double-click **`Pettagam.command`** in Finder (or run `python3 -m pettagam.gui`, or `pettagam-panel` after installing). A small window opens where you choose the old files folder and the new library, then press:

1. **Dry run** – scans and plans, changes nothing, then opens the report.
2. **Open report** – opens `report.md` and `plan.csv`.
3. **Place files** – asks first, then hardlinks or copies files into the library.
4. **Verify** – checks everything is in sync.

The panel remembers your folders (in `~/.pettagam-gui.json`) and shows the same output as the command line. **Stop** is safe: run the step again to continue. The first time you double-click, macOS may ask you to confirm opening it (right-click → Open).

## Use it

Replace the paths with yours. On the Mac, the N300 Pro appears under `/Volumes/`. Keep the catalog (`--db`) and the report on the Mac's internal SSD, and put the library on the **same drive** as the old files so hardlinks can be used.

**1. Dry run (changes nothing):**

```
pettagam --db ~/pettagam.db dryrun "/Volumes/N300/Old files" \
    --library "/Volumes/N300/Pettagam" --out ~/pettagam-report
```

**2. Review.** Open `~/pettagam-report/report.md` for the summary. Open `plan.csv` in Numbers or Excel to see every file, where it will go, and why. Files with weak clues are planned into `review/` instead of being guessed. If something looks wrong, tell Claude and the rules will be adjusted. Then repeat step 1.

**3. Place the files:**

```
pettagam --db ~/pettagam.db apply --workers 2
```

You can stop it and run it again at any time: finished files are skipped. It never overwrites a file that's already in the library.

**4. Check everything is in sync:**

```
pettagam --db ~/pettagam.db verify --rescan
```

This confirms three things:
- The source total equals placed + duplicates + junk.
- Every placed file is identical to its original (same inode for hardlinks; size, date and sampled checksums for copies).
- Nothing in the old folder has changed since the scan.

It exits with code 0 when everything matches.

The manifest at `<library>/_meta/manifest.csv` records every original path and what happened to it.

## How files are placed

| Rule | Example |
| --- | --- |
| Category, then smart type, no date folders | `images/photos/IMG_0412.jpg` |
| Original name kept, extension lowercased | `Photo.JPG` → `Photo.jpg` |
| Real type comes from the content when the extension is missing or wrong | `scan` (a PDF) → `scan.pdf` |
| Characters Windows can't open are replaced | `bad:name?.pdf` → `bad_name_.pdf` |
| Name clashes get a suffix (case-insensitive), so nothing is overwritten | `report.pdf`, `Report__2.pdf` |
| Exact duplicates are stored once; every path stays in the manifest | |
| Junk is skipped: `.DS_Store`, `._*`, `Thumbs.db`, Office lock files, empty files, `.Spotlight-V100` and similar | |
| Folders over 5,000 files split by first letter (`photos/a/`); when names share a prefix (`IMG_0001`…), they split into sorted ranges instead | `photos/i/img_0001.jpg--img_4999.jpg/` |
| Weak clues go to `review/<category>/` for a person to check | |

The classification rules are in `pettagam/classify.py`.

## Moving to Ubuntu later

Install Pettagam on Ubuntu the same way and plug in the drive. Because the paths change (`/srv/ninaivu/...` instead of `/Volumes/...`), start with a fresh `dryrun` against the new mount point, then `apply`. Files that are already placed are recognised (same inode) and left alone, so nothing is copied twice.

## Development

```
python3 -m pip install pytest
python3 -m pytest
```
