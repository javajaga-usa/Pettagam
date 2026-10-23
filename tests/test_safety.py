"""Data-safety and security requirements (docs/REQUIREMENTS.md): careful file operations, one writer per
library, a private settings folder, a bounded background log, and a panel that only this computer can drive."""
import errno
import json
import os
import shutil
import stat
import urllib.error
import urllib.request

import pytest

from pettagam import cli, doctor, panel, safeio, service
from pettagam.apply import apply
from pettagam.maintenance import undo
from pettagam.verify import verify

from test_maintenance import placed  # noqa: F401  (fixture)
from test_panel import call, server, wait_job  # noqa: F401  (fixture)
from test_pipeline import jpeg, quiet, run_all, write

# --- careful file operations ---------------------------------------------------

def test_move_no_clobber_refuses_an_existing_file(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"new")
    b.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        safeio.move_no_clobber(str(a), str(b))
    assert a.read_bytes() == b"new" and b.read_bytes() == b"old"
    c = tmp_path / "c.txt"
    safeio.move_no_clobber(str(a), str(c))
    assert not a.exists() and c.read_bytes() == b"new"


def test_move_no_clobber_without_hardlinks(tmp_path, monkeypatch):
    def no_links(*a):
        raise PermissionError(errno.EPERM, "no hardlinks on this drive")
    monkeypatch.setattr(safeio.os, "link", no_links)
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"x")
    b.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        safeio.move_no_clobber(str(a), str(b))
    safeio.move_no_clobber(str(a), str(tmp_path / "c.txt"))
    assert (tmp_path / "c.txt").read_bytes() == b"x" and b.read_bytes() == b"keep"


def test_durable_copy_checks_and_never_replaces(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    src.write_bytes(os.urandom(200_000))
    dst = tmp_path / "out" / "dst.bin"
    dst.parent.mkdir()
    safeio.durable_copy(str(src), str(dst), check="hash")
    assert dst.read_bytes() == src.read_bytes()
    assert int(dst.stat().st_mtime) == int(src.stat().st_mtime)

    other = tmp_path / "other.bin"
    other.write_bytes(b"different")
    with pytest.raises(FileExistsError):
        safeio.durable_copy(str(other), str(dst))
    assert dst.read_bytes() == src.read_bytes()
    assert not os.path.exists(str(dst) + ".part")

    # A copy that doesn't match is thrown away and the original is left alone.
    hashes = iter(["aaa", "bbb"])
    monkeypatch.setattr(safeio, "hash_file", lambda p: next(hashes))
    with pytest.raises(OSError, match="did not match"):
        safeio.durable_copy(str(src), str(tmp_path / "out" / "bad.bin"), check="hash")
    assert not (tmp_path / "out" / "bad.bin").exists() and not (tmp_path / "out" / "bad.bin.part").exists()
    assert src.exists()


def test_move_across_drives_copies_checks_then_removes(tmp_path, monkeypatch):
    monkeypatch.setattr(safeio, "same_drive", lambda a, b: False)
    src = tmp_path / "inbox" / "photo.jpg"
    src.parent.mkdir()
    src.write_bytes(b"photo bytes")
    dst = tmp_path / "lib" / "images" / "photo.jpg"
    assert safeio.move_file(str(src), str(dst)) == "copy"
    assert dst.read_bytes() == b"photo bytes" and not src.exists()

    src.write_bytes(b"second")
    monkeypatch.setattr(safeio, "durable_copy", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        safeio.move_file(str(src), str(tmp_path / "lib" / "images" / "second.jpg"))
    assert src.read_bytes() == b"second"  # nothing is removed unless the copy is safely in place


def test_apply_by_copy_places_identical_files(tmp_path):
    src, lib, dbp = tmp_path / "old", tmp_path / "library", tmp_path / "p.db"
    write(str(src), "a/IMG_0001.JPG", jpeg("Apple", "iPhone 12", pad=5000))
    write(str(src), "b/notes.txt", b"notes")
    con = run_all(src, lib, dbp, tmp_path)
    res = apply(con, method="copy", progress=quiet)
    assert res == {"done": 2, "errors": 0, "method": "copy"}
    assert not [f for _, _, fs in os.walk(lib) for f in fs if f.endswith(".part")]
    full = verify(con, sample=-1, progress=quiet)
    assert full["ok"]


# --- one writer per library ------------------------------------------------------

def test_apply_and_undo_wait_for_the_library_lock(placed):  # noqa: F811
    fcntl = pytest.importorskip("fcntl")
    con, _, lib, _ = placed
    (lib / "_meta").mkdir(parents=True, exist_ok=True)
    with open(lib / "_meta" / safeio.LOCK_NAME, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        with pytest.raises(SystemExit, match="Another Pettagam step"):
            apply(con, progress=quiet)
        with pytest.raises(SystemExit, match="Another Pettagam step"):
            undo(con, progress=quiet)
        assert undo(con, dry_run=True, progress=quiet)["removed"] >= 0  # a preview needs no lock
    assert apply(con, progress=quiet)["errors"] == 0


# --- private settings and a bounded log ------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_private_dir_is_only_for_this_user(tmp_path):
    d = tmp_path / "home" / ".pettagam"
    d.parent.mkdir()
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    safeio.private_dir(str(d))
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    new = tmp_path / "fresh"
    safeio.private_dir(str(new))
    assert stat.S_IMODE(new.stat().st_mode) == 0o700


def test_background_log_rotates(tmp_path):
    log = tmp_path / ".pettagam" / "inbox-service.log"
    log.parent.mkdir()
    assert not service.rotate_log(home=str(tmp_path))  # no log yet
    log.write_text("x" * 100)
    assert not service.rotate_log(home=str(tmp_path), max_bytes=1000)
    log.write_text("y" * 2000)
    assert service.rotate_log(home=str(tmp_path), max_bytes=1000)
    assert not log.exists() and (tmp_path / ".pettagam" / "inbox-service.log.1").read_text() == "y" * 2000


def test_verify_full_checksums_every_copy(tmp_path, capsys):
    src, lib, dbp = tmp_path / "old", tmp_path / "library", tmp_path / "p.db"
    for i in range(5):
        write(str(src), f"d/file{i}.txt", f"content {i}".encode())
    con = run_all(src, lib, dbp, tmp_path)
    apply(con, method="copy", progress=quiet)
    dest = con.execute("SELECT dest FROM files WHERE name='file3.txt'").fetchone()[0]
    con.close()
    assert cli.main(["--db", str(dbp), "verify", "--full"]) == 0
    assert "checksummed 5 of 5 copies" in capsys.readouterr().out
    # Same size and date but different content: only a full check finds it.
    p = lib / dest
    st = p.stat()
    p.write_bytes(b"content X")
    os.utime(p, (st.st_atime, st.st_mtime))
    assert cli.main(["--db", str(dbp), "verify", "--full"]) == 2


# --- the panel ------------------------------------------------------------------

def raw(base, path, data=None, headers=None, method=None):
    req = urllib.request.Request(base + path, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_panel_refuses_other_host_names(server):  # noqa: F811
    base, _ = server
    # A page on another website that rebinds its name to 127.0.0.1 still sends its own name as the Host.
    assert raw(base, "/api/state", headers={"Host": "evil.example:8484"})[0] == 403
    assert raw(base, "/api/run", json.dumps({"action": "dryrun"}).encode(),
               {"Host": "evil.example:8484", "Origin": "http://evil.example:8484",
                "Content-Type": "application/json"})[0] == 403
    for host in ("localhost:8484", "127.0.0.1", "[::1]:8484"):
        assert raw(base, "/api/state", headers={"Host": host})[0] == 200


def test_panel_post_needs_json_and_a_sane_size(server):  # noqa: F811
    base, _ = server
    form = raw(base, "/api/settings", b"source=/", {"Content-Type": "application/x-www-form-urlencoded"})
    assert form[0] == 415
    big = raw(base, "/api/settings", b"{}", {"Content-Type": "application/json",
                                             "Content-Length": str(panel.MAX_BODY + 1)})
    assert big[0] == 413
    assert raw(base, "/api/settings", b"[1, 2]", {"Content-Type": "application/json"})[0] == 400


def test_panel_settings_are_checked(server):  # noqa: F811
    base, tmp = server
    for bad in ({"workers": "lots"}, {"workers": 0}, {"workers": 500}, {"split_at": 5}, {"source": 7}):
        code, _, body = raw(base, "/api/settings", json.dumps(bad).encode(), {"Content-Type": "application/json"})
        assert code == 400, bad
        assert json.loads(body)["error"]
    s = call(base, "/api/settings", {"workers": "4", "split_at": 2000, "library": str(tmp / "lib") + "/"})["settings"]
    assert s["workers"] == 4 and s["split_at"] == 2000 and s["library"] == str(tmp / "lib")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_panel_settings_folder_is_private(server):  # noqa: F811
    base, tmp = server
    call(base, "/api/settings", {"workers": 2})
    assert stat.S_IMODE(os.stat(panel.HOME).st_mode) == 0o700


def test_panel_headers_and_sandboxed_library_files(server):  # noqa: F811
    base, tmp = server
    code, h, _ = raw(base, "/")
    assert code == 200 and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"

    src = tmp / "old"
    write(str(src), "web/page.html", b"<script>fetch('/api/service',{method:'POST'})</script>")
    write(str(src), "docs/Invoice_1.pdf", b"%PDF-1.7 hello")
    call(base, "/api/settings", {"source": str(src), "library": str(tmp / "lib")})
    call(base, "/api/run", {"action": "dryrun"})
    wait_job(base)
    call(base, "/api/run", {"action": "apply"})
    wait_job(base)
    items = {os.path.basename(i["dest"]): i["dest"] for i in call(base, "/api/explore")["items"]}
    code, h, _ = raw(base, "/api/file?path=" + urllib.request.quote(items["page.html"]))
    assert code == 200 and h["Content-Security-Policy"].startswith("sandbox")
    code, h, _ = raw(base, "/api/file?path=" + urllib.request.quote(items["Invoice_1.pdf"]))
    assert code == 200 and "sandbox" not in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "SAMEORIGIN"




# --- platform: the drive's format -------------------------------------------------

MAC_MOUNT = """/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse)
/dev/disk4s2 on /Volumes/N300 Pro (apfs, local, nodev, nosuid, journaled, noowners)
/dev/disk5s1 on /Volumes/STICK (exfat, local, nodev, nosuid, noowners)
"""
LINUX_MOUNTS = """/dev/nvme0n1p2 / ext4 rw,relatime 0 0
/dev/sdb1 /srv/ninaivu ext4 rw,relatime 0 0
/dev/sdc1 /media/me/My\\040Stick exfat rw 0 0
"""


def test_drive_format_is_read_from_mounts():
    assert doctor.parse_mounts(MAC_MOUNT, "/Volumes/N300 Pro/Pettagam", mac=True) == "apfs"
    assert doctor.parse_mounts(MAC_MOUNT, "/Volumes/STICK/x", mac=True) == "exfat"
    assert doctor.parse_mounts(MAC_MOUNT, "/Users/me", mac=True) == "apfs"
    assert doctor.parse_mounts(LINUX_MOUNTS, "/srv/ninaivu/Pettagam", mac=False) == "ext4"
    assert doctor.parse_mounts(LINUX_MOUNTS, "/media/me/My Stick/lib", mac=False) == "exfat"
    assert doctor.parse_mounts(LINUX_MOUNTS, "/srv/ninaivu2", mac=False) == "ext4"  # the root, not /srv/ninaivu


def test_drive_format_advice():
    assert "Ubuntu can't write" in doctor.drive_format_check("apfs")["text"]
    assert doctor.drive_format_check("exfat")["status"] == "warn"
    assert doctor.drive_format_check("ext4")["status"] == "ok"
    assert doctor.drive_format_check(None)["status"] == "ok"
    fs = doctor.filesystem("/")
    assert fs is None or isinstance(fs, str)


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl to make a certificate")
def test_panel_over_https_for_the_network(tmp_path, monkeypatch):  # SEC-10
    import ssl
    import threading
    monkeypatch.setattr(panel, "HOME", str(tmp_path / "home"))
    cert, key = panel.self_signed()
    assert stat.S_IMODE(os.stat(key).st_mode) & 0o077 == 0  # the key is yours only
    assert panel.self_signed() == (cert, key)  # made once, so the fingerprint stays the same
    assert len(panel.fingerprint(cert).split(":")) == 32

    monkeypatch.setattr(panel.Handler, "token", "s3cret")
    monkeypatch.setattr(panel.Handler, "secure", True)
    httpd = panel.make_server("127.0.0.1", 0, cert, key)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        ctx = ssl.create_default_context(cafile=cert)
        ctx.check_hostname = False  # the certificate names this computer, the test connects by IP
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/?token=s3cret", context=ctx, timeout=10) as r:
            assert r.status == 200
            cookie = r.headers["Set-Cookie"]
            assert "Secure" in cookie and "HttpOnly" in cookie
        with pytest.raises(urllib.error.HTTPError) as e:  # still needs the token
            urllib.request.urlopen(f"https://127.0.0.1:{port}/api/state", context=ctx, timeout=10)
        assert e.value.code == 403
        with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):  # plain HTTP gets nothing
            urllib.request.urlopen(f"http://127.0.0.1:{port}/?token=s3cret", timeout=10).read()
        # A client that connects and says nothing doesn't block the others.
        import socket
        idle = socket.create_connection(("127.0.0.1", port))
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/?token=s3cret", context=ctx, timeout=10) as r:
            assert r.status == 200
        idle.close()
    finally:
        httpd.shutdown()


def test_panel_tls_needs_both_files(tmp_path, monkeypatch):  # SEC-10
    monkeypatch.setattr(panel, "HOME", str(tmp_path / "home"))
    with pytest.raises(SystemExit, match="both"):
        panel.serve("0.0.0.0", 0, open_browser=False, cert=str(tmp_path / "c.pem"))
    with pytest.raises(SystemExit, match="certificate"):
        panel.tls_context(str(tmp_path / "missing.pem"), str(tmp_path / "missing.key"))


def test_scan_stores_inode_numbers_past_sqlite_range(tmp_path, monkeypatch):  # PLAT-8, REL-3
    """macOS's FSKit exFAT driver gives some files inodes just under 2**64, past SQLite's signed integers."""
    import sqlite3
    import types

    from pettagam import scan as sc
    from pettagam.db import connect
    src = tmp_path / "old"
    write(str(src), "a.txt", b"one")
    write(str(src), "b.txt", b"two")
    write(str(src), "c.txt", b"three")
    real = sc._examine
    huge = {"a.txt": 2**64 - 1296, "b.txt": 2**64 - 1296, "c.txt": 2**63}

    def examine(root, rel, st):  # the stat numbers such a drive reports
        return real(root, rel, types.SimpleNamespace(st_size=st.st_size, st_mtime=st.st_mtime, st_dev=2**64 - 1,
                                                     st_ino=huge[rel]))
    monkeypatch.setattr(sc, "_examine", examine)
    con = connect(str(tmp_path / "p.db"))
    assert sc.scan(con, str(src), progress=quiet) == 3
    rows = dict(con.execute("SELECT name, ino FROM files").fetchall())
    assert rows == {"a.txt": -1296, "b.txt": -1296, "c.txt": -(2**63)}
    # Two names for one file (a hardlink) still share one inode, so they still count as one.
    assert con.execute("SELECT COUNT(*) FROM (SELECT 1 FROM files GROUP BY dev, ino)").fetchone()[0] == 2

    # A scan that fails part-way lets go of the catalog instead of keeping it locked.
    def boom(root, d):
        raise OverflowError("cannot store")
    monkeypatch.setattr(sc, "_scan_dir", boom)
    with pytest.raises(OverflowError):
        sc.scan(con, str(src), progress=quiet)
    assert not con.in_transaction
    other = sqlite3.connect(str(tmp_path / "p.db"), timeout=0.1)
    other.execute("INSERT INTO meta(key, value) VALUES('probe', '1')")
    other.commit()
    assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3  # nothing was committed yet
