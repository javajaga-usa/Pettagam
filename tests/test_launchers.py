import os
import plistlib
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="shell scripts")


def copy_of_repo(tmp_path):
    """Setup makes a .venv beside the code, so the tests run on a copy rather than the checkout."""
    dest = tmp_path / "Pettagam"
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.egg-info", "build", "dist"))
    return dest


def run(script, home, root=ROOT, **env):
    e = {**os.environ, "HOME": str(home), "PETTAGAM_NONINTERACTIVE": "1", **env}
    e.pop("PETTAGAM_PYTHON", None)
    return subprocess.run(["bash", os.path.join(root, script)], env=e, capture_output=True, text=True, timeout=300)


def test_setup_builds_the_app_and_uninstall_removes_it_but_never_files(tmp_path):
    home, apps = tmp_path / "home", tmp_path / "Applications"
    (home / ".pettagam").mkdir(parents=True)
    (home / ".pettagam" / "panel.json").write_text("{}")
    library = home / "Library-folder"
    library.mkdir()
    (library / "photo.jpg").write_bytes(b"keep me")

    root = copy_of_repo(tmp_path)
    r = run("Setup Pettagam.command", home, root, PETTAGAM_APPS_DIR=str(apps))
    assert r.returncode == 0, r.stdout + r.stderr
    venv_python = root / ".venv" / "bin" / "python"
    assert subprocess.run([str(venv_python), "-c", "import sys; assert sys.version_info >= (3, 13)"]).returncode == 0
    app = apps / "Pettagam.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "Pettagam" and info["CFBundleShortVersionString"][0].isdigit()
    exe = app / "Contents" / "MacOS" / "Pettagam"
    assert os.access(exe, os.X_OK) and "launchers/Pettagam.command" in exe.read_text()

    r = run("Uninstall Pettagam.command", home, root, PETTAGAM_APPS_DIR=str(apps))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not app.exists() and not (root / ".venv").exists()
    assert (home / ".pettagam" / "panel.json").exists()  # kept unless asked
    assert (library / "photo.jpg").read_bytes() == b"keep me"
    run("Uninstall Pettagam.command", home, root, PETTAGAM_APPS_DIR=str(apps), PETTAGAM_REMOVE_SETTINGS="1")
    assert not (home / ".pettagam").exists()


def test_ubuntu_setup_adds_a_working_command(tmp_path):
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    root = copy_of_repo(tmp_path)
    r = subprocess.run(["sh", str(root / "launchers" / "setup-ubuntu.sh")],
                       env={**os.environ, "HOME": str(home), "PETTAGAM_BIN_DIR": str(bindir), "XDG_CURRENT_DESKTOP": ""},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    out = subprocess.run([str(bindir / "pettagam"), "--version"], capture_output=True, text=True, cwd=str(tmp_path),
                         env={**os.environ, "HOME": str(home)})
    assert out.stdout.startswith("pettagam ")
    assert str(root / ".venv" / "bin" / "python") in (bindir / "pettagam").read_text()


def finder(snippet, **env):
    script = f'. "{ROOT}/launchers/find-python.sh"; {snippet}'
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                          env={**{k: v for k, v in os.environ.items() if k != "PETTAGAM_PYTHON"}, **env})


def test_python_finder_needs_3_13_and_prefers_the_venv(tmp_path):
    old = tmp_path / "python3.9"
    old.write_text("#!/bin/sh\nexit 1\n")  # what a too-old Python does with the version check
    old.chmod(0o755)
    assert finder(f'pettagam_python_ok "{old}"').returncode == 1
    assert finder(f'pettagam_python_ok "{sys.executable}"').returncode == 0
    assert finder('pettagam_python_ok ""').returncode == 1

    venv = tmp_path / "app" / ".venv" / "bin"
    venv.mkdir(parents=True)
    os.symlink(sys.executable, venv / "python")
    assert finder(f'pettagam_python "{tmp_path / "app"}"').stdout.strip() == str(venv / "python")
    # An explicit choice wins, but only if it is new enough.
    assert finder(f'pettagam_python "{tmp_path / "app"}"', PETTAGAM_PYTHON=str(old)).stdout.strip() == str(venv / "python")


def test_icon_is_drawn(tmp_path):
    subprocess.run([sys.executable, os.path.join(ROOT, "launchers", "make_icon.py"), str(tmp_path)], check=True)
    data = (tmp_path / "icon_16x16.png").read_bytes()
    assert data.startswith(b"\x89PNG") and len(os.listdir(tmp_path)) == 12


def test_update_launcher_explains_when_it_cannot_update(tmp_path):  # USE-13
    root = copy_of_repo(tmp_path)  # a copy without .git, like a downloaded zip
    r = run("Update Pettagam.command", tmp_path / "home", root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "wasn't downloaded with git" in r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr
