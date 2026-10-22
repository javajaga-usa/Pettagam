"""Traceability for docs/REQUIREMENTS.md: every test it names exists, and requirements that had no test
of their own before the audit have one here."""
import ast
import os
import re

from pettagam import classify as cl, service
from pettagam.doctor import run_checks
from pettagam.plan import folder_for
from pettagam.tag import TEXT_BYTES, content_for

from test_maintenance import placed  # noqa: F401  (fixture)
from test_pipeline import quiet  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "docs", "REQUIREMENTS.md")


def _tests_in(path: str) -> set:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


def test_every_named_test_exists():
    with open(SPEC, encoding="utf-8") as f:
        text = f.read()
    named = set(re.findall(r"(test_\w+)\.py::(test_\w+)", text))
    assert len(named) > 50
    missing = [f"{m}.py::{t}" for m, t in sorted(named)
               if t not in _tests_in(os.path.join(ROOT, "tests", f"{m}.py"))]
    assert not missing, f"REQUIREMENTS.md names tests that don't exist: {missing}"


def test_requirement_ids_are_unique():
    with open(SPEC, encoding="utf-8") as f:
        ids = re.findall(r"^\| ((?:FR|DS|SEC|PRIV|REL|PERF|USE|MAINT|PLAT)-[A-Z]*-?\d+) \|", f.read(), re.M)
    assert len(ids) > 80
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


# --- requirements that had no direct test --------------------------------------------

def test_weak_guesses_go_to_review():  # FR-PLAN-2
    assert folder_for(cl.Result("images", "photos", cl.REVIEW_BELOW - 0.1, "no strong clues"), "jpg") == "review/images"
    assert folder_for(cl.Result("images", "photos", cl.REVIEW_BELOW, "camera data"), "jpg") == "images/photos"
    assert folder_for(cl.Result("other", "xyz", 0.5, "unrecognised type"), "xyz") == "other/xyz"


def test_tagging_sends_only_the_start_of_a_text_file(tmp_path):  # PRIV-2
    p = tmp_path / "notes.txt"
    p.write_bytes(b"a" * (TEXT_BYTES * 3))
    blocks, _ = content_for(str(p), "txt")
    assert len(blocks[0]["text"]) <= TEXT_BYTES + len("<file>\n\n</file>")
    assert content_for(str(tmp_path / "notes.txt"), "mp3") is None  # unsupported kinds are never sent


def test_health_check_changes_nothing(placed):  # noqa: F811  FR-DOCTOR-3
    con, src, lib, dbp = placed

    def snapshot(root):
        return sorted((os.path.relpath(os.path.join(d, f), root), os.lstat(os.path.join(d, f)).st_mtime)
                      for d, _, fs in os.walk(root) for f in fs)
    before = snapshot(src), snapshot(lib)
    run_checks(con, db_path=str(dbp))
    assert (snapshot(src), snapshot(lib)) == before


def test_background_jobs_run_at_low_priority_as_the_user(tmp_path):  # SEC-9, PERF-5
    cmd = service.command(str(tmp_path / "p.db"), str(tmp_path), str(tmp_path / "lib"))
    plist = service._plist(cmd, str(tmp_path), 300, str(tmp_path / "log"))
    assert "<key>LowPriorityIO</key><true/>" in plist and "<key>Nice</key><integer>10</integer>" in plist
    assert "UserName" not in plist and "sudo" not in plist
    units = service._units(cmd, str(tmp_path), 300, str(tmp_path / "log"),
                           service.fixity_command(str(tmp_path / "p.db"), str(tmp_path / "lib")))
    assert "Nice=10" in units["service"] and "IOSchedulingClass=idle" in units["service"]
    assert not any("User=" in u for u in units.values())  # user units: they run as the owner


def test_python_minimum_is_the_same_everywhere():  # PLAT-7
    import tomllib

    import pettagam
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        project = tomllib.load(f)["project"]
    want = "{}.{}".format(*pettagam.MIN_PYTHON)
    assert want == "3.13"
    assert project["requires-python"] == ">=" + want
    assert f"Programming Language :: Python :: {want}" in project["classifiers"]
    with open(os.path.join(ROOT, "launchers", "find-python.sh"), encoding="utf-8") as f:
        sh = f.read()
    assert f'PETTAGAM_MIN_PYTHON="{want}"' in sh and "sys.version_info >= (3, 13)" in sh
    with open(os.path.join(ROOT, ".github", "workflows", "tests.yml"), encoding="utf-8") as f:
        ci = f.read()
    assert re.search(rf'python: \["{re.escape(want)}"', ci)
    for doc in ("README.md", os.path.join("docs", "REQUIREMENTS.md")):
        with open(os.path.join(ROOT, doc), encoding="utf-8") as f:
            assert "3.9 or newer" not in f.read(), doc


def test_licence_is_declared():  # MAINT-5
    import tomllib
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        project = tomllib.load(f)["project"]
    assert project["license"] == "MIT" and project["license-files"] == ["LICENSE"]
    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f:
        assert f.readline().strip() == "MIT License"
