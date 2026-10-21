import json
import os
import struct
import time

import pytest

from pettagam import cli
from pettagam.apply import apply
from pettagam.db import connect
from pettagam.inbox import DUPES, sort_inbox
from pettagam.rules import RuleError, STARTER, load_rules, parse_rules, write_starter
from pettagam.sniff import sniff_bytes
from pettagam.tag import tag_library
from pettagam.views import build_views, parse_views
from test_pipeline import jpeg, png, quiet, run_all, write


def dated_jpeg(when="2021:07:04 18:30:00", make="Apple", model="iPhone 12"):
    """JPEG whose IFD0 has Make, Model and DateTime."""
    vals = [(0x010F, make), (0x0110, model), (0x0132, when)]
    base = 8 + 2 + 12 * len(vals) + 4
    entries, data = b"", b""
    for tag, v in vals:
        raw = v.encode() + b"\x00"
        entries += struct.pack("<HHII", tag, 2, len(raw), base + len(data))
        data += raw
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(vals)) + entries + b"\x00" * 4 + data
    app1 = b"Exif\x00\x00" + tiff
    sof = struct.pack(">BHHB", 8, 3024, 4032, 3) + b"\x00" * 9
    return (b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xc0" + struct.pack(">H", len(sof) + 2)
            + sof + b"\xff\xda" + b"\x00" * 2000)


def rules_file(lib, rules):
    p = lib / "_meta" / "rules.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"rules": rules}))
    return str(p)


def age(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


# --- rules ---------------------------------------------------------------

def test_exif_capture_time_is_read():
    s = sniff_bytes(dated_jpeg())
    assert s.camera == "Apple iPhone 12"
    assert time.gmtime(s.taken)[:3] == (2021, 7, 4)


def test_rules_validate_with_clear_messages():
    parse_rules(STARTER)
    bad = [
        ({"if": {"colour": "red"}, "then": "x"}, "unknown condition 'colour'"),
        ({"if": {"ext": "pdf"}, "then": "docs/{nope}"}, "unknown placeholder"),
        ({"if": {"ext": "pdf"}, "then": "../outside"}, "inside the library"),
        ({"if": {"size_over": "lots"}, "then": "x"}, "not a size"),
        ({"if": {"older_than": "ages"}, "then": "x"}, "not an age"),
        ({"if": {"year": "twenty"}, "then": "x"}, "not a year"),
        ({"if": {}, "then": "x"}, "non-empty"),
    ]
    for rule, msg in bad:
        with pytest.raises(RuleError, match=msg):
            parse_rules({"rules": [rule]})


def test_rules_steer_the_dry_run(tmp_path):
    src, lib = tmp_path / "old", tmp_path / "library"
    s = str(src)
    write(s, "phone/IMG_0001.JPG", dated_jpeg())
    write(s, "papers/Tax return 2021.pdf", b"%PDF-1.7 tax")
    write(s, "papers/Invoice_7.pdf", b"%PDF-1.7 invoice")
    write(s, "papers/notes.txt", b"hello there")
    write(s, "big/movie.mov", b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 3000)
    rules = load_rules(rules_file(lib, [
        {"name": "Tax", "if": {"ext": "pdf", "name": ["*tax*", "*1099*"]}, "then": "documents/tax/{year}"},
        {"name": "iPhone", "if": {"camera": "iphone"}, "then": "images/photos/iphone/{year}/{month}"},
        {"name": "Not invoices", "if": {"category": "documents", "not": {"subtype": "invoices"},
                                         "any": [{"ext": "txt"}, {"name_regex": "^zzz"}]},
         "then": "documents/notes"},
        {"name": "Big", "if": {"category": "videos", "size_over": "2KB"}, "then": "videos/large"},
        {"name": "Switched off", "if": {"ext": "pdf"}, "then": "nowhere", "off": True},
    ]))
    con = connect(str(tmp_path / "p.db"))
    from pettagam.scan import scan
    from pettagam.plan import plan
    scan(con, s, progress=quiet)
    age(src / "papers" / "Tax return 2021.pdf", 0)
    plan(con, str(lib), rules=rules, progress=quiet)
    d = dict(con.execute("SELECT path, dest FROM files").fetchall())
    j = os.path.join
    year = time.localtime().tm_year
    assert d[j("phone", "IMG_0001.JPG")] == "images/photos/iphone/2021/07/IMG_0001.jpg"
    assert d[j("papers", "Tax return 2021.pdf")] == f"documents/tax/{year}/Tax return 2021.pdf"
    assert d[j("papers", "Invoice_7.pdf")] == "documents/invoices/Invoice_7.pdf"
    assert d[j("papers", "notes.txt")] == "documents/notes/notes.txt"
    assert d[j("big", "movie.mov")] == "videos/large/movie.mov"
    rule_of = dict(con.execute("SELECT path, rule FROM files").fetchall())
    assert rule_of[j("phone", "IMG_0001.JPG")] == "iPhone" and rule_of[j("papers", "Invoice_7.pdf")] is None


def test_rules_command_init_and_count(tmp_path, capsys):
    src, lib, dbp = tmp_path / "old", tmp_path / "library", tmp_path / "p.db"
    write(str(src), "a/report.txt", b"words")
    run_all(src, lib, dbp, tmp_path)
    assert cli.main(["--db", str(dbp), "rules", "--init"]) == 0
    assert (lib / "_meta" / "rules.json").exists()
    assert write_starter(str(lib / "_meta" / "rules.json")) is False  # never overwritten
    rules_file(lib, [{"name": "Text", "if": {"ext": "txt"}, "then": "documents/text"}])
    assert cli.main(["--db", str(dbp), "rules"]) == 0
    assert "Text: 1 files" in capsys.readouterr().out
    rules_file(lib, [{"name": "Bad", "if": {"wat": 1}, "then": "x"}])
    assert cli.main(["--db", str(dbp), "rules"]) == 3


# --- inbox ---------------------------------------------------------------

@pytest.fixture
def placed(tmp_path):
    src, lib, dbp = tmp_path / "old", tmp_path / "library", tmp_path / "p.db"
    write(str(src), "2019/IMG_0001.JPG", jpeg("Apple", "iPhone 12"))
    write(str(src), "docs/report.pdf", b"%PDF-1.7 old report")
    con = run_all(src, lib, dbp, tmp_path)
    apply(con, progress=quiet)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return con, src, lib, inbox


def test_inbox_files_new_files_and_empties_itself(placed):
    con, src, lib, inbox = placed
    rules = parse_rules({"rules": [{"name": "Tax", "if": {"name": "*tax*"}, "then": "documents/tax"}]})
    for rel, data in [("Downloads/tax form.pdf", b"%PDF-1.7 tax"), ("Screenshot 1.png", png(2880, 1800)),
                      ("report.pdf", b"%PDF-1.7 a different report"), ("IMG_0001.JPG", jpeg("Apple", "iPhone 12")),
                      (".DS_Store", b"junk"), ("half.crdownload", b"partial")]:
        age(write(str(inbox), rel, data), 1)
    write(str(inbox), "still-copying.pdf", b"%PDF-1.7 new")  # modified just now

    preview = sort_inbox(con, str(inbox), rules=rules, dry_run=True, progress=quiet)
    assert preview["placed"] == 3 and (inbox / "report.pdf").exists()

    res = sort_inbox(con, str(inbox), rules=rules, progress=quiet)
    assert (res["placed"], res["duplicate"], res["waiting"]) == (3, 1, 1)
    assert (lib / "documents" / "tax" / "tax form.pdf").exists()
    assert len(list((lib / "images" / "screenshots").iterdir())) == 1
    # A different file with a name the library already uses gets a suffix, never an overwrite.
    assert (lib / "documents" / "reports" / "report__2.pdf").read_bytes() == b"%PDF-1.7 a different report"
    assert (lib / "documents" / "reports" / "report.pdf").read_bytes() == b"%PDF-1.7 old report"
    # The exact copy of a library photo is set aside, not deleted.
    assert (inbox / DUPES / "IMG_0001.JPG").exists()
    assert not (inbox / "Downloads").exists()
    assert (inbox / "still-copying.pdf").exists() and (inbox / ".DS_Store").exists()
    log = (lib / "_meta" / "inbox-log.csv").read_text()
    assert "tax form.pdf" in log and "duplicate" in log
    # The old folder is untouched, and verify still balances.
    from pettagam.verify import verify
    assert verify(con, rescan=True, progress=quiet)["ok"]
    # A second pass with nothing new does nothing.
    again = sort_inbox(con, str(inbox), rules=rules, progress=quiet)
    assert again["placed"] == 0 and again["duplicate"] == 0


def test_inbox_follows_split_folders(placed):
    con, _, lib, inbox = placed
    (lib / "documents" / "general" / "s").mkdir(parents=True)
    age(write(str(inbox), "story.txt", b"once upon a time"), 1)
    sort_inbox(con, str(inbox), progress=quiet)
    assert (lib / "documents" / "general" / "s" / "story.txt").exists()


def test_inbox_must_be_separate_from_old_files(placed):
    con, src, lib, _ = placed
    with pytest.raises(SystemExit):
        sort_inbox(con, str(src / "2019"), progress=quiet)
    with pytest.raises(SystemExit):
        sort_inbox(con, str(lib / "documents"), progress=quiet)


def test_inbox_command(placed, tmp_path):
    con, _, lib, inbox = placed
    age(write(str(inbox), "notes.txt", b"note"), 1)
    assert cli.main(["--db", str(tmp_path / "p.db"), "inbox", str(inbox)]) == 0
    assert (lib / "documents" / "general" / "notes.txt").exists()


# --- views ---------------------------------------------------------------

def test_views_are_hardlinks_and_rebuild_cleanly(tmp_path):
    src, lib, dbp = tmp_path / "old", tmp_path / "library", tmp_path / "p.db"
    write(str(src), "a/IMG_0001.JPG", dated_jpeg("2020:12:31 23:00:00"))
    write(str(src), "a/IMG_0002.JPG", dated_jpeg("2022:01:15 10:00:00", "Canon", "EOS R6"))
    write(str(src), "b/IMG_0001.jpg", dated_jpeg("2020:06:01 09:00:00", "Canon", "EOS R6"))
    write(str(src), "notes.txt", b"hi")
    con = run_all(src, lib, dbp, tmp_path)
    apply(con, progress=quiet)
    con.execute("UPDATE files SET tags='beach,family' WHERE path=?", (os.path.join("a", "IMG_0001.JPG"),))
    counts = build_views(con, str(lib), progress=quiet)
    v = lib / "views"
    photo = lib / "images" / "photos" / "IMG_0001.jpg"
    assert os.stat(v / "by-year" / "2020" / "12" / "IMG_0001.jpg").st_ino == os.stat(photo).st_ino
    assert (v / "by-year" / "2020" / "06" / "IMG_0001__2.jpg").exists()
    assert len(list((v / "by-camera" / "Canon EOS R6").iterdir())) == 2
    assert (v / "by-tag" / "beach" / "IMG_0001.jpg").exists()
    assert counts["Recent"] == 1  # notes.txt; the photos' capture dates are old
    # Rebuilding removes stale links but never library files or files that aren't ours.
    (v / "mine.txt").write_text("keep me")
    con.execute("UPDATE files SET tags=NULL")
    build_views(con, str(lib), progress=quiet)
    assert not (v / "by-tag").exists()
    assert (v / "mine.txt").exists() and photo.exists()
    from pettagam.verify import verify
    assert verify(con, progress=quiet)["ok"]


def test_custom_views_file(tmp_path):
    views = parse_views({"views": [{"name": "Docs by year", "if": {"category": "documents"},
                                    "then": "documents-by-year/{year}"}]})
    assert views[0].then == "documents-by-year/{year}"
    with pytest.raises(RuleError):
        parse_views({"views": [{"if": {"ext": "pdf"}, "then": "../x"}]})


# --- tagging -------------------------------------------------------------

class FakeTagger:
    model = "fake"

    def __init__(self):
        self.calls = []

    def __call__(self, blocks, what, name):
        self.calls.append((blocks[0]["type"], what, name))
        if name == "broken.png":
            raise ValueError("boom")
        return {"tags": ["Beach", "family ", "beach", "sunset"], "description": "A family at the beach."}


def test_tagging_is_resumable_and_feeds_rules(tmp_path):
    src, lib, dbp = tmp_path / "old", tmp_path / "library", tmp_path / "p.db"
    write(str(src), "IMG_0001.JPG", jpeg("Apple", "iPhone 12", pad=2000))
    write(str(src), "scan.pdf", b"%PDF-1.7 page")
    write(str(src), "broken.png", png(2000, 1500))
    write(str(src), "song.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x10TIT2\x00\x00\x00\x05\x00\x00\x00Song" + b"\x00" * 50)
    con = run_all(src, lib, dbp, tmp_path)
    apply(con, progress=quiet)
    fake = FakeTagger()
    res = tag_library(con, str(lib), tagger=fake, workers=2, progress=quiet)
    assert res == {"tagged": 2, "skipped": 0, "errors": 1}
    assert sorted(c[0] for c in fake.calls) == ["document", "image", "image"]  # mp3 is not sent
    tags = dict(con.execute("SELECT name, tags FROM files").fetchall())
    assert tags["IMG_0001.JPG"] == "beach,family,sunset"
    # Only untagged files go again.
    fake.calls.clear()
    tag_library(con, str(lib), tagger=fake, progress=quiet)
    assert [c[2] for c in fake.calls] == ["broken.png"]
    # Tags survive a fresh scan, and rules can use them.
    rules = parse_rules({"rules": [{"name": "Beach", "if": {"tags": "beach"}, "then": "images/beach"}]})
    from pettagam.hashing import find_duplicates
    from pettagam.plan import plan
    from pettagam.scan import scan
    scan(con, str(src), progress=quiet)
    find_duplicates(con, progress=quiet)
    plan(con, str(lib), rules=rules, progress=quiet)
    assert con.execute("SELECT dest FROM files WHERE name='IMG_0001.JPG'").fetchone()[0] == "images/beach/IMG_0001.jpg"


def test_old_catalog_gets_new_columns(tmp_path):
    from pettagam import db
    p = tmp_path / "old.db"
    old_schema = db.SCHEMA
    for col in ("taken", "rule", "tags", "description"):
        old_schema = "\n".join(line for line in old_schema.split("\n") if not line.strip().startswith(col + " "))
    old_schema = old_schema.replace("error       TEXT,\n);", "error       TEXT\n);")
    import sqlite3
    c = sqlite3.connect(str(p))
    c.executescript(old_schema.split("CREATE TABLE IF NOT EXISTS inbox")[0])
    c.close()
    cols = {r[1] for r in connect(str(p)).execute("PRAGMA table_info(files)")}
    assert {"taken", "rule", "tags", "description"} <= cols
