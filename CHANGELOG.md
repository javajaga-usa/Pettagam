# Changelog

All notable changes to Pettagam. Versions follow [semantic versioning](https://semver.org/); dates are UTC.

## [Unreleased]

## [0.3.0] - 2026-10-21

### Added
- **Health check** (`pettagam doctor`, and a card in the panel): drives reachable, same drive and working hardlinks, free space against what copying needs, catalog location, inbox, background job and AI-tagging readiness, each with what to do.
- **Automatic inbox sorting in the background** (`pettagam service on|off|status`, and a switch in the panel): a macOS LaunchAgent or a systemd user timer and path watch runs an inbox pass when files arrive and every few minutes. Logged to `~/.pettagam/inbox-service.log`.
- **Undo** (`pettagam undo [--dry-run]`, and a button in the panel): takes placed files back out of the library and returns inbox files to the inbox, never removing the only copy of anything.
- **Catalog copy in the library** (`<library>/_meta/catalog.db`), refreshed after placing, inbox passes, tagging and undo.
- **Relocate** (`pettagam relocate --catalog-from ... --source ... --library ...`) for when the drive is mounted somewhere else, such as after the move to Ubuntu.
- **Setup Pettagam.command** builds a Pettagam app with an icon in `~/Applications` on the Mac itself, so Gatekeeper doesn't block it, with optional AI tagging in a `.venv`. **Uninstall Pettagam.command** removes the app, background job and `.venv`, asks before removing settings, and never touches your files. `launchers/setup-ubuntu.sh` adds a `pettagam` command and menu entry on Ubuntu.
- A release workflow: pushing a `v*` tag tests, builds and publishes a GitHub release from this changelog.

### Changed
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

[Unreleased]: https://github.com/javajaga-usa/Pettagam/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/javajaga-usa/Pettagam/releases/tag/v0.3.0
[0.2.0]: https://github.com/javajaga-usa/Pettagam/releases/tag/v0.2.0
[0.1.0]: https://github.com/javajaga-usa/Pettagam/releases/tag/v0.1.0
