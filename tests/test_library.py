import csv
import json
import os

import pytest

from pettagam import cli
from pettagam.apply import apply
from pettagam.doctor import run_checks
from pettagam.fixity import check, summary
from pettagam.library import duplicate_groups, search, stats

from test_pipeline import jpeg, quiet, run_all, write


@pytest.fixture
def placed(tmp_path):
    src, lib, dbp = tmp_path / "old", tmp_path / "library", tmp_path / "p.db"
    write(str(src), "a/IMG_0001.JPG", jpeg("Apple", "iPhone 12", pad=500))
    write(str(src), "b/IMG_0001 copy.JPG", jpeg("Apple", "iPhone 12", pad=500))
    write(str(src), "c/IMG_0001 (2).JPG", jpeg("Apple", "iPhone 12", pad=500))
    write(str(src), "docs/report.pdf", b"%PDF-1.7 report " * 100)
    write(str(src), "docs/notes.txt", b"notes")
    con = run_all(src, lib, dbp, tmp_path)
    apply(con, progress=quiet)
    con.execute("UPDATE files SET tags='beach,family' WHERE name='IMG_0001.JPG'")
    con.commit()
    return con, src, lib, dbp


def test_search_and_find_command(placed, capsys):
    con, src, lib, dbp = placed
    assert search(con, "report")["total"] == 1
    assert search(con, tag="beach")["total"] == 1
    assert search(con, category="images")["total"] == 1
    assert cli.main(["--db", str(dbp), "find", "--tag", "beach"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(str(lib)) and "[beach,family]" in out
    assert cli.main(["--db", str(dbp), "find", "nothing-like-this"]) == 1
    assert cli.main(["--db", str(dbp), "find", "notes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["name"] == "notes.txt"


def test_stats_and_duplicates(placed, tmp_path, capsys):
    con, src, lib, dbp = placed
    st = stats(con)
    assert st["files"] == 3 and st["duplicates"]["files"] == 2 and st["tagged"] == 1
    assert st["tags"][0]["tag"] in ("beach", "family")
    assert cli.main(["--db", str(dbp), "stats"]) == 0
    assert "By type" in capsys.readouterr().out
    groups = duplicate_groups(con)
    assert len(groups) == 1 and len(groups[0]["copies"]) == 2 and groups[0]["keep"].endswith(".JPG")
    out_csv = tmp_path / "dupes.csv"
    assert cli.main(["--db", str(dbp), "duplicates", "--csv", str(out_csv)]) == 0
    rows = list(csv.DictReader(out_csv.open()))
    assert [r["role"] for r in rows] == ["keep", "copy", "copy"]
    assert all(os.path.exists(r["path"]) for r in rows)  # read-only: nothing deleted


def test_fixity_finds_silent_damage_but_not_edits(placed):
    con, src, lib, dbp = placed
    first = check(con, progress=quiet)
    assert first["recorded"] == 3 and not first["damaged"]
    again = check(con, progress=quiet)
    assert again["ok"] == 3

    pdf = next(os.path.join(lib, r[0]) for r in con.execute("SELECT dest FROM files WHERE name='report.pdf'"))
    st = os.stat(pdf)
    with open(pdf, "r+b") as f:  # flip bytes without changing size or date: bit rot
        f.seek(10)
        f.write(b"XX")
    os.utime(pdf, ns=(st.st_atime_ns, st.st_mtime_ns))
    txt = next(os.path.join(lib, r[0]) for r in con.execute("SELECT dest FROM files WHERE name='notes.txt'"))
    with open(txt, "a") as f:  # a real edit: date changes
        f.write(" more")
    os.utime(txt, (st.st_atime + 100, st.st_mtime + 100))

    res = check(con, progress=quiet)
    assert len(res["damaged"]) == 1 and res["damaged"][0].endswith("report.pdf")
    assert len(res["changed"]) == 1 and res["changed"][0].endswith("notes.txt")
    assert summary(con)["damaged"] == 1
    assert check(con, progress=quiet)["damaged"]  # stays flagged until restored
    checks = {c["name"]: c for c in run_checks(con, library=str(lib))}
    assert checks["Damage check"]["status"] == "fail"
    assert cli.main(["--db", str(dbp), "fixity"]) == 2


def test_fixity_slices_and_missing(placed):
    con, src, lib, dbp = placed
    assert check(con, limit=1, progress=quiet)["recorded"] == 1
    assert check(con, limit=2, progress=quiet)["recorded"] == 2  # unrecorded files go first
    os.remove(next(os.path.join(lib, r[0]) for r in con.execute("SELECT dest FROM files WHERE name='notes.txt'")))
    assert len(check(con, progress=quiet)["missing"]) == 1


def test_default_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PETTAGAM_DB", raising=False)
    assert cli.default_db() == str(tmp_path / "home" / ".pettagam" / "pettagam.db")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "pettagam.db").write_bytes(b"")  # a catalog made by following the 0.2 README
    assert cli.default_db() == str(tmp_path / "home" / "pettagam.db")
    (tmp_path / "pettagam.db").write_bytes(b"")
    assert cli.default_db() == "pettagam.db"
    monkeypatch.setenv("PETTAGAM_DB", "/x/y.db")
    assert cli.default_db() == "/x/y.db"


def test_export_copies_a_selection_without_overwriting(placed, tmp_path):
    con, src, lib, dbp = placed
    out = tmp_path / "share"
    assert cli.main(["--db", str(dbp), "export", str(out), "--tag", "beach"]) == 0
    files = [os.path.join(d, f) for d, _, fs in os.walk(out) for f in fs]
    assert len(files) == 1 and files[0].lower().endswith("img_0001.jpg")
    assert cli.main(["--db", str(dbp), "export", str(out), "--tag", "beach"]) == 0  # already there: no copy
    assert len([f for _, _, fs in os.walk(out) for f in fs]) == 1
    open(files[0], "ab").write(b"edited")  # a different file with the same name is kept, not replaced
    from pettagam.library import export
    assert export(con, str(out), tag="beach", progress=quiet)["renamed"] == 1
    assert export(con, str(tmp_path / "flat"), category="documents", flat=True, progress=quiet)["copied"] == 2
    assert sorted(os.listdir(tmp_path / "flat")) == ["notes.txt", "report.pdf"]
    with pytest.raises(SystemExit):
        cli.main(["--db", str(dbp), "export", str(out)])


def test_notifications():
    from pettagam.notify import inbox_message, notify
    calls = []
    runner = lambda args, **kw: calls.append(args) or type("R", (), {"returncode": 0})()
    assert notify("Pettagam", 'Filed "3" files', platform="darwin", runner=runner)
    assert calls[-1][0] == "osascript" and '\\"3\\"' in calls[-1][2]
    assert notify("Pettagam", "x", platform="linux", runner=runner) and calls[-1][0] == "notify-send"
    assert not notify("Pettagam", "x", platform="win32", runner=runner)
    assert inbox_message({"placed": 1, "duplicate": 2}) == \
        "Filed 1 file into the library; 2 were already there (moved to _duplicates)."
    assert inbox_message({"placed": 0}) == ""


def test_rules_explain(placed, tmp_path, capsys):
    con, src, lib, dbp = placed
    rules = tmp_path / "rules.json"
    rules.write_text('{"rules": [{"name": "Reports", "if": {"ext": "pdf", "name": "report*"}, "then": "documents/reports"}]}')
    pdf, photo = src / "docs" / "report.pdf", src / "a" / "IMG_0001.JPG"
    assert cli.main(["--db", str(dbp), "rules", "--rules", str(rules), "--explain", str(pdf), str(photo)]) == 0
    out = capsys.readouterr().out
    assert "-> documents/reports/  (your rule 'Reports')" in out
    assert "images/photos" in out and "camera" in out and "smart type" in out


def test_tags_by_hand_from_the_command_line(placed, monkeypatch, capsys):  # FR-TAG-6
    con, src, lib, dbp = placed
    photo = next(os.path.join(lib, r[0]) for r in con.execute("SELECT dest FROM files WHERE name='IMG_0001.JPG'"))
    db = ["--db", str(dbp)]
    assert cli.main(db + ["tags", photo, "--add", "Grandma, Goa Trip", "--remove", "family"]) == 0
    con.close()
    from pettagam.db import connect
    con = connect(str(dbp))
    assert con.execute("SELECT tags FROM files WHERE name='IMG_0001.JPG'").fetchone()[0] == "beach,grandma,goa trip"
    assert search(con, "grandma")["total"] == 1 and search(con, tag="goa trip")["total"] == 1  # index kept up
    assert search(con, tag="family")["total"] == 0
    assert os.path.exists(lib / "_meta" / "catalog.db")  # the drive's copy has the change too

    # A path inside the library works as well, and listing changes nothing.
    rel = os.path.relpath(photo, lib)
    assert cli.main(db + ["tags", rel]) == 0
    assert "tags: beach, grandma, goa trip" in capsys.readouterr().out
    assert cli.main(db + ["tags", rel, "--set", "", "--description", "  Sunset at   the beach "]) == 0
    row = con.execute("SELECT tags, description FROM files WHERE name='IMG_0001.JPG'").fetchone()
    assert row[0] is None and row[1] == "Sunset at the beach"  # no tags left: AI tagging may suggest some
    assert cli.main(db + ["tags", str(src / "a" / "IMG_0001.JPG"), "--add", "x"]) == 1  # old files aren't library

    # Many files at once from a search asks first.
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert cli.main(db + ["tags", "--match", "documents", "--add", "paperwork"]) == 1
    assert search(con, tag="paperwork")["total"] == 0
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert cli.main(db + ["tags", "--match", "documents", "--add", "paperwork"]) == 0
    assert search(con, tag="paperwork")["total"] == search(con, category="documents")["total"] == 2


def test_edit_tags_limits_and_cleans(placed):
    from pettagam.library import MAX_TAGS, edit_tags, find_item
    con, src, lib, dbp = placed
    assert find_item(con, "documents/no-such-file.txt") is None
    it = find_item(con, next(r[0] for r in con.execute("SELECT dest FROM files WHERE name='notes.txt'")))
    assert edit_tags(con, [it], tags=[f"Tag {i}" for i in range(30)] + ["tag 1", "a,b"]) == 1
    tags = con.execute("SELECT tags FROM files WHERE id=?", (it[1],)).fetchone()[0].split(",")
    assert len(tags) == MAX_TAGS and tags[:2] == ["tag 0", "tag 1"] and len(set(tags)) == len(tags)
    assert edit_tags(con, [it], tags=tags) == 0  # nothing changed
    with pytest.raises(ValueError):
        edit_tags(con, [("sqlite_master", 1)], add=["x"])


def test_on_this_day(placed, capsys):  # FR-FIND-5
    import calendar
    con, src, lib, dbp = placed
    day = lambda y, m, d: calendar.timegm((y, m, d, 12, 0, 0))
    con.execute("UPDATE files SET taken=? WHERE name='IMG_0001.JPG'", (day(2019, 12, 25),))
    con.execute("UPDATE files SET taken=NULL, mtime=? WHERE name='report.pdf'", (day(2021, 12, 25),))
    con.execute("UPDATE files SET taken=?, mtime=? WHERE name='notes.txt'", (day(2021, 12, 24), day(2021, 12, 25)))
    con.commit()
    found = search(con, day="12-25")
    assert [it["name"] for it in found["items"]] == ["report.pdf", "IMG_0001.JPG"]  # newest first; taken wins
    assert search(con, category="images", day="12-25")["total"] == 1
    assert search(con, day="12-24")["items"][0]["name"] == "notes.txt"
    for bad in ("2019-12-25", "13-01", "1-5", "12-25' OR 1=1"):
        with pytest.raises(ValueError):
            search(con, day=bad)
    db = ["--db", str(dbp)]
    assert cli.main(db + ["find", "--day", "12-25"]) == 0
    assert "2 found" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(db + ["find", "--day", "25-12"])
    cli.main(db + ["find", "--on-this-day"])  # today: whatever it finds, it runs


def test_copies_of_a_library_file(placed):  # FR-FIND-6
    from pettagam.library import copies_of
    con, src, lib, dbp = placed
    keep = con.execute("SELECT id FROM files WHERE name='IMG_0001.JPG'").fetchone()[0]
    got = copies_of(con, "files", keep)
    assert got["total"] == 2 and got["copies"] == sorted(got["copies"])
    assert {os.path.relpath(c, src) for c in got["copies"]} == {os.path.join("b", "IMG_0001 copy.JPG"),
                                                                os.path.join("c", "IMG_0001 (2).JPG")}
    assert all(os.path.exists(c) for c in got["copies"])
    assert copies_of(con, "files", keep, limit=1) == {"copies": got["copies"][:1], "total": 2}
    pdf = con.execute("SELECT id FROM files WHERE name='report.pdf'").fetchone()[0]
    assert copies_of(con, "files", pdf)["total"] == 0 and copies_of(con, "inbox", keep)["total"] == 0
