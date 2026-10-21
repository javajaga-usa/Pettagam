import os
import plistlib
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="shell scripts")


def run(script, home, **env):
    e = {**os.environ, "HOME": str(home), "PETTAGAM_NONINTERACTIVE": "1", **env}
    return subprocess.run(["bash", os.path.join(ROOT, script)], env=e, capture_output=True, text=True, timeout=120)


def test_setup_builds_the_app_and_uninstall_removes_it_but_never_files(tmp_path):
    home, apps = tmp_path / "home", tmp_path / "Applications"
    (home / ".pettagam").mkdir(parents=True)
    (home / ".pettagam" / "panel.json").write_text("{}")
    library = home / "Library-folder"
    library.mkdir()
    (library / "photo.jpg").write_bytes(b"keep me")

    r = run("Setup Pettagam.command", home, PETTAGAM_APPS_DIR=str(apps))
    assert r.returncode == 0, r.stdout + r.stderr
    app = apps / "Pettagam.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "Pettagam" and info["CFBundleShortVersionString"][0].isdigit()
    exe = app / "Contents" / "MacOS" / "Pettagam"
    assert os.access(exe, os.X_OK) and "launchers/Pettagam.command" in exe.read_text()

    r = run("Uninstall Pettagam.command", home, PETTAGAM_APPS_DIR=str(apps))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not app.exists()
    assert (home / ".pettagam" / "panel.json").exists()  # kept unless asked
    assert (library / "photo.jpg").read_bytes() == b"keep me"
    run("Uninstall Pettagam.command", home, PETTAGAM_APPS_DIR=str(apps), PETTAGAM_REMOVE_SETTINGS="1")
    assert not (home / ".pettagam").exists()


def test_ubuntu_setup_adds_a_working_command(tmp_path):
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    r = subprocess.run(["sh", os.path.join(ROOT, "launchers", "setup-ubuntu.sh")],
                       env={**os.environ, "HOME": str(home), "PETTAGAM_BIN_DIR": str(bindir), "XDG_CURRENT_DESKTOP": ""},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    out = subprocess.run([str(bindir / "pettagam"), "--version"], capture_output=True, text=True, cwd=str(tmp_path),
                         env={**os.environ, "HOME": str(home)})
    assert out.stdout.startswith("pettagam ")


def test_icon_is_drawn(tmp_path):
    subprocess.run([sys.executable, os.path.join(ROOT, "launchers", "make_icon.py"), str(tmp_path)], check=True)
    data = (tmp_path / "icon_16x16.png").read_bytes()
    assert data.startswith(b"\x89PNG") and len(os.listdir(tmp_path)) == 12
