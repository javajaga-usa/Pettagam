# Pettagam requirements

| | |
| --- | --- |
| Document | Software requirements specification |
| Product | Pettagam (பெட்டகம், "strongbox") |
| Version | 1.0, covering Pettagam 0.3.0 and the unreleased changes listed in CHANGELOG.md |
| Date | 2026-10-22 |
| Owner | Jagadeesh (javajaga-usa) |
| Status | Baseline. Changes go through a pull request that updates this file, the code and the tests together. |

This document says what Pettagam must do and how well, so that every feature can be checked against a written requirement and every requirement against a test. It was written by auditing the code and documentation of version 0.3.0. Section 9 lists what the audit found, what was fixed with this document, and what is still open.

The key words **shall** (mandatory), **should** (recommended) and **may** (optional) are used as in RFC 2119.

## Contents

1. [Purpose and scope](#1-purpose-and-scope)
2. [Users and operating environment](#2-users-and-operating-environment)
3. [Definitions](#3-definitions)
4. [User stories and acceptance criteria](#4-user-stories-and-acceptance-criteria)
5. [Functional requirements](#5-functional-requirements)
6. [Non-functional requirements](#6-non-functional-requirements)
7. [Software and platform requirements](#7-software-and-platform-requirements)
8. [Constraints, assumptions and out of scope](#8-constraints-assumptions-and-out-of-scope)
9. [Audit findings](#9-audit-findings)
10. [Verification and change control](#10-verification-and-change-control)

## 1. Purpose and scope

Pettagam turns a large, deeply nested personal file collection into a clean library organized by what each file is (`images/photos`, `documents/invoices`, `audio/voice`...), and then keeps that library tidy for years.

In scope:

- A one-time reorganization of an existing folder ("the old files") into a library, by hardlink on the same drive or copy across drives, with a dry run first and a proof of completeness after.
- Ongoing care: sorting new files from an inbox, user-defined placement rules, automatic views, optional AI tagging, search and export, damage (bit rot) detection, undo, and moving the library to another computer.
- Two ways to use it: a command line (`pettagam`) and a local web panel (`pettagam panel`), sharing one catalog.

The guiding principle, which overrides any other requirement it conflicts with: **Pettagam never loses or silently changes a user's file.**

## 2. Users and operating environment

### 2.1 Users

| User | Description | Needs |
| --- | --- | --- |
| Owner | One person organizing a family archive: photos, scans, documents, recordings, decades of downloads. Comfortable double-clicking and following instructions, not a programmer. | Point-and-click use, plain language, nothing irreversible without a preview, confidence nothing was lost. |
| Maintainer | The owner, or Claude working for them, changing the code. | Tests for every requirement, CI on both platforms, one place that lists the requirements. |

### 2.2 Environment

| Item | Today | Planned |
| --- | --- | --- |
| Computer | Mac (Apple silicon or Intel), a current macOS release | Ubuntu 24.04 LTS server or desktop |
| Drive | Toshiba N300 Pro hard drive (7,200 rpm, CMR), USB or Thunderbolt enclosure, mounted under `/Volumes/` | Same drive or its successor, mounted under `/srv/ninaivu/` |
| Drive format | APFS (hardlinks work) | ext4 (see PLAT-6: Ubuntu cannot write APFS) |
| Python | 3.13 or newer, from python.org or Homebrew (the 3.9 in the Command Line Tools is too old) | 3.13 or newer (Ubuntu 24.04 ships 3.12, so 3.13 comes from the deadsnakes PPA or a newer Ubuntu) |
| Collection size (assumed design point, to confirm) | Up to 2 million files and 8 TB | Same |
| Catalog | `~/.pettagam/pettagam.db` on the internal SSD, with a copy in `<library>/_meta/catalog.db` | Same |

The N300 Pro is a spinning disk: random reads cost about 10 ms each, sequential reads run at about 250 MB/s. That is why parallelism defaults to 2, the catalog lives on the SSD, and duplicate detection reads only files that share a size.

## 3. Definitions

| Term | Meaning |
| --- | --- |
| Old files (source) | The existing folder being reorganized. Pettagam only reads it. |
| Library | The new, organized folder tree Pettagam builds. |
| Catalog | The SQLite database with one row per old file and per inbox file, and what happened to each. |
| Place / placement | Putting a file into the library: a hardlink on the same drive, otherwise a copy. |
| Dry run | Scan, duplicate detection and planning, with a report. Writes only the catalog and the report. |
| Smart type | Pettagam's own classification of a file into a category and subtype, from its content, name, size and metadata. |
| Rule | A user-written condition and folder template in `<library>/_meta/rules.json` that overrides the smart type. |
| Inbox | A folder whose files are moved into the library as they arrive. |
| View | A folder of hardlinks (or shortcuts) under `<library>/views/` showing the library another way, such as by year. |
| Fixity | A stored content fingerprint of each library file, used to detect silent damage. |
| Junk | Files that carry no user content (`.DS_Store`, `Thumbs.db`, empty files...) and are skipped. |

## 4. User stories and acceptance criteria

Each story lists the requirements that implement it (section 5 and 6). Acceptance criteria are written so that a test or a person can decide pass or fail.

**US-1. Preview the reorganization.** As the owner, I want to see where every file would go before anything happens, so that I can correct the plan first.
- AC-1.1 A dry run writes a summary report and a spreadsheet with one row per file: its old path, new path, the reason, and the rule if one chose it.
- AC-1.2 After a dry run, the old files are unchanged: same names, contents and dates.
- AC-1.3 Files whose type is a weak guess are planned into `review/` rather than guessed.
- Requirements: FR-SCAN-1..4, FR-PLAN-1..6, FR-REPORT-1..2, DS-1.

**US-2. Reorganize without risk.** As the owner, I want the library built without touching the old files and without using double the space.
- AC-2.1 On the same drive, every placed file is a hardlink to its old file and uses no extra space.
- AC-2.2 Placing can be stopped and restarted; finished files are skipped.
- AC-2.3 A file already in the library is never replaced.
- Requirements: FR-APPLY-1..5, DS-1..5.

**US-3. Prove nothing was lost.** As the owner, I want a check that every file made it and is identical.
- AC-3.1 The check reports `old files = placed + duplicates + junk`, and lists any file not accounted for.
- AC-3.2 Every hardlinked file is confirmed to be the same file; copies can be checksummed in full on request.
- AC-3.3 Changes to the old files since the scan are listed.
- AC-3.4 The command exits with code 0 only when everything matches.
- Requirements: FR-VERIFY-1..4.

**US-4. Choose where things go.** As the owner, I want my own rules, such as "PDFs with *tax* in the name go to `documents/tax/<year>`".
- AC-4.1 An invalid rules file is rejected with a message naming the rule and the problem, and is never saved from the panel.
- AC-4.2 I can see how many files each rule takes, and ask why one file goes where it goes.
- Requirements: FR-RULES-1..5.

**US-5. Keep it tidy.** As the owner, I want files I drop into an inbox filed automatically, even when the panel is closed.
- AC-5.1 Files still being copied are left for the next pass.
- AC-5.2 Exact copies of library files are set aside in `_duplicates/`, not deleted.
- AC-5.3 Across drives, an inbox file is removed only after its copy in the library is on the disk and checked in full.
- AC-5.4 The background job survives a restart and can be turned off with one command.
- Requirements: FR-INBOX-1..7, FR-SERVICE-1..4, DS-2, DS-3, DS-6.

**US-6. Find things.** As the owner, I want to search by name, folder, year, camera and descriptive tags, and copy a selection elsewhere.
- Requirements: FR-FIND-1..4, FR-VIEWS-1..3, FR-TAG-1..5.

**US-7. Know the drive is healthy.** As the owner, I want to learn about silent damage before it spreads to backups.
- AC-7.1 A file whose content changed while its size and date did not is reported as damaged and stays flagged until restored.
- AC-7.2 An edited file is not reported as damaged.
- Requirements: FR-FIX-1..4, FR-DOCTOR-1..3.

**US-8. Change my mind.** As the owner, I want to take a placement back out of the library.
- AC-8.1 Undo removes a library file only when its old file still holds the same content.
- AC-8.2 Inbox files go back to the inbox.
- Requirements: FR-UNDO-1..3.

**US-9. Move to Ubuntu.** As the owner, I want the library, tags and rules to work after moving the drive to an Ubuntu machine.
- AC-9.1 The catalog travels with the library and can be pointed at the new mount points.
- AC-9.2 The health check warns, while still on the Mac, about drive formats Ubuntu can't write.
- Requirements: FR-MOVE-1..3, PLAT-1..6.

**US-10. Use it without a Terminal.** As the owner, I want to double-click to install, open and remove Pettagam.
- Requirements: FR-PANEL-1..6, FR-INSTALL-1..4, USE-1..4.

**US-11. Use it from another computer at home.** As the owner, I may run the panel on the Ubuntu server and open it from my laptop, without strangers being able to use it.
- Requirements: SEC-1..9.

## 5. Functional requirements

"Verified by" names the automated tests (in `tests/`) that check the requirement. `tests/test_requirements.py` checks that every test named here exists. "Manual" means it is checked by a person following the README.

### 5.1 Scan (`pettagam scan`, part of the dry run)

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-SCAN-1 | The scan shall record every regular file under the source with its path, size, modification time, device and inode, without following symbolic links. | `scan.py` | `test_pipeline.py::test_dry_run_changes_nothing` |
| FR-SCAN-2 | The scan shall detect each file's real format from its content (magic bytes), and its dimensions, camera and EXIF capture date where present. | `sniff.py` | `test_pipeline.py::test_sniffing`, `test_milestone2.py::test_exif_capture_time_is_read` |
| FR-SCAN-3 | The scan shall mark junk files and skip system folders (`.Spotlight-V100`, `.Trashes`...). | `classify.py` | `test_pipeline.py::test_classification_and_naming` |
| FR-SCAN-4 | Duplicate detection shall find byte-identical files, reading only files that share a size, first a 64 KB sample, then in full. | `hashing.py` | `test_library.py::test_stats_and_duplicates` |
| FR-SCAN-5 | A new scan shall keep the AI tags of files whose path, size and date are unchanged. | `scan.py` | `test_milestone2.py::test_tagging_is_resumable_and_feeds_rules` |

### 5.2 Plan

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-PLAN-1 | Each file shall be placed by the first matching user rule, otherwise by its smart type as `<category>/<subtype>/`. | `plan.py`, `classify.py` | `test_milestone2.py::test_rules_steer_the_dry_run` |
| FR-PLAN-2 | Files whose classification confidence is below the review threshold shall be planned into `review/<category>/`. | `plan.py` | `test_requirements.py::test_weak_guesses_go_to_review` |
| FR-PLAN-3 | Names shall keep the original stem, use a lowercase canonical extension (from the content when the name's is missing or wrong), and replace characters Windows clients can't open. | `plan.py` | `test_pipeline.py::test_safe_name_and_ext` |
| FR-PLAN-4 | Name clashes (case-insensitive) shall get a `__N` suffix. | `plan.py` | `test_pipeline.py::test_name_clashes_get_suffix` |
| FR-PLAN-5 | A folder over the split threshold (default 5,000) shall be split by first letter, or into sorted name ranges when names share a prefix. | `plan.py` | `test_pipeline.py::test_large_folders_split_by_letter_then_range` |
| FR-PLAN-6 | Planning shall refuse a library inside the source or a source inside the library. | `plan.py` | `test_pipeline.py::test_library_inside_source_is_refused` |

### 5.3 Report

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-REPORT-1 | The dry run shall write `report.md` (totals, folders, duplicates, junk, rules) and `plan.csv` (one row per file). | `report.py` | `test_pipeline.py::test_dry_run_changes_nothing` |
| FR-REPORT-2 | Placing shall write `<library>/_meta/manifest.csv` recording every old path and what happened to it. | `apply.py` | `test_pipeline.py::test_apply_link_and_verify` |

### 5.4 Apply (place files)

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-APPLY-1 | On the same drive, files shall be placed as hardlinks; otherwise by copy. The user may force either. | `apply.py` | `test_pipeline.py::test_apply_link_and_verify`, `test_pipeline.py::test_apply_copy_and_detect_drift` |
| FR-APPLY-2 | Placing shall be resumable: files already done are skipped, and an identical file already at the destination counts as done. | `apply.py` | `test_pipeline.py::test_apply_link_and_verify` |
| FR-APPLY-3 | The command line shall ask for confirmation before placing, unless `--yes` is given. | `cli.py` | Manual |
| FR-APPLY-4 | A copy shall be written to a `.part` file, flushed to the disk, checked for size, and only then given its name. | `apply.py`, `safeio.py` | `test_safety.py::test_apply_by_copy_places_identical_files`, `test_safety.py::test_durable_copy_checks_and_never_replaces` |
| FR-APPLY-5 | Only one step at a time shall write to a library (place, inbox pass, undo); a second one is refused with a plain message. | `safeio.py` | `test_safety.py::test_apply_and_undo_wait_for_the_library_lock`, `test_maintenance.py::test_inbox_pass_skips_while_another_runs` |

### 5.5 Verify

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-VERIFY-1 | Verify shall check the accounting: every old file is placed, a duplicate of a placed file, or junk. | `verify.py` | `test_pipeline.py::test_apply_link_and_verify` |
| FR-VERIFY-2 | Verify shall confirm each hardlink is the same file as its old file, and each copy has the same size and date; it shall checksum a random sample of copies, or every copy with `--full`. | `verify.py` | `test_safety.py::test_verify_full_checksums_every_copy` |
| FR-VERIFY-3 | With `--rescan`, verify shall list old files added, changed or deleted since the scan. | `verify.py` | `test_pipeline.py::test_apply_copy_and_detect_drift` |
| FR-VERIFY-4 | Verify shall exit 0 when everything matches and 2 otherwise. | `cli.py` | `test_safety.py::test_verify_full_checksums_every_copy` |

### 5.6 Rules

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-RULES-1 | Rules shall support the conditions category, subtype, ext, kind, name, path, name_regex, camera, size_over/under, older/newer_than, year, confidence_below, tags, and any/all/not. | `rules.py` | `test_milestone2.py::test_rules_steer_the_dry_run` |
| FR-RULES-2 | Folder templates shall support `{year}`, `{month}`, `{category}`, `{subtype}`, `{ext}`, `{camera}`, and shall never produce a path outside the library or into `_meta/` or `views/`. | `rules.py` | `test_milestone2.py::test_rules_validate_with_clear_messages` |
| FR-RULES-3 | Invalid rules shall be rejected with a message naming the rule and the problem. | `rules.py` | `test_milestone2.py::test_rules_validate_with_clear_messages` |
| FR-RULES-4 | `pettagam rules` shall count the files each rule takes; `--init` shall write switched-off examples; `--explain` shall say where a file goes and why. | `cli.py`, `plan.py` | `test_milestone2.py::test_rules_command_init_and_count`, `test_library.py::test_rules_explain` |
| FR-RULES-5 | The panel's rules editor shall check rules and save them only when valid. | `panel.py` | `test_panel.py::test_rules_editor` |

### 5.7 Inbox

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-INBOX-1 | An inbox pass shall move each settled file into the library by rules and smart types, and empty folders it leaves. | `inbox.py` | `test_milestone2.py::test_inbox_files_new_files_and_empties_itself` |
| FR-INBOX-2 | Files changed in the last N seconds (default 10), or with a partial-download extension, shall be left for the next pass. | `inbox.py` | `test_milestone2.py::test_inbox_files_new_files_and_empties_itself` |
| FR-INBOX-3 | Exact copies of library files shall be moved to `<inbox>/_duplicates/`, never deleted. | `inbox.py` | `test_milestone2.py::test_inbox_files_new_files_and_empties_itself` |
| FR-INBOX-4 | New files shall follow the letter folders a big folder was split into. | `inbox.py` | `test_milestone2.py::test_inbox_follows_split_folders` |
| FR-INBOX-5 | The inbox shall be refused if it is inside, or contains, the library or the old files. | `inbox.py` | `test_milestone2.py::test_inbox_must_be_separate_from_old_files` |
| FR-INBOX-6 | Every filed file shall be logged in `<library>/_meta/inbox-log.csv` and the catalog. | `inbox.py` | `test_milestone2.py::test_inbox_files_new_files_and_empties_itself` |
| FR-INBOX-7 | `--dry-run` shall show where files would go without moving anything; `--watch N` shall repeat every N seconds, re-reading rules each pass. | `inbox.py`, `cli.py` | `test_milestone2.py::test_inbox_files_new_files_and_empties_itself` (dry run), `test_milestone2.py::test_inbox_command`; `--watch` manual |

### 5.8 Background service

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-SERVICE-1 | `service on` shall install a macOS LaunchAgent, or a systemd user timer and path unit on Linux, that runs an inbox pass when files arrive and every N seconds (minimum 60). | `service.py` | `test_maintenance.py::test_service_mac_launch_agent`, `test_maintenance.py::test_service_linux_systemd_units` |
| FR-SERVICE-2 | Alongside it, a weekly damage check (30 minutes, Sunday 03:00) shall run and notify on damage. | `service.py` | `test_maintenance.py::test_service_mac_launch_agent` |
| FR-SERVICE-3 | `service off` shall stop and remove both jobs; `service status` shall say whether it is on and show the end of its log. | `service.py` | `test_maintenance.py::test_service_mac_launch_agent`, `test_maintenance.py::test_service_linux_systemd_units` |
| FR-SERVICE-4 | The background log shall be kept small: past 5 MB it is rotated, keeping one older file. | `service.py`, `cli.py` | `test_safety.py::test_background_log_rotates` |

### 5.9 Views, tagging, find, export

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-VIEWS-1 | `views` shall build by-year, by-camera, recent, large-files and by-tag folders of hardlinks (shortcuts where hardlinks aren't possible). | `views.py` | `test_milestone2.py::test_views_are_hardlinks_and_rebuild_cleanly` |
| FR-VIEWS-2 | Rebuilding shall remove only the entries Pettagam made before. | `views.py` | `test_milestone2.py::test_views_are_hardlinks_and_rebuild_cleanly` |
| FR-VIEWS-3 | Views shall be configurable in `<library>/_meta/views.json` using the rule conditions. | `views.py` | `test_milestone2.py::test_custom_views_file` |
| FR-TAG-1 | `tag` shall send photos, PDFs and text files from the library to Claude and store 3 to 8 tags and a one-line description. | `tag.py` | `test_milestone2.py::test_tagging_is_resumable_and_feeds_rules` |
| FR-TAG-2 | Before sending, the command line shall show the number of files and an estimated cost and ask for confirmation; the panel shows the same before starting. | `cli.py`, `tag.py`, `panel.html` | Manual |
| FR-TAG-3 | Tagging shall be resumable: only untagged files are sent unless `--retag`. | `tag.py` | `test_milestone2.py::test_tagging_is_resumable_and_feeds_rules` |
| FR-TAG-4 | A file that fails or is declined shall not stop the others. | `tag.py` | `test_milestone2.py::test_tagging_is_resumable_and_feeds_rules` |
| FR-TAG-5 | Tags shall be usable in rules, views, search and export. | `rules.py`, `views.py`, `library.py` | `test_milestone2.py::test_tagging_is_resumable_and_feeds_rules` |
| FR-FIND-1 | `find` shall search words in the name, folder, tags, description and camera, with type, tag and year filters. | `library.py` | `test_library.py::test_search_and_find_command` |
| FR-FIND-2 | `stats` shall summarize by type, year, camera and tag, and the space saved. | `library.py` | `test_library.py::test_stats_and_duplicates` |
| FR-FIND-3 | `duplicates` shall list identical sets, biggest waste first, and write them to CSV. | `library.py` | `test_library.py::test_stats_and_duplicates` |
| FR-FIND-4 | `export` shall copy matching files elsewhere, never overwriting (a different file gets ` (2)`), without changing the library. | `library.py` | `test_library.py::test_export_copies_a_selection_without_overwriting` |

### 5.10 Damage check, health check, undo, relocate

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-FIX-1 | The first damage check shall fingerprint every library file. | `fixity.py` | `test_library.py::test_fixity_finds_silent_damage_but_not_edits` |
| FR-FIX-2 | A later check shall report as damaged a file whose content changed while its size and date did not, and keep it flagged until restored; an edited file shall be re-fingerprinted. | `fixity.py` | `test_library.py::test_fixity_finds_silent_damage_but_not_edits` |
| FR-FIX-3 | Checks shall run in time slices, least recently checked first. | `fixity.py` | `test_library.py::test_fixity_slices_and_missing` |
| FR-FIX-4 | The exit code shall be 2 for damage, 1 for missing or unreadable files, 0 otherwise. | `cli.py` | `test_library.py::test_fixity_slices_and_missing` |
| FR-DOCTOR-1 | The health check shall report Python, drives reachable, library writable, same drive and hardlinks, free space against what copying needs, catalog location, inbox, background job, damage-check age and AI-tagging readiness, each with what to do. | `doctor.py` | `test_maintenance.py::test_doctor_reports_hardlinks_space_and_missing_drive` |
| FR-DOCTOR-2 | The health check shall report the library drive's format, warn when it can't hardlink, and note when Ubuntu won't be able to write it. | `doctor.py` | `test_safety.py::test_drive_format_is_read_from_mounts`, `test_safety.py::test_drive_format_advice` |
| FR-DOCTOR-3 | The health check shall never change anything except a tiny test file it removes again. | `doctor.py` | `test_requirements.py::test_health_check_changes_nothing` |
| FR-UNDO-1 | Undo shall remove a library file only when its old file still holds the same content. | `maintenance.py` | `test_maintenance.py::test_undo_never_removes_the_only_copy` |
| FR-UNDO-2 | Undo shall return inbox files to the inbox, never replacing a file there, and checking a copy across drives before removing it from the library. | `maintenance.py`, `safeio.py` | `test_maintenance.py::test_undo_hardlinks_and_inbox_files` |
| FR-UNDO-3 | `undo --dry-run` shall show what would happen; without `--yes` the command line shall ask first. | `cli.py` | `test_maintenance.py::test_undo_hardlinks_and_inbox_files` (dry run); asking is manual |
| FR-MOVE-1 | A copy of the catalog shall be kept at `<library>/_meta/catalog.db`, refreshed after placing, inbox passes, tagging and undo. | `maintenance.py` | `test_maintenance.py::test_catalog_copy_travels_and_relocate_restores_it` |
| FR-MOVE-2 | `relocate` shall restore a catalog from that copy and point it at new source and library roots, then spot-check files. | `maintenance.py` | `test_maintenance.py::test_catalog_copy_travels_and_relocate_restores_it` |
| FR-MOVE-3 | Paths inside the catalog shall be relative to the source and library roots. | `db.py` | `test_maintenance.py::test_catalog_copy_travels_and_relocate_restores_it` |

### 5.11 Panel and install

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| FR-PANEL-1 | The panel shall walk through choosing folders, health check, dry run, placing, verify, undo and keeping it tidy, with live progress. | `panel.py`, `panel.html` | `test_panel.py::test_full_flow_through_panel` |
| FR-PANEL-2 | The panel shall refuse to place, verify or undo before a dry run, and run one step at a time. | `panel.py` | `test_panel.py::test_apply_before_dryrun_is_refused` |
| FR-PANEL-3 | The Explore tab shall search, filter, show thumbnails and preview files, serving only files inside the library. | `panel.py` | `test_panel.py::test_explore_inbox_and_files` |
| FR-PANEL-4 | The panel shall show the health check, switch background sorting on and off, and run undo. | `panel.py` | `test_panel.py::test_doctor_service_and_undo_from_panel` |
| FR-PANEL-5 | The folder browser shall list folders starting from `/Volumes`, `/srv`, `/mnt`, `/media` and home. | `panel.py` | `test_panel.py::test_browse_lists_folders` |
| FR-PANEL-6 | The panel and the command line shall share one catalog (`~/.pettagam/pettagam.db` unless `PETTAGAM_DB`, `--db` or `./pettagam.db` says otherwise). | `cli.py` | `test_library.py::test_default_db` |
| FR-INSTALL-1 | `Setup Pettagam.command` shall build a Pettagam app in `~/Applications` on the Mac itself, and optionally set up AI tagging in a `.venv`. | `Setup Pettagam.command` | `test_launchers.py::test_setup_builds_the_app_and_uninstall_removes_it_but_never_files` |
| FR-INSTALL-2 | `launchers/setup-ubuntu.sh` shall add a working `pettagam` command and a menu entry. | `launchers/setup-ubuntu.sh` | `test_launchers.py::test_ubuntu_setup_adds_a_working_command` |
| FR-INSTALL-3 | `Uninstall Pettagam.command` shall remove the app, background job and `.venv`, ask before removing settings, and never touch the user's files. | `Uninstall Pettagam.command` | `test_launchers.py::test_setup_builds_the_app_and_uninstall_removes_it_but_never_files` |
| FR-INSTALL-4 | `pip install .` shall install a `pettagam` command with the panel page included. | `pyproject.toml` | CI job `package` |

## 6. Non-functional requirements

### 6.1 Data safety (highest priority)

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| DS-1 | The old files shall only be read: never written, renamed, moved, re-dated or deleted, by any command. | all | `test_pipeline.py::test_dry_run_changes_nothing`, `test_pipeline.py::test_apply_link_and_verify` |
| DS-2 | No step shall replace an existing file in the library, the inbox, an export folder or `_duplicates/`. Moves use an atomic no-replace operation where the drive allows it. | `safeio.py`, `apply.py`, `inbox.py`, `library.py` | `test_pipeline.py::test_existing_different_file_is_never_overwritten`, `test_safety.py::test_move_no_clobber_refuses_an_existing_file`, `test_safety.py::test_move_no_clobber_without_hardlinks` |
| DS-3 | A file shall be removed from where it was (inbox, or library during undo) only after its new copy is flushed to the disk and checked in full. A failed check leaves the original in place. | `safeio.py` | `test_safety.py::test_move_across_drives_copies_checks_then_removes`, `test_safety.py::test_durable_copy_checks_and_never_replaces` |
| DS-4 | Nothing shall be deleted to remove duplicates: duplicates in the old files are only listed; duplicates in the inbox are set aside. | `hashing.py`, `inbox.py` | `test_library.py::test_stats_and_duplicates`, `test_milestone2.py::test_inbox_files_new_files_and_empties_itself` |
| DS-5 | Interrupted copies shall never look complete: they keep a `.part` name until checked. | `safeio.py` | `test_safety.py::test_durable_copy_checks_and_never_replaces` |
| DS-6 | Only one writer at a time per library (FR-APPLY-5). | `safeio.py` | `test_safety.py::test_apply_and_undo_wait_for_the_library_lock` |
| DS-7 | The catalog shall survive a crash (SQLite WAL) and be backed up with the library (FR-MOVE-1). An older catalog shall be upgraded in place on first open. | `db.py`, `maintenance.py` | `test_milestone2.py::test_old_catalog_gets_new_columns` |
| DS-8 | Silent damage to library files shall be detectable (FR-FIX-1..4). | `fixity.py` | `test_library.py::test_fixity_finds_silent_damage_but_not_edits` |

### 6.2 Security

Threat model: Pettagam runs as the owner on their own computer. The panel is a web server, so the threats are other websites the owner visits (cross-site requests, DNS rebinding, clickjacking), hostile files inside the collection (an HTML or SVG file with scripts), and, in network mode, other devices on the home network. Pettagam has no accounts and stores no passwords; the Anthropic API key stays in the environment or the `ant` login, never in Pettagam's files.

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| SEC-1 | The panel shall listen on 127.0.0.1 unless the owner asks for the network. | `panel.py`, `cli.py` | `test_panel.py::test_full_flow_through_panel` |
| SEC-2 | In local mode the panel shall reject requests whose Host header is not `localhost`, `127.0.0.1` or `[::1]` (defeats DNS rebinding). | `panel.py` | `test_safety.py::test_panel_refuses_other_host_names` |
| SEC-3 | In network mode every request shall carry the random start-up token (URL or HttpOnly SameSite=Strict cookie), compared in constant time. | `panel.py` | `test_panel.py::test_network_mode_needs_token` |
| SEC-4 | State-changing requests shall be POSTs with a JSON body, and be refused when the Origin is another site. | `panel.py` | `test_panel.py::test_other_websites_cannot_trigger_steps`, `test_safety.py::test_panel_post_needs_json_and_a_sane_size` |
| SEC-5 | Request bodies shall be limited to 2 MB, and settings shall be type- and range-checked (workers 1..32, split 100..1,000,000, paths as strings). | `panel.py` | `test_safety.py::test_panel_post_needs_json_and_a_sane_size`, `test_safety.py::test_panel_settings_are_checked` |
| SEC-6 | The panel page shall send a Content-Security-Policy that forbids framing by other sites and loading from other origins, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. | `panel.py` | `test_safety.py::test_panel_headers_and_sandboxed_library_files` |
| SEC-7 | Library files served by the panel shall be confined to the library (no `..` or symlink escape) and, apart from PDFs, served in a CSP sandbox so a hostile HTML or SVG file can't act as the panel. | `panel.py` | `test_safety.py::test_panel_headers_and_sandboxed_library_files`, `test_panel.py::test_explore_inbox_and_files` |
| SEC-8 | Rule templates shall not be able to write outside the library (FR-RULES-2). | `rules.py` | `test_milestone2.py::test_rules_validate_with_clear_messages` |
| SEC-9 | Background jobs shall run as the user, never as root, with low CPU and I/O priority. | `service.py` | `test_requirements.py::test_background_jobs_run_at_low_priority_as_the_user` |

### 6.3 Privacy

| ID | Requirement | Code | Verified by |
| --- | --- | --- | --- |
| PRIV-1 | No file, name or path shall leave the computer except through AI tagging, which is off until the owner installs it, provides a key and confirms the file count and cost. | `tag.py`, `cli.py` | Manual (FR-TAG-2) |
| PRIV-2 | AI tagging shall send only library files of the supported kinds, and at most the first 8 KB of a text file. | `tag.py` | `test_milestone2.py::test_tagging_is_resumable_and_feeds_rules`, `test_requirements.py::test_tagging_sends_only_the_start_of_a_text_file` |
| PRIV-3 | `~/.pettagam/` (catalog, settings, logs: a list of every file name) shall be readable only by the owner (mode 0700). | `safeio.py`, `panel.py`, `cli.py`, `service.py` | `test_safety.py::test_private_dir_is_only_for_this_user`, `test_safety.py::test_panel_settings_folder_is_private` |
| PRIV-4 | Pettagam shall send no telemetry. | all | Code review |

### 6.4 Reliability and performance

| ID | Requirement | Verified by |
| --- | --- | --- |
| REL-1 | Every long step (scan, apply, inbox, tag, fixity) shall be safe to interrupt and run again. | `test_pipeline.py::test_apply_link_and_verify`, `test_library.py::test_fixity_slices_and_missing` |
| REL-2 | One unreadable file shall be recorded as an error and shall not stop the others. | `test_pipeline.py::test_existing_different_file_is_never_overwritten` |
| PERF-1 | The scan should examine at least 1,000 files per second on the N300 Pro with 2 workers and the catalog on the SSD. | Not yet measured (gap G-12) |
| PERF-2 | Hardlink placement should run at least 2,000 files per second on the same drive. | Not yet measured (gap G-12) |
| PERF-3 | Duplicate detection shall read in full only files that share both size and a 64 KB sample with another file. | `test_library.py::test_stats_and_duplicates` |
| PERF-4 | The panel's Explore tab should answer a search over 1 million files in under 1 second. | Not yet measured (gap G-12) |
| PERF-5 | The background jobs shall run at low priority (`Nice=10`, idle I/O) so they don't slow the computer. | `test_requirements.py::test_background_jobs_run_at_low_priority_as_the_user` |

### 6.5 Usability

| ID | Requirement | Verified by |
| --- | --- | --- |
| USE-1 | Every message shall be plain language a non-programmer understands, and every problem the health check reports shall say what to do. | Review |
| USE-2 | Everything the owner needs day to day shall be possible from the panel, without a Terminal. | `test_panel.py::test_full_flow_through_panel` |
| USE-3 | Installing and removing shall each be one double-click on the Mac. | `test_launchers.py::test_setup_builds_the_app_and_uninstall_removes_it_but_never_files` |
| USE-4 | The panel should work on a phone-sized screen and be usable by keyboard. | Manual; keyboard and screen-reader use not yet audited (gap G-14) |

### 6.6 Maintainability

| ID | Requirement | Verified by |
| --- | --- | --- |
| MAINT-1 | The core shall use the Python standard library only; extra packages shall be optional extras (`fast`, `ai`). | `pyproject.toml`; CI runs the tests with and without the extras |
| MAINT-2 | Every requirement shall be verified by an automated test where practical, and `tests/test_requirements.py` shall check that the tests this document names exist. | `test_requirements.py::test_every_named_test_exists` |
| MAINT-3 | CI shall run the tests on macOS and Ubuntu with Python 3.13 (the minimum) and 3.14 for every pull request, and build and install the package. | `.github/workflows/tests.yml` |
| MAINT-4 | The version shall be defined once (`pettagam/__init__.py`); releases are made by pushing a matching `v*` tag, with notes from `CHANGELOG.md`. | `.github/workflows/release.yml` |

## 7. Software and platform requirements

### 7.1 Runtime

| Package | Needed for | Version | Where it is declared |
| --- | --- | --- | --- |
| Python | everything | 3.13 or newer (tested on 3.13 and 3.14) | `pyproject.toml` `requires-python`, `pettagam.MIN_PYTHON`, `launchers/find-python.sh` |
| SQLite | the catalog | the one built into Python | n/a |
| `xxhash` | optional, faster hashing | `>=3,<5` | extra `fast` |
| `anthropic` | optional, AI tagging | `>=1,<3` (uses `output_config` and the server-side fallback beta, present since 1.0; tested with 2.1) | extra `ai` |
| `pytest`, `build` | development | `pytest>=7`, `build>=1` | extra `dev` |

Install: `pip install .` (core), `pip install ".[fast]"`, `".[ai]"`, `".[all]"`, or `".[dev]"` for development. The setup scripts install the AI extra into a `.venv` beside the code so the system Python is never changed.

Upper bounds stop a future major version of an optional library from breaking Pettagam unnoticed; raise them after CI passes on the new version.

### 7.2 Platform

| ID | Requirement |
| --- | --- |
| PLAT-1 | Pettagam shall run on macOS 13 or newer and Ubuntu 22.04/24.04 LTS, on Apple silicon, Intel and x86-64/ARM64 Linux. |
| PLAT-2 | The library drive should be APFS (Mac) or ext4 (Ubuntu) so hardlinks work; on exFAT/FAT/NTFS files are copied and views are shortcuts (FR-DOCTOR-2). |
| PLAT-3 | HEIC and RAW previews for AI tagging use macOS `sips`; on Ubuntu those files are skipped. |
| PLAT-4 | Background sorting uses launchd on macOS and systemd user units on Ubuntu; on a headless Ubuntu server `loginctl enable-linger` is required. |
| PLAT-5 | Paths shall be valid on Windows/Samba clients (no `\ / : * ? " < > |` or control characters in names), so the library can be shared from the Ubuntu server. |
| PLAT-7 | Pettagam shall require Python 3.13 or newer and say so clearly on an older one. The setup scripts shall find a new enough Python (also where python.org and Homebrew install it, which a double-clicked app can't see on its PATH), and Pettagam shall run from its own `.venv` so the app, the command and the background job use that Python whatever else is installed. The minimum shall be stated the same way in every place. Verified by `test_requirements.py::test_python_minimum_is_the_same_everywhere`, `test_launchers.py::test_python_finder_needs_3_13_and_prefers_the_venv`. A background job set up with an older Python shall be moved to the new one by setup (`pettagam service refresh`), and the health check shall flag one that still uses an old Python: `test_maintenance.py::test_service_refresh_moves_the_job_to_this_python`, `test_maintenance.py::test_doctor_flags_a_background_job_on_an_old_python`. |
| PLAT-6 | Ubuntu can't write to APFS. Moving the library to Ubuntu means copying it to an ext4 drive with hardlinks preserved (`rsync -aH`, or the library alone if the old files stay on the Mac) and then running `relocate`. The README describes this. |

## 8. Constraints, assumptions and out of scope

- Single owner, single computer at a time. No multi-user access control beyond the panel token.
- The old files and the library are local or directly attached drives. Network shares work but are not a design point (no hardlinks across SMB, slow random reads).
- Windows is out of scope as a host (the code avoids Windows-only failures but is not tested there).
- Pettagam is not a backup. A library of hardlinks shares its data with the old files: damage to one is damage to both. The owner needs a separate backup (README, Damage check).
- Out of scope: editing files, face recognition, cloud sync, deleting the old files.

## 9. Audit findings

The audit compared the 0.3.0 code, tests and README against the requirements above.

### 9.1 Fixed in this change

| Gap | Finding | Risk | Fix | Requirement |
| --- | --- | --- | --- | --- |
| G-1 | In local mode the panel accepted any Host header. A website could use DNS rebinding to reach the panel on 127.0.0.1, read folder listings and the catalog, change settings, run steps and install the background job. | High | Host header must name this computer. | SEC-2 |
| G-2 | Library files were served from the panel's own origin. A hostile `.html` or `.svg` in the collection, opened from Explore, could run scripts with the panel's powers. | High | CSP sandbox and `nosniff` on library files (PDFs excepted). | SEC-7 |
| G-3 | Inbox files moved across drives, and library files returned to the inbox by undo, were removed after the copy without flushing it to disk; undo did not check the copy at all. A power cut could lose the only copy. | High | `safeio.durable_copy`: flush (`F_FULLFSYNC` on macOS), check, then name; then remove the original. | DS-3 |
| G-4 | Same-drive moves checked for an existing file and then renamed, which replaces a file that appears in between; copies used `os.replace`, which replaces. | Medium | Atomic no-replace move (hardlink then unlink), with a checked fallback for drives without hardlinks. | DS-2 |
| G-5 | Only inbox passes took a lock. Placing or undo from the command line could run at the same time as the panel or the background job. | Medium | One library lock for placing, inbox passes and undo. | FR-APPLY-5, DS-6 |
| G-6 | Panel accepted any JSON for settings (a string for workers made later steps crash), any body size, and any content type. | Medium | Settings validation, 2 MB limit, JSON only. | SEC-4, SEC-5 |
| G-7 | No framing or content-type protections on the panel page. | Medium | CSP with `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`, `Referrer-Policy`. | SEC-6 |
| G-8 | `~/.pettagam/` (every file name the owner has) was created with default permissions. | Low | Mode 0700. | PRIV-3 |
| G-9 | The background log grew without limit. | Low | Rotated past 5 MB. | FR-SERVICE-4 |
| G-10 | Copies could only be sample-checked; there was no way to prove every copy. | Low | `verify --full`. | FR-VERIFY-2 |
| G-11 | Nothing warned that Ubuntu can't write the APFS drive the library is being built on, which affects the planned move. | Medium (planning) | Health check reports the drive format; README explains the move. | FR-DOCTOR-2, PLAT-6 |
| G-15 | Packaging: version defined twice, no metadata (readme, classifiers, URLs), optional extras without upper bounds, no `all` extra, `build` missing from dev, CI not testing 3.13, the extras, or the built wheel. | Low | `pyproject.toml` and CI updated. | MAINT-1, MAINT-3, MAINT-4 |
| G-16 | No written requirements and no traceability. Four requirements had no test (review folder, health check changing nothing, background priority, how much of a text file tagging sends). | Medium | This document, `tests/test_requirements.py`, and tests for those four. | MAINT-2 |

### 9.2 Resolved after the audit

| Gap | Decision or fix | Requirement |
| --- | --- | --- |
| G-18 | Python 3.9 had reached its end of life. The owner chose 3.13 as the minimum (2026-10-22). `requires-python = ">=3.13"`, CI tests 3.13 and 3.14, the setup scripts find 3.13 or point to python.org (Mac) or the deadsnakes PPA (Ubuntu 24.04), and Pettagam runs from its own `.venv`. | PLAT-7, MAINT-3 |

### 9.3 Still open

| Gap | Finding | Recommendation | Owner decision needed? |
| --- | --- | --- | --- |
| G-12 | Performance targets (PERF-1, 2, 4) have never been measured on the N300 Pro. | Time a dry run and placement of a real subset on the Mac and record the numbers here. | No |
| G-13 | Network mode is plain HTTP: the token crosses the home network unencrypted. | Use the panel over an SSH tunnel (`ssh -L 8484:localhost:8484 server`) instead of `--host 0.0.0.0`, or put it behind a TLS reverse proxy. | No, but note it before using network mode |
| G-14 | The panel has not been checked for keyboard-only and screen-reader use. | Audit against WCAG 2.2 AA when the panel next changes. | No |
| G-17 | No licence file. Without one, nobody else may legally use or change the code. | Choose a licence (MIT is the usual choice for a personal tool), or keep the repository private. | Yes |
| G-19 | The catalog has no schema version number; upgrades rely on adding missing columns. | Add `PRAGMA user_version` with the first change that needs more than new columns. | No |
| G-20 | No linting or type checking in CI. | Add `ruff` (and optionally `mypy`) as a CI step. | No |

## 10. Verification and change control

- `python3 -m pytest` runs every automated check, including `tests/test_requirements.py`, which fails if this document names a test that doesn't exist.
- A pull request that adds or changes behaviour shall update this document (add or change the requirement and its "Verified by"), add or change the tests, and add a line to `CHANGELOG.md`.
- Requirement IDs are never reused. A dropped requirement is struck through with the version that dropped it.
