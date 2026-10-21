# Pettagam (பெட்டகம்)

Pettagam turns a messy, deeply nested folder into a clean library organized by what each file actually is: `images/photos`, `images/screenshots`, `documents/invoices`, `audio/voice` and so on.

- **The originals are never changed.** Pettagam only reads the old folder.
- **Placement is fast.** When the library is on the same drive as the old files, each file is hardlinked. That's instant, uses no extra space, and still leaves the original where it was. On a different drive, files are copied instead.
- **You see everything before it happens.** A dry run writes a report plus a spreadsheet of every file's planned location.
- **It proves the result.** `verify` checks that every file made it and matches its original.

After the one-time cleanup it keeps the library tidy:

- **Your own rules** decide where files go when the smart types aren't what you want ("PDFs with *tax* in the name go to `documents/tax/<year>`").
- **Inbox sorting** files anything you drop into an inbox folder, using your rules and the smart types.
- **Automatic views** show the same files by year, by camera, recent, large and by tag, as hardlinks that take no extra space.
- **AI tagging** (optional) gives photos and documents descriptive tags and a one-line description using Claude.
- **The library explorer** in the panel lets you search and browse everything, with thumbnails.

## Requirements

- Python 3.9 or newer. It's already on macOS (run `python3 --version`; if asked, install the Command Line Tools) and on Ubuntu.
- No other packages are needed. For faster duplicate checks on big libraries you can optionally run `python3 -m pip install xxhash`.
- AI tagging only: `python3 -m pip install anthropic` and an Anthropic API key (see [AI tagging](#ai-tagging)).

## Install

**On the Mac (no typing):** double-click **`Setup Pettagam.command`** in this folder. It finds Python, offers to set up AI tagging, and puts a **Pettagam** app in your Applications folder, built on your Mac so macOS doesn't block it. From then on, open Pettagam from Launchpad or Spotlight. (If macOS refuses to open the setup file itself the first time, open **System Settings → Privacy & Security** and press **Open Anyway** next to it.)

**On Ubuntu:** run `./launchers/setup-ubuntu.sh` (add `--ai` for AI tagging). It adds a `pettagam` command, and a menu entry on a desktop.

**Or with pip, anywhere:**

```
cd pettagam
python3 -m pip install --user .
```

You now have a `pettagam` command. If your shell can't find it, run `python3 -m pettagam` from this folder instead.

To remove Pettagam, double-click **`Uninstall Pettagam.command`** (it also works on Linux: `bash "Uninstall Pettagam.command"`). It removes the app, the background inbox job and the AI-tagging libraries, and asks before removing your settings and catalog. Your old files, the library and the inbox are never touched.

## The panel

Open the Pettagam app (or run `pettagam panel`, or double-click `launchers/Pettagam.command`). Your browser opens the panel. The **Organize** tab walks you through:

1. **Choose folders.** Pick the folder with your old files, then the place for the new library.
2. **Health check.** Shows at a glance whether the drive is reachable, whether placing can use hardlinks (instant, no extra space), and whether there's enough free space.
3. **Start dry run.** This scans and plans without changing anything. You see a count for every folder, can search any file to see where it will go and why, and can open the full report.
4. **Place files**, then **Check everything is in sync.** **Undo placement…** takes it all back out again if you change your mind.
5. **Keep it tidy:** choose an inbox folder and press **Sort the inbox now**, or tick **Sort the inbox automatically in the background**. **Rebuild views** and **Add AI tags…** are here too (tagging shows the number of files and a rough cost first).

The **Explore library** tab shows every file in the library, newest first, with thumbnails. Search by any word in the name, folder, tags or description, filter by kind and year, or click a tag. Click a file to see a preview, where it came from, its date and tags, and to open it.

The panel remembers your folders, shows live progress, and keeps its catalog in `~/.pettagam/` on your internal drive. To stop it, close the Terminal window that opened with it.

**On Ubuntu Server (no screen):** run `launchers/pettagam-panel.sh --host 0.0.0.0 --no-browser`, then open the link it prints from any computer on your network. In this mode the link carries a secret token, so only people with the link can use the panel.

## Use it from the command line

Replace the paths with yours. On the Mac, the N300 Pro appears under `/Volumes/`. Put the library on the **same drive** as the old files so hardlinks can be used.

The command line and the panel share one catalog, `~/.pettagam/pettagam.db`, on the computer's internal SSD. To use another one, set `PETTAGAM_DB` or pass `--db FILE`. A `pettagam.db` in the current folder is used if there is one, as in earlier versions.

**1. Dry run (changes nothing):**

```
pettagam dryrun "/Volumes/N300/Old files" \
    --library "/Volumes/N300/Pettagam" --out ~/pettagam-report
```

**2. Review.** Open `~/pettagam-report/report.md` for the summary. Open `plan.csv` in Numbers or Excel to see every file, where it will go, and why. Files with weak clues are planned into `review/` instead of being guessed. If something looks wrong, tell Claude and the rules will be adjusted. Then repeat step 1.

**3. Place the files:**

```
pettagam apply --workers 2
```

You can stop it and run it again at any time: finished files are skipped. It never overwrites a file that's already in the library.

**4. Check everything is in sync:**

```
pettagam verify --rescan
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

The built-in smart types are in `pettagam/classify.py`. To change where files go without editing code, write [your own rules](#your-own-rules).

## Your own rules

Rules live in `<library>/_meta/rules.json`. Create a starter file with examples (all switched off) and then check what your rules match:

```
pettagam rules --library "/Volumes/N300/Pettagam" --init
pettagam rules
```

Open the file in TextEdit, change an example and delete its `"off": true` line to switch it on. A rule has a name, conditions (`if`) and a folder (`then`):

```json
{"rules": [
  {"name": "Tax papers", "if": {"ext": "pdf", "name": ["*tax*", "*1099*"]}, "then": "documents/tax/{year}"},
  {"name": "iPhone photos", "if": {"subtype": "photos", "camera": "iphone"}, "then": "images/photos/iphone/{year}"},
  {"name": "WhatsApp", "if": {"any": [{"path": "*whatsapp*"}, {"name": "IMG-*-WA*"}]}, "then": "{category}/whatsapp"},
  {"name": "Big videos", "if": {"category": "videos", "size_over": "1GB"}, "then": "videos/large"}
]}
```

The first rule that matches a file wins; files that match none are placed by the smart types. Every condition in `if` must hold.

| Condition | Matches when | Example |
| --- | --- | --- |
| `category`, `subtype` | the smart type is one of these | `"category": ["images", "videos"]`, `"subtype": "screenshots"` |
| `ext`, `kind` | the extension (or the format found in the content) is one of these | `"ext": ["pdf", "docx"]` |
| `name`, `path` | the file name (or its path in the old folder or inbox) matches a wildcard; not case-sensitive | `"name": "*invoice*"`, `"path": "work/*"` |
| `name_regex` | the name matches a regular expression | `"name_regex": "^IMG_\\d{4}"` |
| `camera` | the camera name contains this text, or `true` for any camera | `"camera": "canon"` |
| `size_over`, `size_under` | bigger or smaller than a size | `"size_over": "500MB"` |
| `older_than`, `newer_than` | taken (or last changed) more or less than this long ago | `"older_than": "2y"`, `"newer_than": "30d"` |
| `year` | taken (or last changed) in this year or range | `"year": "2019-2021"` |
| `confidence_below` | the smart type was a weak guess (0 to 1) | `"confidence_below": 0.6` |
| `tags` | the file has one of these AI tags, or `true` for any | `"tags": ["passport", "visa"]` |
| `any`, `all`, `not` | combine conditions | `"not": {"subtype": "invoices"}` |

Folders can use `{year}`, `{month}`, `{category}`, `{subtype}`, `{ext}` and `{camera}`. The date is when the photo was taken if the camera recorded it, otherwise when the file was last changed.

Rules apply to every dry run (the report lists how many files each rule took) and to the inbox. To move files already placed, run the dry run and **Place files** again into a fresh library folder.

To see where a particular file would go, and why:

```
pettagam rules --explain "/Volumes/N300/Old files/Scans/tax 2023.pdf"
```

It prints the folder, the rule or smart type that chose it, and what Pettagam sees in the file: its type, size, date and camera. In the panel, open **Your rules** to edit the rules. **Check** validates them and counts the files each rule would place, and **Save** writes them only when they are valid.

## Inbox sorting

Make a folder such as `/Volumes/N300/Inbox` and drop new files into it (or point your phone backup or downloads there). Then:

```
pettagam inbox "/Volumes/N300/Inbox" --dry-run   # only show where files would go
pettagam inbox "/Volumes/N300/Inbox"             # file them
pettagam inbox "/Volumes/N300/Inbox" --watch 60  # keep running, checking every minute
```

- Unlike the old-files folder, **the inbox is emptied**: each file is moved into the library (instantly on the same drive; across drives it is copied, checked, and only then removed from the inbox).
- Exact copies of files already in the library are moved to `_duplicates/` inside the inbox. Nothing is ever deleted.
- Files still being copied or downloaded (changed in the last 10 seconds, or ending in `.crdownload`, `.part` and similar) are left for the next pass. Junk like `.DS_Store` is ignored.
- Names are never overwritten: a clash gets a `__2` suffix. Folders that were split by letter are followed.
- Every file filed is listed in `<library>/_meta/inbox-log.csv`. The inbox must be outside both the library and the old-files folder.

## Automatic views

```
pettagam views
```

This builds `<library>/views/` with `by-year/<year>/<month>/` (photos and videos), `by-camera/<camera>/`, `recent/` (last 30 days), `large-files/` (over 500 MB) and `by-tag/<tag>/`. Each entry is a hardlink to the file in the library, so views use no extra space, and deleting from a view never deletes the library file. Run it again after placing files, sorting the inbox or tagging; it removes the entries it made before and leaves anything else alone. (On a drive without hardlinks, such as exFAT, the entries are shortcuts instead.)

To change the views, write them to a file you can edit with `pettagam views --init` and edit `<library>/_meta/views.json`. Views use the same conditions as rules; a file appears in every view it matches, and `{tag}` makes one folder per tag.

## AI tagging

Optional. Claude looks at each photo, PDF and text file in the library and gives it 3 to 8 tags (beach, birthday, receipt, passport…) and a one-line description, which you can then search in **Explore library** and use in rules and views.

1. `python3 -m pip install anthropic`
2. Get an API key at <https://console.anthropic.com> and run `export ANTHROPIC_API_KEY=...` (or `ant auth login`).
3. Try a few first, then the rest:

```
pettagam tag --limit 20
pettagam tag
```

It shows how many files will be sent and a rough cost, and asks before starting. Only files without tags are sent, so you can stop and resume; tags are kept when you run a new dry run. The default model is `claude-opus-5-5` (about $0.01 per photo); `--model claude-haiku-5-5` is much cheaper. HEIC and RAW photos are previewed with macOS's built-in `sips`; on Ubuntu they are skipped. Use `--only images/photos` to tag one folder.

## Find, stats and duplicates

```
pettagam find beach 2019                 # words in the name, folder, tags, description or camera
pettagam find --tag receipt --year 2024  # also --type images|documents|...; --json for scripts
pettagam stats                           # files by type, by year, cameras, top tags, space saved
pettagam duplicates --csv dupes.csv      # sets of identical old files, biggest waste first
```

To copy a selection somewhere else, for example to share it or put it on a USB stick:

```
pettagam export ~/Desktop/Beach --tag beach            # keeps the library's folders
pettagam export ~/Desktop/Tax-2024 --type documents --year 2024 --flat
```

Export only reads the library. A file that is already there is skipped, and a different file with the same name gets ` (2)` added instead of being replaced.

`find` prints full paths, so `pettagam find --tag invoice | head` works with other tools. `duplicates` only lists files. Nothing is deleted, and only one copy of each set went into the library. The Explore tab in the panel shows the same summary as `stats`.

## Health check

```
pettagam doctor
```

Checks Python, that the old files and library are reachable, whether placing will use hardlinks (same drive, and a drive format that supports them; exFAT doesn't), free space against what copying needs, where the catalog is, and whether the background inbox and AI tagging are set up. Each problem comes with what to do about it. The same list is in the panel.

## Damage check

A drive that holds files for years can damage them silently (bit rot). The first check records a fingerprint of every library file. Later checks read each file again and compare it with that fingerprint:

```
pettagam fixity                 # the whole library
pettagam fixity --minutes 60    # an hour at a time; the next run carries on where this one stopped
```

A file you edited is noted and its fingerprint is updated. A file whose content changed while its size and date did not is reported as **damaged**, and it stays flagged until it is restored. That is a sign the drive may be failing. The health check shows when the library was last checked, and the panel has a **Check for damage** button. When background sorting is on (next section), a 30-minute damage check also runs every Sunday at 3:00. It shows a notification if anything is damaged, and covers the whole library over a few weeks.

## Sorting the inbox automatically

```
pettagam service on --inbox "/Volumes/N300/Inbox"
pettagam service status
pettagam service off
```

This sorts the inbox in the background, even with the panel closed and after a restart: as soon as something lands in the inbox, and every 5 minutes (`--every SECONDS`) to pick up files that were still being copied. On a Mac it's a LaunchAgent (`~/Library/LaunchAgents/local.pettagam.inbox.plist`). On Ubuntu it's a systemd user timer and path watch (`~/.config/systemd/user/pettagam-inbox.*`). The weekly damage check runs beside it as `local.pettagam.fixity` or `pettagam-fixity.timer`, and `service off` stops both. What it does is logged in `~/.pettagam/inbox-service.log`, and a desktop notification says when files were filed (`--quiet` turns that off; on Ubuntu it uses `notify-send` when there is a desktop). The first time it reaches an external drive, macOS may ask whether Python may access files on a removable volume: allow it. On an Ubuntu server, run `loginctl enable-linger` once so it keeps running when you're logged out.

## Undo

```
pettagam undo --dry-run   # see what would happen
pettagam undo
```

Takes the placed files back out of the library and moves files that came from the inbox back into the inbox, so you can change your rules and place again. A library file is only removed when its old file is still there with the same content (for hardlinks, the very same file), so undo never removes the only copy of anything; files it keeps are listed. Views are removed too; your rules and `_meta` stay.

## Moving to Ubuntu later

Pettagam keeps a copy of its catalog with the library (`<library>/_meta/catalog.db`, refreshed after every placement, inbox pass and tagging run), so the catalog travels with the drive. On Ubuntu, set Pettagam up (see [Install](#install)), plug in the drive, and point a new catalog at the new mount points:

```
pettagam relocate --catalog-from /srv/ninaivu/Pettagam/_meta/catalog.db \
    --source "/srv/ninaivu/Old files" --library /srv/ninaivu/Pettagam
pettagam verify --rescan
```

The paths inside the catalog are relative, so nothing else changes: tags, rules, the inbox history and the views all carry over. `relocate` spot-checks that files are where it now expects them. Then set up the background inbox again with `pettagam service on`.

## Development

```
python3 -m pip install pytest
python3 -m pytest
```

CI runs the tests on macOS and Ubuntu with Python 3.9 and 3.12 for every pull request. Changes are listed in [CHANGELOG.md](CHANGELOG.md). To release, update the version in `pyproject.toml` and `pettagam/__init__.py`, add a CHANGELOG section, and push a tag such as `v0.3.0`: the release workflow tests it, builds the package, and publishes a GitHub release with that CHANGELOG section as its notes.
