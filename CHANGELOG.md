# Changelog

All notable changes to Pettagam. Versions follow [semantic versioning](https://semver.org/); dates are UTC.

## [Unreleased]

## [0.7.0] - 2026-10-24

Easier to use from start to finish.

### Added
- **The panel says what to do next.** A line at the top of the Organize tab always names the one next step: choose folders, start the dry run, look over the plan and place the files, place the rest, check everything is in sync, or all done.
- **Percent done and time left** for long steps, in the panel (with a progress bar, and in the browser tab's title) and on the command line. A dry run of a drive scanned before expects as many files as last time, so even the scan shows a percentage.
- **Stop** button for the running step (Ctrl+C on the command line). Placing and AI tagging keep what they finished and carry on next time; tagging sends nothing more once stopped.
- **The Mac stays awake** while a step runs, and a step that took more than a minute ends with a desktop notification, so a 50-minute dry run can be left alone.
- **Updating without the Terminal**: when a new version is on GitHub the panel says so and **Update and restart** installs it. `Update Pettagam.command` and `pettagam update` do the same and restart a running panel. After an update by any other means (git pull, GitHub Desktop) the panel offers **Restart now**, so it no longer needs restarting by hand.
- `pettagam` on its own shows how to start and where the catalog has got to; **`pettagam status`** names the next step. Dry run, apply and verify each end by naming the next step.
- **When something goes wrong** section in the README, with what to do about each common problem.

### Changed
- `pettagam --help` lists the commands in groups, in the order they're used, and a mistyped command gets a suggestion ("Did you mean: pettagam apply").
- Problems are explained in plain words with what to do (drive unplugged, disk full, macOS refusing access, catalog busy, drive failing) instead of a Python error. Anything unexpected is called a bug, with its details saved in `~/.pettagam/last-error.txt` for reporting.
- Opening Pettagam while the panel is already running opens the running panel instead of failing with "address already in use".
- Planning, placing, the sync check and the damage check report progress at least every 10 seconds.

### Fixed
- `pettagam apply` with nothing planned asked "Place 0 files?"; it now says to run a dry run first (or that everything is already placed).
- `pettagam verify` before any dry run said "everything is in sync"; it now says there is nothing to check yet.
- A question asked with nobody to answer (from a script) crashed with `EOFError`; it now counts as no and says to use `--yes`.

## [0.6.3] - 2026-10-23

### Fixed
- **A long dry run in the panel failed at the end with `unable to open database file`.** Each page refresh left two catalog files open until Python's garbage collector got round to them, which in a quiet panel can be hours; over a 50-minute scan the panel reached macOS's limit of 256 open files. Every request and step now closes its catalog connections when it finishes, and the panel raises its open-file limit when it starts. Run the dry run again after updating.

## [0.6.2] - 2026-10-23

### Changed
- **The panel shows what is happening right now.** The status line names the running step, how long it has run and its latest progress (for example "Running: dry run, 4 min · scanned 38,000 files (160/s), 212 folders still to read"), and says when the last step failed. A scan reports at least every 10 seconds instead of every 20,000 files, which on a slow drive could mean minutes of silence, and the duplicate check reports how many files it has sampled and hashed. While a dry run runs, the numbers at the top are marked as being rebuilt.
- *Measure speed* is named in the status line (it showed "undefined").

## [0.6.1] - 2026-10-23

### Fixed
- **Scanning an exFAT drive on a recent macOS stopped at once** with `OverflowError: Python int too large to convert to SQLite INTEGER`. macOS's new FSKit exFAT driver gives some files inode numbers just under 2^64, which SQLite can't store as they are; Pettagam now stores them wrapped into SQLite's range.
- A scan that failed part-way could keep the catalog locked, so the next step (for example Measure speed) failed with `database is locked` after a minute. A failed scan now lets go of the catalog at once.

## [0.6.0] - 2026-10-23

### Added
- **Browse the library by folder** in Explore: the folders inside the current one, with how many files each holds, and a trail of links back to the top. Searching and filters then look only inside that folder. Back and reloading keep your place, so a folder can be bookmarked. From the command line: `pettagam folders [FOLDER]`, and `find --folder` / `export --folder`.
- **Sort** Explore and `pettagam find --sort` by newest, oldest, name, largest or smallest. Explore remembers your choice.
- **List view**: switch Explore between the picture grid and a list with name, folder, date, size and tags. Click a folder in the list to go there.
- **Choose many files and tag them at once**: tick files (Shift-click for a run, or Select all shown, or every file the search finds), then add or remove tags on all of them.
- **Keyboard**: `/` jumps to search, the arrow keys move between files, Enter opens one, `x` chooses it. In a file's details, ← and → (or Previous and Next) step through the results without closing it.
- File details preview audio and the start of text files (notes, CSV, JSON...), show a picture's size in pixels, and have **Show its folder** and, on the computer running the panel, **Show in Finder**.
- **Faster thumbnails on the Mac**: the grid shows small JPEGs made once with macOS's own `sips`, instead of loading every full-size photo off the drive. HEIC and TIFF photos now show in every browser. They are kept in `~/.pettagam/thumbs/` (at most 1 GB, oldest dropped first).
- More results load by themselves as you scroll.
- `pettagam bench` also times sorting and folder browsing.

### Changed
- The catalog is now version 3: opening it adds indexes by folder, size and name (a copy of the catalog is kept first, as before). Over a million files, the top folders list in about 0.13 s and sorting by name or size takes about 0.02 s.

## [0.5.0] - 2026-10-22

### Added
- **Tags by hand, no AI needed**: type tags and a description in Explore's file details and press Save tags, or use `pettagam tags FILE --add grandma,goa --remove family` (`--set` replaces them all, `--description` the description). `pettagam tags --match "goa 2019" --add holiday` tags every file a search finds, after asking. Search sees the change at once; run Rebuild views to update the by-tag folders.
- **On this day**: a button in Explore library, and `pettagam find --on-this-day` (or `--day 12-25`), shows the files taken on today's date in every earlier year. Over a million files it answers in about 0.4 s.
- A file's details in Explore list the identical copies of it that stayed in the old files, so you can see where else it was before deciding to tidy them up.

## [0.4.0] - 2026-10-22

### Breaking
- **Python 3.13 or newer is now required** (it was 3.9). On the Mac, install Python from python.org or Homebrew; on Ubuntu 24.04, from the deadsnakes PPA. An older Python stops with a message saying what to install. Run Setup Pettagam (or `launchers/setup-ubuntu.sh`) again after updating: Pettagam now always runs from its own `.venv` beside the code, so the app, the `pettagam` command and the background job use the same Python. Setup finds Python where python.org and Homebrew install it even when Terminal's `python3` is still Apple's 3.9. Setup also moves a background inbox job that was set up with the old Python onto the new one (`pettagam service refresh`), and the health check flags one that wasn't.

### Added
- **Word search uses a full-text index**: searching a million files for a word takes milliseconds instead of about a second. It finds the same files as before (any part of a word of 3 or more letters, ignoring case, now in every language). Existing catalogs build the index once, on first open; that takes a minute or so for a couple of million files. The health check says if your Python's SQLite can't do this (then search still works, more slowly).
- **HTTPS for the panel on the network** (`pettagam panel --host 0.0.0.0 --tls`): with a certificate Pettagam makes for this computer once (it prints the fingerprint to check), or your own with `--tls-cert`/`--tls-key`. Plain network mode now warns that it isn't encrypted.
- `pettagam move-plan --to /srv/ninaivu --host you@server` writes out every step and command for moving the library (and the old files, keeping hardlinks) to the Ubuntu server, optionally as two scripts. It copies nothing itself.
- **Measure speed** button in the panel's health check runs the same timings on your folders.
- `pettagam bench` measures scanning, reading, hardlink placing and Explore search speed on your drive against the targets in the requirements, without changing anything.
- An MIT licence (`LICENSE`), declared in the package metadata.
- **Requirements specification** ([docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)): user stories with acceptance criteria, functional, data-safety, security, privacy, performance and platform requirements, each traced to the code and the test that checks it, plus the audit's findings and open items. `tests/test_requirements.py` fails if the document names a test that doesn't exist.
- `pettagam verify --full` checksums every copied file instead of a sample.
- The health check shows the library drive's format, warns when it can't make hardlinks, and notes that Ubuntu can't write APFS.
- `SECURITY.md`.

### Changed
- The panel works with the keyboard and screen readers: folders, library tiles and tags are real buttons, fields have names, focus is always visible, the tabs follow the arrow keys, results are announced, and a few light-mode colours were darkened to be easier to read (WCAG 2.2 AA).
- Explore opens much faster on big libraries: the newest files come from an index instead of sorting everything (a million files: 2.3 s before, under 0.05 s now), and searches read the placed and inbox files separately.
- The catalog has a version number. Opening an older catalog keeps a copy of it (`pettagam.db.v0.bak`) and then upgrades it; a catalog from a newer Pettagam is refused unchanged instead of being half-understood (this also protects `relocate` from a newer drive copy). The upgrade adds an index that makes `pettagam duplicates` much faster on big collections.
- CI lints the code with `ruff` and checks that the shell scripts parse; the code uses Python 3.13 syntax throughout.
- Copies (placing across drives, inbox, undo, export) are flushed to the disk and checked before they get their name, and an inbox file is removed only after that. Moves and copies never replace an existing file, even one that appears at the last moment.
- Placing, inbox sorting and undo share one lock per library, so they never run at the same time (the lock file is now `_meta/library.lock`).
- The panel only answers requests addressed to this computer (stops DNS-rebinding attacks from websites), accepts only JSON up to 2 MB, checks settings, and sends security headers. Library files it shows run in a sandbox, so an HTML or SVG file with scripts can't act as the panel.
- `~/.pettagam/` is created readable by you only. The background log is rotated past 5 MB.
- Packaging: the version is defined once in `pettagam/__init__.py`; `pyproject.toml` has a readme, classifiers and project links, version ranges for the optional extras (`xxhash>=3,<5`, `anthropic>=1,<3`), an `all` extra, and `build` in `dev`. CI adds Python 3.13, a run with the extras, and a check that the built wheel installs and includes the panel.
- README: the SSH tunnel is the recommended way to use the panel from another computer, and moving to Ubuntu explains copying from APFS to ext4 with hardlinks kept.

### Fixed
- A brand-new catalog opened by two parts of the panel at the same moment could fail with "database is locked". Creating and upgrading the catalog now waits its turn.

## [0.3.0] - 2026-10-21

### Added
- **Health check** (`pettagam doctor`, and a card in the panel): drives reachable, same drive and working hardlinks, free space against what copying needs, catalog location, inbox, background job and AI-tagging readiness, each with what to do.
- **Automatic inbox sorting in the background** (`pettagam service on|off|status`, and a switch in the panel): a macOS LaunchAgent or a systemd user timer and path watch runs an inbox pass when files arrive and every few minutes. Logged to `~/.pettagam/inbox-service.log`.
- **Undo** (`pettagam undo [--dry-run]`, and a button in the panel): takes placed files back out of the library and returns inbox files to the inbox, never removing the only copy of anything.
- **Catalog copy in the library** (`<library>/_meta/catalog.db`), refreshed after placing, inbox passes, tagging and undo.
- **Relocate** (`pettagam relocate --catalog-from ... --source ... --library ...`) for when the drive is mounted somewhere else, such as after the move to Ubuntu.
- **Setup Pettagam.command** builds a Pettagam app with an icon in `~/Applications` on the Mac itself, so Gatekeeper doesn't block it, with optional AI tagging in a `.venv`. **Uninstall Pettagam.command** removes the app, background job and `.venv`, asks before removing settings, and never touches your files. `launchers/setup-ubuntu.sh` adds a `pettagam` command and menu entry on Ubuntu.
- **Find** (`pettagam find`), **stats** (`pettagam stats`, and a summary line in Explore library) and **duplicates** (`pettagam duplicates [--csv]`) for the finished library and the old files.
- **Rules editor** in the panel (Your rules: check, count matches, save only when valid) and `pettagam rules --explain FILE` to see where a file would go and why.
- **Export** (`pettagam export DEST --tag beach`): copies the files matching a search to another folder, never overwriting.
- **Notifications**: background inbox sorting shows a desktop notification when files were filed (`service on --quiet` turns it off).
- **Damage check** (`pettagam fixity [--minutes N]`, and a button in the panel): fingerprints every library file and later tells silent damage (bit rot) apart from edits. Shown in the health check. With background sorting on, a 30-minute slice runs every Sunday night and notifies you about damage.
- A release workflow: pushing a `v*` tag tests, builds and publishes a GitHub release from this changelog.

### Changed
- The health check card no longer widens the panel on a phone when paths are long.
- The command line uses the panel's catalog (`~/.pettagam/pettagam.db`) unless `PETTAGAM_DB`, `--db` or a `./pettagam.db` says otherwise, so both see the same library.
- Inbox passes take a lock, so the panel, a Terminal and the background job never sort at the same time.
- The launchers use the `.venv` Python when it exists.

## [0.2.0] - 2026-10-21

### Added
- **Your own rules** in `<library>/_meta/rules.json`: conditions (type, name and path wildcards, regex, camera, size, age, year, confidence, AI tags, any/all/not) and folder templates (`{year}`, `{month}`, `{category}`, `{subtype}`, `{ext}`, `{camera}`). `pettagam rules [--init]` checks them and counts matches.
- **Inbox sorting** (`pettagam inbox [--watch N] [--dry-run]`): new files are filed into the library; exact copies go to `_duplicates/`; nothing is overwritten or deleted.
- **Automatic views** (`pettagam views`): by year, camera, recent, large files and tag, as hardlinks under `views/`.
- **AI tagging** (`pettagam tag`, optional): Claude adds tags and a one-line description to photos, PDFs and text files.
- **Explore library** tab in the panel: search, filters, tag cloud, thumbnails and previews.
- The scanner reads the EXIF capture date.

## [0.1.0] - 2026-10-20

### Added
- Read-only scanner, smart-type classification, duplicate detection, planner with folder splitting, placement by hardlink or copy, sync verification, dry-run report and `plan.csv`, manifest.
- The point-and-click panel (`pettagam panel`) and double-click launchers.

[Unreleased]: https://github.com/javajaga-usa/Pettagam/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/javajaga-usa/Pettagam/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/javajaga-usa/Pettagam/releases/tag/v0.4.0
[0.3.0]: https://github.com/javajaga-usa/Pettagam/releases/tag/v0.3.0
[0.2.0]: https://github.com/javajaga-usa/Pettagam/releases/tag/v0.2.0
[0.1.0]: https://github.com/javajaga-usa/Pettagam/releases/tag/v0.1.0
