"""Ease of use: progress with time left, stopping a step, plain errors, the command line's start screen and
help, next steps, the panel finding itself already open, and updating and restarting (USE-8..USE-13)."""
import errno
import os
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pettagam import cli, errors, notify, panel, update
from pettagam.apply import apply
from pettagam.db import connect
from pettagam.hashing import find_duplicates
from pettagam.plan import plan
from pettagam.progress import Progress, Stopped, time_left
from pettagam.scan import scan
from pettagam.verify import verify

from test_panel import call, server, wait_job  # noqa: F401  (fixture)
from test_pipeline import tree, write  # noqa: F401  (fixture)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# --- progress, time left and stopping -------------------------------------------------

def test_progress_shows_percent_and_time_left():  # USE-8
    clock, said = Clock(), []
    p = Progress(said.append, clock=clock)
    clock.t += 10
    p("  scanned 10,000 of about 100,000 files (1,000/s), 40 folders still to read")
    assert said[-1].endswith("· 10%, about 2 min left")
    assert p.percent == 10 and 85 < p.eta < 95
    clock.t += 10
    p("  scanned 20,000 of about 100,000 files (1,000/s), 30 folders still to read")
    assert p.percent == 20 and 75 < p.eta < 85
    p("Scanned 120,000 files in 12.0s")  # a line without a count clears it
    assert said[-1] == "Scanned 120,000 files in 12.0s" and p.percent is None
    clock.t += 5
    p("  1,000 of 4,000 placed (200/s)")  # a new count starts its own clock
    assert p.percent == 25 and 10 < p.eta < 20
    assert time_left(30) == "less than a minute" and time_left(600) == "about 10 min"
    assert time_left(2 * 3600 + 5 * 60) == "about 2 h 05 min"


def test_stopping_raises_at_the_next_line():  # USE-9
    said = []
    p = Progress(said.append)
    p("one")
    p.stop()
    with pytest.raises(Stopped):
        p("two")
    assert said == ["one"]


def test_a_stopped_placement_carries_on_next_time(tree):  # noqa: F811  USE-9
    src, lib, dbp = tree
    con = connect(str(dbp))
    scan(con, str(src), progress=lambda m: None)
    find_duplicates(con, progress=lambda m: None)
    plan(con, str(lib), progress=lambda m: None)
    p = Progress(lambda m: p.stop() if m.startswith("Placing") else None)  # stop asked as placing starts
    with pytest.raises(Stopped):
        apply(con, workers=1, progress=p)
    placed = con.execute("SELECT COUNT(*) FROM files WHERE status='done'").fetchone()[0]
    todo = con.execute("SELECT COUNT(*) FROM files WHERE action='place'").fetchone()[0]
    assert 1 <= placed < todo  # stopped part-way, and what was placed is recorded
    res = apply(con, workers=2, progress=lambda m: None)
    assert res["errors"] == 0
    assert verify(con, progress=lambda m: None)["ok"]


def test_a_repeat_scan_knows_how_many_files_to_expect(tree, monkeypatch):  # noqa: F811  USE-8
    from pettagam import scan as sc
    monkeypatch.setattr(sc, "PROGRESS_EVERY", 0)
    src, _, dbp = tree
    con = connect(str(dbp))
    n = scan(con, str(src), progress=lambda m: None)
    said = []
    scan(con, str(src), progress=said.append)
    assert any(f"of about {n:,} files" in m for m in said)


def test_panel_stop_button_stops_the_running_step(server, monkeypatch):  # noqa: F811  USE-9
    base, _ = server
    monkeypatch.setattr(panel, "NOTIFY_AFTER", 0)
    sent = []
    monkeypatch.setattr(notify, "RUNNER", lambda args, **kw: sent.append(args) or subprocess.CompletedProcess(args, 0))
    monkeypatch.setattr(notify.sys, "platform", "linux")

    def slow(say):
        for i in range(1, 1000):
            say(f"  {i:,} of 1,000 placed")
            time.sleep(0.01)

    panel.JOB.start("apply", slow)
    time.sleep(0.1)
    st = call(base, "/api/state")
    assert st["job"]["running"] and st["job"]["percent"] is not None
    assert call(base, "/api/stop", {})["stopping"]
    st = wait_job(base)
    assert st["job"]["result"] == {"stopped": True}
    assert "Place files again to carry on" in st["job"]["log"][-1]
    assert sent and "Placing files stopped." in sent[0]  # the desktop notification after a long step
    assert not call(base, "/api/stop", {})["stopping"]  # nothing left to stop


# --- plain errors ------------------------------------------------------------------------

def test_errors_are_explained_in_plain_words():  # USE-10
    full = OSError(errno.ENOSPC, "No space left on device", "/Volumes/N300/Pettagam/a.jpg")
    assert errors.explain(full).startswith("The drive is full (/Volumes/N300/Pettagam/a.jpg)")
    assert "plugged in" in errors.explain(FileNotFoundError(2, "No such file", "/Volumes/N300/Old"))
    assert "Not allowed" in errors.explain(PermissionError(13, "Permission denied", "/Volumes/N300"))
    assert "another Pettagam step" in errors.explain(sqlite3.OperationalError("database is locked"))
    assert "failing" in errors.explain(OSError(errno.EIO, "Input/output error"))
    assert errors.is_bug(ValueError("x")) and not errors.is_bug(full)


def test_a_bug_is_reported_plainly_with_its_details_saved(tmp_path, monkeypatch, capsys):  # USE-10
    monkeypatch.setattr(errors, "HOME", str(tmp_path / "home"))

    def boom(con, args):
        raise ValueError("something unexpected")

    monkeypatch.setattr(cli, "_run", boom)
    assert cli.main(["--db", str(tmp_path / "p.db"), "stats"]) == 1
    err = capsys.readouterr().err
    assert "Problem: ValueError: something unexpected" in err and "Traceback" not in err
    details = (tmp_path / "home" / "last-error.txt").read_text()
    assert "Traceback" in details and "stats" in details


def test_ctrl_c_says_work_is_kept(tmp_path, monkeypatch, capsys):  # USE-10
    def interrupted(con, args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupted)
    assert cli.main(["--db", str(tmp_path / "p.db"), "apply"]) == 130
    assert "run the same command again" in capsys.readouterr().err


# --- the command line ------------------------------------------------------------------

def test_every_command_is_in_a_help_group(capsys):  # USE-11
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    grouped = [n for _, names in cli.GROUPS for n in names]
    assert len(grouped) == len(set(grouped))
    import argparse
    parser_names = set()
    real = argparse._SubParsersAction.add_parser

    def spy(self, name, **kw):
        parser_names.add(name)
        return real(self, name, **kw)

    argparse._SubParsersAction.add_parser = spy
    try:
        with pytest.raises(SystemExit):
            cli.main(["--help"])
    finally:
        argparse._SubParsersAction.add_parser = real
    assert parser_names == set(grouped)
    assert "Start here:" in out and "  dryrun      scan + plan" in out


def test_start_screen_typos_and_next_steps(tree, capsys, monkeypatch):  # noqa: F811  USE-11
    src, lib, dbp = tree
    db = ["--db", str(dbp)]
    assert cli.main(db) == 0
    assert "pettagam panel" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        cli.main([*db, "aply"])
    assert e.value.code == 2 and "Did you mean: pettagam apply" in capsys.readouterr().err
    assert cli.main([*db, "apply"]) == 1  # nothing planned: says so instead of asking about 0 files
    assert "Start with a dry run" in capsys.readouterr().out
    assert cli.main([*db, "verify"]) == 1
    assert "Nothing to check yet" in capsys.readouterr().out

    assert cli.main([*db, "dryrun", str(src), "--library", str(lib), "--out", str(dbp.parent / "rep")]) == 0
    assert "place the files with: pettagam apply" in capsys.readouterr().out
    assert cli.main([*db, "status"]) == 0
    assert "Next:      review the plan" in capsys.readouterr().out

    def no_terminal(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_terminal)
    assert cli.main([*db, "apply"]) == 1  # nobody to answer: no traceback, and nothing placed
    assert "Add --yes" in capsys.readouterr().out
    assert cli.main([*db, "apply", "--yes"]) == 0
    assert "pettagam verify --rescan" in capsys.readouterr().out
    assert cli.main([*db, "verify", "--rescan"]) == 0
    capsys.readouterr()
    assert cli.main([*db, "status"]) == 0
    assert "All done" in capsys.readouterr().out
    assert cli.main([*db, "apply"]) == 0
    assert "already in the library" in capsys.readouterr().out


# --- opening the panel twice ---------------------------------------------------------------

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_opening_the_panel_twice_finds_the_running_one(tmp_path, monkeypatch, capsys):  # USE-12
    monkeypatch.setattr(panel, "HOME", str(tmp_path / "home"))
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), panel.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        panel.serve("127.0.0.1", port, open_browser=False)  # returns at once instead of failing
        assert "already open" in capsys.readouterr().out
    finally:
        httpd.shutdown()
        httpd.server_close()

    class Other(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    other = ThreadingHTTPServer(("127.0.0.1", port), Other)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        with pytest.raises(SystemExit, match="Another program is using port"):
            panel.serve("127.0.0.1", port, open_browser=False)
    finally:
        other.shutdown()
        other.server_close()


# --- updating and restarting ------------------------------------------------------------

def git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_update_pulls_the_new_version_and_never_overwrites_edits(tmp_path):  # USE-13
    origin, mine, theirs = tmp_path / "origin.git", tmp_path / "mine", tmp_path / "theirs"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(theirs))
    write(str(theirs), "pettagam/__init__.py", b'__version__ = "1.0.0"\n')
    git(theirs, "add", ".")
    git(theirs, "commit", "-m", "First")
    git(theirs, "push", "origin", "HEAD:main")
    git(tmp_path, "clone", str(origin), str(mine))
    assert update.check(str(mine))["behind"] == 0

    write(str(theirs), "pettagam/__init__.py", b'__version__ = "1.1.0"\n')
    write(str(theirs), "launchers/Pettagam.command", b"#!/bin/bash\n")
    git(theirs, "add", ".")
    git(theirs, "commit", "-m", "Friendlier panel")
    git(theirs, "push", "origin", "HEAD:main")
    res = update.check(str(mine))
    assert res["behind"] == 1 and res["changes"] == ["Friendlier panel"] and not res["error"]

    (mine / "pettagam" / "__init__.py").write_text('__version__ = "my own"\n')  # an edit by hand
    with pytest.raises(SystemExit, match="changed on this computer"):
        update.update(str(mine))
    assert (mine / "pettagam" / "__init__.py").read_text() == '__version__ = "my own"\n'
    git(mine, "checkout", "--", ".")
    done = update.update(str(mine))
    assert done == {"updated": True, "changes": ["Friendlier panel"], "version": "1.1.0", "setup_again": True}
    assert update.update(str(mine))["updated"] is False

    assert update.check(str(tmp_path / "not-a-checkout"))["checkout"] is False


def test_panel_offers_a_restart_after_its_files_change(server, monkeypatch):  # noqa: F811  USE-13
    base, _ = server
    monkeypatch.setattr(panel, "STARTED", {"stamp": update.code_stamp() - 100})
    monkeypatch.setattr(panel, "RESTART", [])
    st = call(base, "/api/state")
    assert st["app"]["restart_needed"] and st["app"]["version"]
    assert call(base, "/api/hello")["app"] == "pettagam"
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as e:  # started without serve(): it can't start itself again
        call(base, "/api/restart", {})
    assert e.value.code == 400
    restarted = []
    monkeypatch.setattr(panel, "RESTART", ["panel", "--no-browser"])
    monkeypatch.setattr(panel, "restart_soon", lambda: restarted.append(True))
    panel.JOB.start("verify", lambda say: time.sleep(0.5))
    with pytest.raises(urllib.error.HTTPError) as e:  # never in the middle of a step
        call(base, "/api/restart", {})
    assert e.value.code == 409
    wait_job(base)
    assert call(base, "/api/restart", {})["restarting"] and restarted
    with pytest.raises(urllib.error.HTTPError) as e:  # only from this computer
        call(base, "/api/restart", {}, headers={"Host": "example.com"})
    assert e.value.code == 403
