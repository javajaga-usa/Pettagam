"""Updating Pettagam from GitHub, and noticing when the code on disk is newer than the running panel.

Pettagam runs from the folder it was downloaded to with git (the one Setup Pettagam is in). Updating is
`git pull --ff-only` there: it never merges and never overwrites a change made by hand, and says so
when it can't update. Nothing is asked for: with no internet, or no GitHub sign-in, it just says it
couldn't reach GitHub.
"""

import os
import re
import shutil
import subprocess

PACKAGE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(PACKAGE)
MAX_CHANGES = 30


def version_on_disk(package: str = PACKAGE) -> str:
    """The version in the package's __init__.py now, which after an update differs from the running one."""
    try:
        with open(os.path.join(package, "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)', f.read())
        return m.group(1) if m else ""
    except OSError:
        return ""


def code_stamp(package: str = PACKAGE) -> float:
    """When the newest of Pettagam's own files was last changed."""
    newest = 0.0
    try:
        for e in os.scandir(package):
            if e.name.endswith((".py", ".html")):
                newest = max(newest, e.stat().st_mtime)
    except OSError:
        pass
    return newest


def is_checkout(code: str = CODE) -> bool:
    return os.path.exists(os.path.join(code, ".git")) and shutil.which("git") is not None


def _git(code: str, *args, timeout: float = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}  # never stop to ask for a password
    try:
        return subprocess.run(["git", "-C", code, *args], capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def _lines(text: str) -> list:
    return [line for line in text.splitlines() if line.strip()]


def check(code: str = CODE, fetch: bool = True) -> dict:
    """Is a newer Pettagam on GitHub? {checkout, behind, changes, branch, error}."""
    if not is_checkout(code):
        return {"checkout": False, "behind": 0, "changes": [],
                "error": "This copy of Pettagam wasn't downloaded with git, so it can't update itself. "
                         "Download the new version from GitHub, or run: python3 -m pip install --upgrade ."}
    out = {"checkout": True, "behind": 0, "changes": [], "error": None}
    if fetch and _git(code, "fetch", "--quiet", timeout=90).returncode:
        out["error"] = "Could not reach GitHub to look for updates. Check the internet connection and try again."
    up = _git(code, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up.returncode:
        out["error"] = "This copy of Pettagam doesn't follow a branch on GitHub, so it can't update itself."
        return out
    out["branch"] = up.stdout.strip()
    behind = _git(code, "rev-list", "--count", "HEAD..@{u}").stdout.strip()
    out["behind"] = int(behind) if behind.isdigit() else 0
    if out["behind"]:
        out["changes"] = _lines(_git(code, "log", "--format=%s", f"-{MAX_CHANGES}", "HEAD..@{u}").stdout)
    return out


def update(code: str = CODE) -> dict:
    """Bring this copy up to date with GitHub. Raises SystemExit with a plain message when it can't."""
    if not is_checkout(code):
        raise SystemExit(check(code, fetch=False)["error"])
    before = _git(code, "rev-parse", "HEAD").stdout.strip()
    r = _git(code, "pull", "--ff-only", "--quiet", timeout=300)
    if r.returncode:
        err = (r.stderr or r.stdout).lower()
        if "would be overwritten" in err or "local changes" in err:
            raise SystemExit("Some of Pettagam's own files were changed on this computer, so updating would overwrite "
                             "them. Nothing was changed. Undo those edits (git status shows them), then update again.")
        if "fast-forward" in err or "diverg" in err:
            raise SystemExit("This copy of Pettagam has changes of its own that aren't on GitHub, so it can't simply "
                             "be updated. Nothing was changed.")
        if "could not resolve" in err or "unable to access" in err or "could not read" in err:
            raise SystemExit("Could not reach GitHub. Check the internet connection and try again. Nothing was changed.")
        raise SystemExit("Could not update: " + (_lines(r.stderr or r.stdout) or ["git pull failed"])[-1])
    after = _git(code, "rev-parse", "HEAD").stdout.strip()
    changes = _lines(_git(code, "log", "--format=%s", f"-{MAX_CHANGES}", f"{before}..{after}").stdout) if before != after else []
    files = _lines(_git(code, "diff", "--name-only", before, after).stdout) if before != after else []
    return {"updated": before != after, "changes": changes, "version": version_on_disk(os.path.join(code, "pettagam")),
            # The app in Applications is built by setup from the launchers, so a change there needs setup again.
            "setup_again": any(f.startswith("launchers/") or f.endswith(".command") for f in files)}
