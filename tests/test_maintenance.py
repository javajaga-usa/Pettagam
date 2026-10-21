import os
import plistlib
import sqlite3
import time

import pytest

from pettagam import cli, service
from pettagam.apply import apply
from pettagam.db import connect, get_meta
from pettagam.doctor import run_checks
from pettagam.inbox import sort_inbox
from pettagam.maintenance import CATALOG_COPY, backup_catalog, relocate, undo
from pettagam.verify import verify
from test_pipeline import jpeg, quiet, run_all, write


@pytest.fixture
def placed(tmp_path):
    src, lib, dbp = tmp_path / "old", tmp_path / "library", tmp_path / "p.db"
    write(str(src), "a/IMG_0001.JPG", jpeg("Apple", "iPhone 12", pad=500))
    write(str(src), "docs/report.pdf", b"%PDF-1.7 report")
    write(str(src), "docs/notes.txt", b"notes")
    con = run_all(src, lib, dbp, tmp_path)
    return con, src, lib, dbp


class FakeRunner:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, args, **kw):
        self.calls.append(args)
        return type("R", (), {"returncode": 0 if self.ok else 1})()


# --- doctor -----------------------------------------------------------------

def test_doctor_reports_hardlinks_space_and_missing_drive(placed, tmp_path):
    con, src, lib, dbp = placed
    checks = {c["name"]: c for c in run_checks(con, db_path=str(dbp))}
    assert checks["Old files"]["status"] == "ok"
    assert checks["Placement"]["status"] == "ok" and "instant" in checks["Placement"]["text"]
    assert checks["Free space"]["status"] in ("ok", "warn")
    gone = {c["name"]: c for c in run_checks(con, source=str(tmp_path / "unplugged"))}
    assert gone["Old files"]["status"] == "fail" and "relocate" in gone["Old files"]["fix"]
    assert cli.main(["--db", str(dbp), "doctor"]) == 0


# --- background service -------------------------------------------------------

def test_service_mac_launch_agent(tmp_path):
    inbox, home = tmp_path / "inbox", tmp_path / "home"
    inbox.mkdir()
    fake = FakeRunner()
    st = service.install(str(tmp_path / "p.db"), str(inbox), str(tmp_path / "lib"), every=120,
                         platform="darwin", home=str(home), runner=fake)
    plist = home / "Library" / "LaunchAgents" / f"{service.LABEL}.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data["ProgramArguments"][1:4] == ["-m", "pettagam", "--db"]
    assert data["ProgramArguments"][-1] == "--notify"
    assert data["ProgramArguments"][-5:-1] == ["inbox", str(inbox), "--library", str(tmp_path / "lib")]
    assert data["WatchPaths"] == [str(inbox)]
    assert data["StartInterval"] == 120 and os.path.isdir(data["EnvironmentVariables"]["PYTHONPATH"])
    assert fake.calls[-1][:2] == ["launchctl", "bootstrap"] and st["started"]
    weekly = plistlib.loads((plist.parent / f"{service.FIXITY_LABEL}.plist").read_bytes())
    assert weekly["ProgramArguments"][-6:] == ["fixity", "--library", str(tmp_path / "lib"), "--minutes", "30", "--notify"]
    assert weekly["StartCalendarInterval"] == {"Weekday": 0, "Hour": 3, "Minute": 0} and "WatchPaths" not in weekly
    s = service.status(platform="darwin", home=str(home))
    assert s["installed"] and "2 minutes" in s["text"]
    assert service.uninstall(platform="darwin", home=str(home), runner=fake)
    assert not plist.exists() and fake.calls[-1][:2] == ["launchctl", "bootout"]
    assert not list(plist.parent.iterdir())
    assert not service.status(platform="darwin", home=str(home))["installed"]


def test_service_linux_systemd_units(tmp_path):
    inbox, home = tmp_path / "in box", tmp_path / "home"
    inbox.mkdir()
    fake = FakeRunner()
    service.install(str(tmp_path / "p.db"), str(inbox), str(tmp_path / "lib"), every=30,
                    platform="linux", home=str(home), runner=fake)
    units = home / ".config" / "systemd" / "user"
    svc = (units / "pettagam-inbox.service").read_text()
    assert f'"{inbox}"' in svc and "StandardOutput=append:" in svc
    assert "OnUnitActiveSec=60" in (units / "pettagam-inbox.timer").read_text()  # at least a minute
    assert f"PathChanged={inbox}" in (units / "pettagam-inbox.path").read_text()
    assert ["systemctl", "--user", "enable", "--now", "pettagam-inbox.timer", "pettagam-inbox.path",
            "pettagam-fixity.timer"] in fake.calls
    fx = (units / "pettagam-fixity.service").read_text()
    assert " fixity --library " in fx and "--minutes 30 --notify" in fx
    assert "OnCalendar=Sun" in (units / "pettagam-fixity.timer").read_text()
    service.uninstall(platform="linux", home=str(home), runner=fake)
    assert not list(units.iterdir())


def test_service_needs_an_inbox(tmp_path):
    with pytest.raises(SystemExit):
        service.install("p.db", str(tmp_path / "missing"), "lib", platform="darwin", home=str(tmp_path),
                        runner=FakeRunner())


# --- undo -----------------------------------------------------------------------

def test_undo_hardlinks_and_inbox_files(placed, tmp_path):
    con, src, lib, _ = placed
    apply(con, progress=quiet)
    inbox = tmp_path / "inbox"
    p = write(str(inbox), "sub/new.txt", b"fresh")
    os.utime(p, (time.time() - 60, time.time() - 60))
    sort_inbox(con, str(inbox), progress=quiet)
    assert not os.path.exists(p)
    preview = undo(con, dry_run=True, progress=quiet)
    assert preview == {"removed": 3, "kept": 0, "returned": 1}
    assert (lib / "documents" / "reports" / "report.pdf").exists()
    res = undo(con, progress=quiet)
    assert res == preview
    assert open(p, "rb").read() == b"fresh"  # back in the inbox
    assert sorted(os.listdir(lib)) == ["_meta"]
    assert all(os.path.exists(src / r) for r in ("a/IMG_0001.JPG", "docs/report.pdf", "docs/notes.txt"))
    # The catalog is ready to place again.
    assert apply(con, progress=quiet)["done"] == 3
    assert verify(con, rescan=True, progress=quiet)["ok"]


def test_undo_never_removes_the_only_copy(placed):
    con, src, lib, _ = placed
    apply(con, method="copy", progress=quiet)
    os.remove(src / "docs" / "notes.txt")  # old file gone: the library copy is now the only one
    with open(lib / "documents" / "reports" / "report.pdf", "ab") as f:
        f.write(b" edited")  # changed after placing
    res = undo(con, progress=quiet)
    assert res["kept"] == 2 and res["removed"] == 1
    assert (lib / "documents" / "general" / "notes.txt").exists()
    assert (lib / "documents" / "reports" / "report.pdf").exists()


# --- catalog copy and relocate ----------------------------------------------------

def test_catalog_copy_travels_and_relocate_restores_it(placed, tmp_path):
    con, src, lib, dbp = placed
    assert cli.main(["--db", str(dbp), "apply", "--yes"]) == 0
    copy = lib / CATALOG_COPY
    assert copy.exists()
    n = sqlite3.connect(str(copy)).execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert n == 3
    # "Move to Ubuntu": the drive is now mounted elsewhere.
    mnt = tmp_path / "srv"
    mnt.mkdir()
    os.rename(src, mnt / "old")
    os.rename(lib, mnt / "library")
    new_db = tmp_path / "ubuntu.db"
    assert cli.main(["--db", str(new_db), "relocate", "--catalog-from", str(mnt / "library" / CATALOG_COPY),
                     "--source", str(mnt / "old"), "--library", str(mnt / "library")]) == 0
    con2 = connect(str(new_db))
    assert get_meta(con2, "source") == str(mnt / "old")
    assert verify(con2, rescan=True, progress=quiet)["ok"]
    with pytest.raises(SystemExit):
        relocate(str(new_db), catalog_from=str(mnt / "library" / CATALOG_COPY), progress=quiet)


def test_backup_without_library_is_a_no_op(tmp_path):
    con = connect(str(tmp_path / "p.db"))
    assert backup_catalog(con) == ""


# --- inbox lock -----------------------------------------------------------------

def test_inbox_pass_skips_while_another_runs(placed, tmp_path):
    fcntl = pytest.importorskip("fcntl")
    con, _, lib, _ = placed
    inbox = tmp_path / "inbox"
    p = write(str(inbox), "x.txt", b"x")
    os.utime(p, (time.time() - 60, time.time() - 60))
    (lib / "_meta").mkdir(parents=True, exist_ok=True)
    with open(lib / "_meta" / "inbox.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert sort_inbox(con, str(inbox), progress=quiet).get("busy") == 1
    assert sort_inbox(con, str(inbox), progress=quiet)["placed"] == 1
