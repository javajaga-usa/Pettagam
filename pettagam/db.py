"""SQLite catalog: one row per source file, plus key/value settings."""

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
"""

# Columns added after milestone 1; older catalogs get them on first open.
ADDED = {"files": {"taken": "REAL", "rule": "TEXT", "tags": "TEXT", "description": "TEXT"}}


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    for table, cols in ADDED.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue  # created below with every column
        for col, typ in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    con.executescript(SCHEMA)
    return con


def get_meta(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))
    con.commit()
