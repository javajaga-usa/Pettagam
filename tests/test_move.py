"""pettagam move-plan: the steps to move the library to the Ubuntu server (docs/REQUIREMENTS.md, FR-MOVE-4)."""
import os
import re
import shlex
import shutil
import subprocess

import pytest

from pettagam import cli, move
from pettagam.apply import apply
from pettagam.maintenance import backup_catalog, relocate

from test_maintenance import placed  # noqa: F401  (fixture)
from test_pipeline import quiet


def _rsync3():
    try:
        out = subprocess.run(["rsync", "--version"], capture_output=True, text=True).stdout
    except OSError:
        return False
    m = re.search(r"version (\d+)\.(\d+)", out)
    return bool(m) and (int(m[1]), int(m[2])) >= (3, 1)


def test_plan_copies_hardlinked_files_with_their_originals(placed, capsys):  # noqa: F811  FR-MOVE-4
    con, src, lib, dbp = placed
    apply(con, progress=quiet)
    p = move.plan(con, "/srv/ninaivu", host="jag@server")
    assert p["together"] and p["linked"] == 3
    assert p["copy_from"] == os.path.commonpath([str(src), str(lib)])
    assert p["new_library"] == "/srv/ninaivu/library" and p["new_source"] == "/srv/ninaivu/old"
    copy = next(s for s in p["steps"] if s["title"].startswith("Copy"))["commands"][0]
    assert shlex.split(copy)[:2] == ["rsync", "-aH"] and copy.endswith("jag@server:/srv/ninaivu/")
    assert p["bytes"] == sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(src) for f in fs)
    text = move.render(p)
    assert "relocate --catalog-from /srv/ninaivu/library/_meta/catalog.db" in text
    assert "doesn't run any of this" in text
    assert os.listdir(src) and not os.path.exists("/srv/ninaivu/library")  # nothing was copied or made

    alone = move.plan(con, "/srv/ninaivu", library_only=True)
    assert not alone["together"] and alone["new_source"] is None
    assert "become" in move.render(alone) or "full copies" in move.render(alone)


def test_plan_command_and_scripts(placed, tmp_path, capsys):  # noqa: F811  FR-MOVE-4
    con, src, lib, dbp = placed
    apply(con, progress=quiet)
    out = tmp_path / "plan"
    assert cli.main(["--db", str(dbp), "move-plan", "--to", "/srv/ninaivu", "--host", "jag@server",
                     "--write", str(out)]) == 0
    here, there = (out / "move-1-here.sh").read_text(), (out / "move-2-new-computer.sh").read_text()
    assert "rsync -aH" in here and "service off" in here and "relocate" not in here
    assert "relocate" in there and "verify --rescan" in there and "rsync" not in there
    assert os.access(out / "move-1-here.sh", os.X_OK)
    with pytest.raises(SystemExit, match="already exists"):  # never overwrites
        move.write_scripts(move.plan(con, "/srv/ninaivu"), str(out))
    with pytest.raises(SystemExit, match="full path"):
        move.plan(con, "srv/ninaivu")


@pytest.mark.skipif(not _rsync3(), reason="needs rsync 3")
def test_following_the_plan_keeps_hardlinks_and_relocate_finds_everything(placed, tmp_path):  # noqa: F811  FR-MOVE-4
    con, src, lib, dbp = placed
    apply(con, progress=quiet)
    backup_catalog(con)
    new = tmp_path / "ubuntu"
    p = move.plan(con, str(new))
    for step in p["steps"]:
        if step["title"].startswith(("Make the folder", "Copy")):
            for c in step["commands"]:
                subprocess.run(c, shell=True, check=True, capture_output=True)
    assert sorted(os.listdir(new)) == ["library", "old"]  # the two folders, nothing else from beside them
    photo_lib = next(os.path.join(d, f) for d, _, fs in os.walk(p["new_library"]) for f in fs if f.lower().endswith(".jpg"))
    photo_old = os.path.join(p["new_source"], "a", "IMG_0001.JPG")
    assert os.stat(photo_lib).st_ino == os.stat(photo_old).st_ino  # still one file, no extra space
    res = relocate(str(tmp_path / "new.db"), source=p["new_source"], library=p["new_library"],
                   catalog_from=os.path.join(p["new_library"], "_meta", "catalog.db"), progress=lambda *a: None)
    assert res["missing"] == 0 and res["found"] > 0
    shutil.rmtree(new)
