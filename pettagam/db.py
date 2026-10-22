"""SQLite catalog: one row per source file, plus key/value settings."""

import os
import sqlite3
import time

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
    _wal(con)
    con.execute("PRAGMA synchronous=NORMAL")
    if version < SCHEMA_VERSION:
        _set_up(con, path)
    ensure_search(con)
    return con


def _wal(con) -> None:
    """Switch a new catalog to write-ahead logging. Switching ignores the busy timeout, so when another
    thread is switching or creating the same new catalog at that moment, wait a little and try again."""
    for _ in range(200):
        try:
            if con.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
                return
            con.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                raise
            time.sleep(0.05)
    con.execute("PRAGMA journal_mode=WAL")  # one last time, letting the error through


def _set_up(con, path: str) -> None:
    """Create or upgrade the catalog. The panel can open a new catalog from several threads at once, so this
    takes the write lock first (BEGIN IMMEDIATE waits for it) and then looks again at what is there."""
    has_data = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='files'").fetchone()
    if has_data:
        _backup(con, f"{path}.v{schema_version(con)}.bak")
    con.execute("BEGIN IMMEDIATE")
    try:
        version = schema_version(con)
        if version < SCHEMA_VERSION:  # nobody else did it while we waited
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='files'").fetchone():
                for v in range(version + 1, SCHEMA_VERSION + 1):
                    MIGRATIONS[v](con)
            for stmt in _statements(SCHEMA):
                con.execute(stmt)
            con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        con.commit()
    except BaseException:
        con.rollback()
        raise


def _statements(script: str) -> list:
    out, cur = [], ""
    for line in script.splitlines(keepends=True):
        cur += line
        if sqlite3.complete_statement(cur):
            out.append(cur.strip())
            cur = ""
    return out


# Word search for Explore and `find`: an SQLite full-text index with the trigram tokenizer, which finds any
# piece of a word of 3 or more letters (like LIKE '%word%', ignoring case) without reading every row.
# Library files only: placed files have rowid 2*id, inbox files 2*id+1. Triggers keep it in step.
# Python builds whose SQLite lacks FTS5 or trigram (older than 3.34) get no index; search then uses LIKE.
SEARCH_TEXT = "COALESCE({p}.dest,'')||char(10)||COALESCE({p}.tags,'')||char(10)||COALESCE({p}.description,'')" \
              "||char(10)||COALESCE({p}.camera,'')"
SEARCH = [  # run one by one, in one transaction (see ensure_search)
    """CREATE VIRTUAL TABLE search USING fts5(text, tokenize='trigram')""",
    f"""CREATE TRIGGER files_search_ins AFTER INSERT ON files WHEN new.action='place' AND new.status='done' BEGIN
    INSERT INTO search(rowid, text) VALUES (2*new.id, {SEARCH_TEXT.format(p="new")}); END""",
    """CREATE TRIGGER files_search_del AFTER DELETE ON files BEGIN DELETE FROM search WHERE rowid=2*old.id; END""",
    f"""CREATE TRIGGER files_search_upd AFTER UPDATE OF dest, tags, description, camera, action, status ON files BEGIN
    DELETE FROM search WHERE rowid=2*old.id;
    INSERT INTO search(rowid, text) SELECT 2*new.id, {SEARCH_TEXT.format(p="new")}
        WHERE new.action='place' AND new.status='done'; END""",
    f"""CREATE TRIGGER inbox_search_ins AFTER INSERT ON inbox WHEN new.action='placed' BEGIN
    INSERT INTO search(rowid, text) VALUES (2*new.id+1, {SEARCH_TEXT.format(p="new")}); END""",
    """CREATE TRIGGER inbox_search_del AFTER DELETE ON inbox BEGIN DELETE FROM search WHERE rowid=2*old.id+1; END""",
    f"""CREATE TRIGGER inbox_search_upd AFTER UPDATE OF dest, tags, description, camera, action ON inbox BEGIN
    DELETE FROM search WHERE rowid=2*old.id+1;
    INSERT INTO search(rowid, text) SELECT 2*new.id+1, {SEARCH_TEXT.format(p="new")} WHERE new.action='placed'; END""",
    f"""INSERT INTO search(rowid, text) SELECT 2*f.id, {SEARCH_TEXT.format(p="f")} FROM files f
    WHERE f.action='place' AND f.status='done'""",
    f"""INSERT INTO search(rowid, text) SELECT 2*i.id+1, {SEARCH_TEXT.format(p="i")} FROM inbox i WHERE i.action='placed'""",
]


def has_search(con) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search'").fetchone())


def ensure_search(con) -> None:
    """Build the word-search index once, from what is already in the catalog."""
    if has_search(con):
        return
    try:
        con.execute("CREATE VIRTUAL TABLE temp.probe USING fts5(x, tokenize='trigram')")
        con.execute("DROP TABLE temp.probe")
    except sqlite3.OperationalError:
        return  # this SQLite can't; search falls back to LIKE
    con.commit()
    con.execute("BEGIN IMMEDIATE")  # one writer: the panel may open the catalog from two threads at once
    try:
        if not has_search(con):
            for stmt in SEARCH:
                con.execute(stmt)
        con.commit()
    except BaseException:
        con.rollback()
        raise


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
