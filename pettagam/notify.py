"""A desktop notification after a background inbox pass. It never fails the pass: no desktop, no notice."""
from __future__ import annotations

import shutil
import subprocess
import sys

RUNNER = None  # tests swap in a fake


def _applescript_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, text: str, platform: str = None, runner=None) -> bool:
    plat = platform or sys.platform
    if plat == "darwin":
        args = ["osascript", "-e", f"display notification {_applescript_string(text)} "
                                   f"with title {_applescript_string(title)}"]
    elif plat.startswith("linux") and (runner or RUNNER or shutil.which("notify-send")):
        args = ["notify-send", "--app-name=Pettagam", title, text]
    else:
        return False
    try:
        return (runner or RUNNER or subprocess.run)(args, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def inbox_message(counts: dict) -> str:
    """'Filed 3 files into the library; 1 was already there.' or '' when nothing happened."""
    parts = []
    if counts.get("placed"):
        n = counts["placed"]
        parts.append(f"Filed {n} file{'s' * (n != 1)} into the library")
    if counts.get("duplicate"):
        n = counts["duplicate"]
        parts.append(f"{n} {'was' if n == 1 else 'were'} already there (moved to _duplicates)")
    if counts.get("error"):
        n = counts["error"]
        parts.append(f"{n} could not be filed: see the log")
    return "; ".join(parts) + "." if parts else ""
