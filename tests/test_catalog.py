"""The catalog's layout version and upgrades (docs/REQUIREMENTS.md, DS-9)."""
import os
import sqlite3

import pytest

from pettagam import db
from pettagam.maintenance import relocate

# A catalog as milestone 1 wrote it: no version, and none of the later columns or tables.
MILESTONE_1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL, size INTEGER NOT NULL,
    mtime REAL NOT NULL, dev INTEGER, ino INTEGER, ext TEXT, kind TEXT, width INTEGER, height INTEGER, camera TEXT,
    has_exif INTEGER DEFAULT 0, has_tags INTEGER DEFAULT 0, junk TEXT, quick_hash TEXT, full_hash TEXT,
    dup_of INTEGER, category TEXT, subtype TEXT, confidence REAL, reason TEXT, folder TEXT, dest TEXT,
    action TEXT, status TEXT, method TEXT, error TEXT);
INSERT INTO meta VALUES ('library', '/Volumes/N300/Pettagam');
INSERT INTO files (path, name, size, mtime, dest, action, status) VALUES ('a/x.jpg', 'x.jpg', 5, 1, 'images/x.jpg',
    'place', 'done');
"""


def test_new_catalog_gets_the_current_version(tmp_path):
    con = db.connect(str(tmp_path / "p.db"))
    assert db.schema_version(con) == db.SCHEMA_VERSION
    assert not list(tmp_path.glob("*.bak"))  # nothing to back up


def test_old_catalog_is_upgraded_with_a_backup_and_keeps_its_data(tmp_path):
    path = tmp_path / "p.db"
    raw = sqlite3.connect(path)
    raw.executescript(MILESTONE_1)
    raw.close()

    con = db.connect(str(path))
    assert db.schema_version(con) == db.SCHEMA_VERSION
    cols = {r[1] for r in con.execute("PRAGMA table_info(files)")}
    assert {"taken", "rule", "tags", "description"} <= cols
    assert "files_dup" in {r[1] for r in con.execute("PRAGMA index_list(files)")}
    assert con.execute("SELECT dest FROM files").fetchone()[0] == "images/x.jpg"
    assert db.get_meta(con, "library") == "/Volumes/N300/Pettagam"
    con.execute("SELECT * FROM inbox").fetchall()  # tables added since exist too

    backup = sqlite3.connect(tmp_path / "p.db.v0.bak")
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "tags" not in {r[1] for r in backup.execute("PRAGMA table_info(files)")}  # as it was before
    backup.close()
    con.close()
    db.connect(str(path)).close()  # opening again changes nothing more
    assert [p.name for p in tmp_path.glob("*.bak")] == ["p.db.v0.bak"]


def test_catalog_from_a_newer_pettagam_is_refused_untouched(tmp_path):
    path = tmp_path / "p.db"
    raw = sqlite3.connect(path)
    raw.executescript(MILESTONE_1)
    raw.execute(f"PRAGMA user_version={db.SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()
    before = path.read_bytes()
    with pytest.raises(db.NewerCatalog, match="newer Pettagam"):
        db.connect(str(path))
    assert path.read_bytes() == before
    # The copy that travels with the drive is checked the same way when moving to another computer.
    with pytest.raises(SystemExit, match="newer Pettagam"):
        relocate(str(tmp_path / "new.db"), catalog_from=str(path), progress=lambda *a: None)
    assert not (tmp_path / "new.db").exists()


def test_every_version_has_an_upgrade_step():
    assert sorted(db.MIGRATIONS) == list(range(1, db.SCHEMA_VERSION + 1))
    assert all(os.path.basename(f.__code__.co_filename) == "db.py" for f in db.MIGRATIONS.values())


# --- the word-search index (PERF-4, PERF-7) ---------------------------------------

def _catalog(tmp_path):
    con = db.connect(str(tmp_path / "s.db"))
    rows = [("a/1.jpg", "images/2019/Beach day.jpg", "beach,sunset", "Kids on the beach", "Apple iPhone 12"),
            ("a/2.jpg", "images/2020/IMG_2041.jpg", "receipt", None, None),
            ("a/3.pdf", "documents/invoices/Invoice \"Q1\".pdf", None, "Invoice from O'Brien", None),
            ("a/4.mp3", "audio/voice/memo.mp3", None, None, None)]
    for i, (path, dest, tags, desc, cam) in enumerate(rows):
        con.execute("INSERT INTO files(path, name, size, mtime, dest, tags, description, camera, action, status) "
                    "VALUES (?,?,1,?,?,?,?,?, 'place', 'done')", (path, path, i, dest, tags, desc, cam))
    con.execute("INSERT INTO inbox(at, original, dest, action, mtime, tags) VALUES (0, '/in/x', "
                "'images/2021/beach ball.jpg', 'placed', 9, 'toy')")
    con.commit()
    return con


QUERIES = ["", "beach", "BEACH", "be", "2019", "img_2041", "iphone 12", 'q1"', "o'brien", "invoice q1", "zzz", "toy",
           "ach", "voice"]


def test_word_search_index_finds_what_like_finds(tmp_path):  # PERF-7
    from pettagam import library
    con = _catalog(tmp_path)
    assert db.has_search(con)
    with_index = {q: [r["dest"] for r in library.search(con, q)["items"]] for q in QUERIES}
    con.execute("DROP TABLE search")  # what a Python whose SQLite has no trigram index does
    assert not db.has_search(con)
    without = {q: [r["dest"] for r in library.search(con, q)["items"]] for q in QUERIES}
    for q in QUERIES:
        if q.isascii():  # LIKE only ignores case for ASCII; the index does it for every language
            assert with_index[q] == without[q], q
    assert with_index["beach"] == ["images/2021/beach ball.jpg", "images/2019/Beach day.jpg"]
    assert with_index["voice"] == ["audio/voice/memo.mp3", 'documents/invoices/Invoice "Q1".pdf']


def test_word_search_index_follows_every_change(tmp_path):  # PERF-7
    from pettagam import library
    con = _catalog(tmp_path)

    def found(q):
        return [r["dest"] for r in library.search(con, q)["items"]]
    con.execute("UPDATE files SET tags='passport' WHERE path='a/4.mp3'")  # tagging
    assert found("passport") == ["audio/voice/memo.mp3"]
    con.execute("UPDATE files SET status=NULL WHERE path='a/4.mp3'")  # undo takes it out of the library
    assert found("passport") == [] and found("memo") == []
    con.execute("UPDATE files SET status='done' WHERE path='a/4.mp3'")
    assert found("memo") == ["audio/voice/memo.mp3"]
    con.execute("DELETE FROM files")  # a fresh scan
    con.execute("UPDATE inbox SET action='error'")
    assert found("beach") == [] and con.execute("SELECT COUNT(*) FROM search").fetchone()[0] == 0
    con.execute("INSERT INTO inbox(at, original, dest, action) VALUES (0, '/in/y', 'images/new/kite.jpg', 'placed')")
    assert found("kite") == ["images/new/kite.jpg"]


def test_old_catalog_gets_the_search_index(tmp_path):  # PERF-7
    path = tmp_path / "p.db"
    raw = sqlite3.connect(path)
    raw.executescript(MILESTONE_1)
    raw.close()
    con = db.connect(str(path))
    assert db.has_search(con)
    assert con.execute("SELECT rowid FROM search WHERE search MATCH '\"x.jpg\"'").fetchone()[0] == 2


def test_health_check_reports_the_search_index(tmp_path):  # PERF-7
    from pettagam.doctor import run_checks
    con = _catalog(tmp_path)
    check = {c["name"]: c for c in run_checks(con)}["Word search"]
    assert check["status"] == "ok"
    con.execute("DROP TABLE search")
    check = {c["name"]: c for c in run_checks(con)}["Word search"]
    assert check["status"] == "warn" and "python.org" in check["fix"]


def test_new_catalog_opened_from_many_threads_at_once(tmp_path):  # DS-9
    import threading
    errors = []
    for trial in range(15):  # the race needed a few tries to show before the fix
        path = str(tmp_path / f"p{trial}.db")
        start = threading.Barrier(6)

        def open_and_write(path=path, start=start):
            start.wait()
            try:
                con = db.connect(path)
                db.set_meta(con, f"t{threading.get_ident()}", 1)
            except Exception as e:  # "database is locked" before the fix
                errors.append(repr(e))
        threads = [threading.Thread(target=open_and_write) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert not errors, errors[:3]
