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

```
cd pettagam
python3 -m pip install --user .
```

You now have a `pettagam` command. If your shell can't find it, run `python3 -m pettagam` from this folder instead.

## Easy launcher (no typing)

**On the Mac:** in Finder, open the `launchers` folder and double-click **`Pettagam.command`**. The first time, macOS may block it; if so, right-click it and choose **Open**. Your browser opens the Pettagam panel, where you:

1. **Choose folders.** Pick the folder with your old files, then the place for the new library.
2. **Start dry run.** This scans and plans without changing anything. You see a count for every folder, can search any file to see where it will go and why, and can open the full report.
3. **Place files**, then **Check everything is in sync.**

The panel remembers your folders, shows live progress, and keeps its catalog in `~/.pettagam/` on your internal drive. To stop it, close the Terminal window that opened with it.

**On Ubuntu Server (no screen):** run `launchers/pettagam-panel.sh --host 0.0.0.0 --no-browser`, then open the link it prints from any computer on your network. In this mode the link carries a secret token, so only people with the link can use the panel.

You can also start it with `pettagam panel`.

The panel has two tabs. **Organize** holds the steps above plus **4. Keep it tidy**: choose an inbox folder and press **Sort the inbox now**, **Rebuild views**, or **Add AI tags…** (it shows the number of files and a rough cost first). **Explore library** shows every file in the library, newest first, with thumbnails. Search by any word in the name, folder, tags or description, filter by kind and year, or click a tag. Click a file to see a preview, where it came from, its date and tags, and to open it.

## Use it from the command line

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

The built-in smart types are in `pettagam/classify.py`. To change where files go without editing code, write [your own rules](#your-own-rules).

## Your own rules

Rules live in `<library>/_meta/rules.json`. Create a starter file with examples (all switched off) and then check what your rules match:

```
pettagam --db ~/pettagam.db rules --library "/Volumes/N300/Pettagam" --init
pettagam --db ~/pettagam.db rules
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

## Inbox sorting

Make a folder such as `/Volumes/N300/Inbox` and drop new files into it (or point your phone backup or downloads there). Then:

```
pettagam --db ~/pettagam.db inbox "/Volumes/N300/Inbox" --dry-run   # only show where files would go
pettagam --db ~/pettagam.db inbox "/Volumes/N300/Inbox"             # file them
pettagam --db ~/pettagam.db inbox "/Volumes/N300/Inbox" --watch 60  # keep running, checking every minute
```

- Unlike the old-files folder, **the inbox is emptied**: each file is moved into the library (instantly on the same drive; across drives it is copied, checked, and only then removed from the inbox).
- Exact copies of files already in the library are moved to `_duplicates/` inside the inbox. Nothing is ever deleted.
- Files still being copied or downloaded (changed in the last 10 seconds, or ending in `.crdownload`, `.part` and similar) are left for the next pass. Junk like `.DS_Store` is ignored.
- Names are never overwritten: a clash gets a `__2` suffix. Folders that were split by letter are followed.
- Every file filed is listed in `<library>/_meta/inbox-log.csv`. The inbox must be outside both the library and the old-files folder.

## Automatic views

```
pettagam --db ~/pettagam.db views
```

This builds `<library>/views/` with `by-year/<year>/<month>/` (photos and videos), `by-camera/<camera>/`, `recent/` (last 30 days), `large-files/` (over 500 MB) and `by-tag/<tag>/`. Each entry is a hardlink to the file in the library, so views use no extra space, and deleting from a view never deletes the library file. Run it again after placing files, sorting the inbox or tagging; it removes the entries it made before and leaves anything else alone. (On a drive without hardlinks, such as exFAT, the entries are shortcuts instead.)

To change the views, write them to a file you can edit with `pettagam views --init` and edit `<library>/_meta/views.json`. Views use the same conditions as rules; a file appears in every view it matches, and `{tag}` makes one folder per tag.

## AI tagging

Optional. Claude looks at each photo, PDF and text file in the library and gives it 3 to 8 tags (beach, birthday, receipt, passport…) and a one-line description, which you can then search in **Explore library** and use in rules and views.

1. `python3 -m pip install anthropic`
2. Get an API key at <https://console.anthropic.com> and run `export ANTHROPIC_API_KEY=...` (or `ant auth login`).
3. Try a few first, then the rest:

```
pettagam --db ~/pettagam.db tag --limit 20
pettagam --db ~/pettagam.db tag
```

It shows how many files will be sent and a rough cost, and asks before starting. Only files without tags are sent, so you can stop and resume; tags are kept when you run a new dry run. The default model is `claude-opus-5-5` (about $0.01 per photo); `--model claude-haiku-5-5` is much cheaper. HEIC and RAW photos are previewed with macOS's built-in `sips`; on Ubuntu they are skipped. Use `--only images/photos` to tag one folder.

## Moving to Ubuntu later

Install Pettagam on Ubuntu the same way and plug in the drive. Because the paths change (`/srv/ninaivu/...` instead of `/Volumes/...`), start with a fresh `dryrun` against the new mount point, then `apply`. Files that are already placed are recognised (same inode) and left alone, so nothing is copied twice.

## Development

```
python3 -m pip install pytest
python3 -m pytest
```
