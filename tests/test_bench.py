"""pettagam bench, and the search speed it checks (docs/REQUIREMENTS.md, PERF-1, 2, 4, 6)."""
import json
import os
import time

from pettagam import bench, cli, library
from pettagam.db import connect, get_meta


def snapshot(root):
    return sorted((os.path.relpath(os.path.join(d, f), root), os.lstat(os.path.join(d, f)).st_mtime_ns)
                  for d, _, fs in os.walk(root) for f in fs)


def test_bench_reads_old_files_only_and_cleans_up(tmp_path):
    src, lib = tmp_path / "old", tmp_path / "lib"
    for i in range(30):
        (src / f"d{i % 4}").mkdir(parents=True, exist_ok=True)
        (src / f"d{i % 4}" / f"f{i}.jpg").write_bytes(os.urandom(2000 + i))
    lib.mkdir()
    before = snapshot(src)
    res = bench.run(str(src), str(lib), files=20, search_rows=2000, progress=lambda *a: None)
    assert snapshot(src) == before  # the old files are only read
    assert os.listdir(lib / "_meta") == []  # its test links are gone
    assert 20 <= res["scan"]["files"] <= 30 and res["scan"]["rate"] > 0
    assert res["read"]["bytes"] > 0 and res["place"]["files"] == 20
    assert set(res["search"]["each"]) == {"(newest first)", "beach 2019", "IMG_12345"}
    names = [v[0] for v in bench.verdicts(res)]
    assert names == ["Scan", "Read", "Place", "Search"]


def test_bench_reports_a_drive_without_hardlinks(tmp_path, monkeypatch):
    def no_links(*a):
        raise OSError(1, "Operation not permitted")
    monkeypatch.setattr(os, "link", no_links)
    r = bench.bench_place(str(tmp_path), files=5)
    assert "no hardlinks" in r["error"] and not os.listdir(tmp_path / "_meta")
    assert bench.verdicts({"place": r})[0][3] is False


def test_bench_command_records_the_numbers(tmp_path, capsys):
    dbp = tmp_path / "p.db"
    assert cli.main(["--db", str(dbp), "bench", "--search-rows", "1000", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["search"]["rows"] == 1000
    saved = json.loads(get_meta(connect(str(dbp)), "bench_last"))
    assert saved["search"]["rows"] == 1000 and saved["at"] <= time.time()


def _library_with_inbox(tmp_path):
    con = connect(str(tmp_path / "p.db"))
    for i in range(10):  # placed files, every other day
        con.execute("INSERT INTO files(path, name, size, mtime, dest, action, status, category) "
                    "VALUES (?,?,1,?,?, 'place', 'done', 'images')", (f"o/{i}", f"{i}.jpg", i * 2.0, f"images/{i}.jpg"))
    for i in range(5):  # inbox files on the days in between
        con.execute("INSERT INTO inbox(at, original, dest, action, mtime, size, category) "
                    "VALUES (0, ?, ?, 'placed', ?, 1, 'images')", (f"/in/{i}", f"images/in{i}.jpg", i * 2.0 + 1))
    con.execute("INSERT INTO files(path, name, size, mtime, dest, action, status) "
                "VALUES ('o/x', 'x', 1, 99, 'images/x', 'place', 'error')")  # not in the library
    con.commit()
    return con


def test_search_merges_placed_and_inbox_files_newest_first(tmp_path):  # PERF-6
    con = _library_with_inbox(tmp_path)
    res = library.search(con, limit=100)
    assert res["total"] == 15
    order = [r["mtime"] for r in res["items"]]
    assert order == [18, 16, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    assert [r["src"] for r in res["items"][5:7]] == ["inbox", "files"]
    pages = [r["dest"] for o in range(0, 15, 4) for r in library.search(con, offset=o, limit=4)["items"]]
    assert pages == [r["dest"] for r in res["items"]]  # pages join up with nothing lost or repeated
    assert library.search(con, "in3")["total"] == 1 and library.search(con, "zzz")["items"] == []
    assert [r["name"] for r in library.search(con, "in3")["items"]] == ["in3.jpg"]


def test_newest_first_uses_the_index(tmp_path):  # PERF-6
    con = connect(str(tmp_path / "p.db"))
    for part, index in ((library.PLACED_SQL, "files_recent"), (library.INBOX_SQL, "inbox_recent")):
        plan = " ".join(r[3] for r in con.execute(f"EXPLAIN QUERY PLAN {part} AND 1=1 {library.NEWEST} LIMIT 120"))
        assert index in plan and "TEMP B-TREE" not in plan, plan


def test_search_over_many_files_is_quick():  # PERF-4, scaled down for CI
    res = bench.bench_search(100_000)
    assert res["seconds"] < 1.0, res
