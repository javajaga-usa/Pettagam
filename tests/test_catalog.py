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
