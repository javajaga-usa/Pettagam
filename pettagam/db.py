"""SQLite catalog: one row per source file, plus key/value settings."""

import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,   -- relative to the source root
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    dev         INTEGER,
    ino         INTEGER,
    ext         TEXT,                   -- normalized extension used for placement
    kind        TEXT,                   -- format detected from content
    width       INTEGER,
    height      INTEGER,
    camera      TEXT,
    has_exif    INTEGER DEFAULT 0,
    has_tags    INTEGER DEFAULT 0,
    taken       REAL,                   -- capture time from EXIF, when present
    junk        TEXT,                   -- reason when skipped as junk
    quick_hash  TEXT,
    full_hash   TEXT,
    dup_of      INTEGER,                -- id of the canonical copy
    category    TEXT,
    subtype     TEXT,
    confidence  REAL,
    reason      TEXT,
    rule        TEXT,                   -- name of your rule that chose the folder, if any
    folder      TEXT,                   -- planned folder before any size split, e.g. images/photos
    dest        TEXT,                   -- relative to the library root
    action      TEXT,                   -- place | duplicate | skip
    status      TEXT,                   -- NULL | done | error
    method      TEXT,                   -- link | copy
    error       TEXT,
    tags        TEXT,                   -- comma-separated descriptive tags (pettagam tag)
    description TEXT                    -- one-line description from pettagam tag
);
CREATE INDEX IF NOT EXISTS files_size ON files(size);
CREATE INDEX IF NOT EXISTS files_hash ON files(full_hash);
CREATE INDEX IF NOT EXISTS files_dup ON files(dup_of);
CREATE INDEX IF NOT EXISTS files_recent ON files(COALESCE(taken, mtime) DESC, dest)
    WHERE action='place' AND status='done';
CREATE TABLE IF NOT EXISTS inbox (
    id          INTEGER PRIMARY KEY,
    at          REAL NOT NULL,          -- when it was sorted
    original    TEXT NOT NULL,          -- full path in the inbox
    dest        TEXT,                   -- relative to the library root
    action      TEXT NOT NULL,          -- placed | duplicate | error
    size        INTEGER,
    full_hash   TEXT,
    category    TEXT,
    subtype     TEXT,
    confidence  REAL,
    reason      TEXT,
    rule        TEXT,
    taken       REAL,
    mtime       REAL,
    camera      TEXT,
    width       INTEGER,
    height      INTEGER,
    tags        TEXT,
    description TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS inbox_size ON inbox(size);
CREATE INDEX IF NOT EXISTS inbox_recent ON inbox(COALESCE(taken, mtime) DESC, dest) WHERE action='placed';
"""

# The catalog's layout version, kept in SQLite's PRAGMA user_version. A catalog without one (0) is from
# before versioning. Each step below takes a catalog from the version before it to its own number.
SCHEMA_VERSION = 2


def _v1(con):
    """Columns added after milestone 1."""
    have = {r[1] for r in con.execute("PRAGMA table_info(files)")}
    for col, typ in {"taken": "REAL", "rule": "TEXT", "tags": "TEXT", "description": "TEXT"}.items():
        if col not in have:
            con.execute(f"ALTER TABLE files ADD COLUMN {col} {typ}")


def _v2(con):
    """Indexes for big libraries. Duplicates looks up every copy of a file by dup_of, and Explore lists the
    newest files first; without these both read the whole table."""
    con.execute("CREATE INDEX IF NOT EXISTS files_dup ON files(dup_of)")
    con.execute("CREATE INDEX IF NOT EXISTS files_recent ON files(COALESCE(taken, mtime) DESC, dest) "
                "WHERE action='place' AND status='done'")
    # The inbox table itself may not exist yet in a very old catalog; SCHEMA adds it with its indexes.


MIGRATIONS = {1: _v1, 2: _v2}


class NewerCatalog(SystemExit):
    pass


def schema_version(con) -> int:
    return con.execute("PRAGMA user_version").fetchone()[0]


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    version = schema_version(con)
    if version > SCHEMA_VERSION:
        con.close()
        raise NewerCatalog(f"{path} was written by a newer Pettagam (catalog version {version}; this one knows "
                           f"up to {SCHEMA_VERSION}). Update Pettagam to use it; the catalog was not changed.")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    if version < SCHEMA_VERSION:
        has_data = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='files'").fetchone()
        if has_data:
            _backup(con, f"{path}.v{version}.bak")
            with con:  # all steps or none
                for v in range(version + 1, SCHEMA_VERSION + 1):
                    MIGRATIONS[v](con)
                con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    con.executescript(SCHEMA)
    if version < SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        con.commit()
    return con


def _backup(con, dest: str) -> None:
    """A copy of the catalog as it was before an upgrade, in case the upgrade needs undoing."""
    if os.path.exists(dest):
        return
    out = sqlite3.connect(dest)
    try:
        con.backup(out)
    finally:
        out.close()


def get_meta(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))
    con.commit()
