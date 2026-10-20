"""SQLite catalog: one row per source file, plus key/value settings."""
from __future__ import annotations

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
    junk        TEXT,                   -- reason when skipped as junk
    quick_hash  TEXT,
    full_hash   TEXT,
    dup_of      INTEGER,                -- id of the canonical copy
    category    TEXT,
    subtype     TEXT,
    confidence  REAL,
    reason      TEXT,
    folder      TEXT,                   -- planned folder before any size split, e.g. images/photos
    dest        TEXT,                   -- relative to the library root
    action      TEXT,                   -- place | duplicate | skip
    status      TEXT,                   -- NULL | done | error
    method      TEXT,                   -- link | copy
    error       TEXT
);
CREATE INDEX IF NOT EXISTS files_size ON files(size);
CREATE INDEX IF NOT EXISTS files_hash ON files(full_hash);
"""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def get_meta(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))
    con.commit()
