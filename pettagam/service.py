"""Sort the inbox automatically in the background, without the panel or a Terminal window open.

On a Mac this is a LaunchAgent: it runs when something lands in the inbox, and every few minutes to
catch files that were still being copied. On Ubuntu it is a systemd user timer plus a path watch.
Both run `pettagam inbox` once per trigger, so a pass never overlaps another.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from xml.sax.saxutils import escape

LABEL = "local.pettagam.inbox"
RUNNER = None  # tests swap in a fake so launchctl/systemctl are never really called
UNIT = "pettagam-inbox"


def _home(home=None) -> str:
    return home or os.path.expanduser("~")


def _state_file(home=None) -> str:
    return os.path.join(_home(home), ".pettagam", "service.json")


def _platform(platform=None) -> str:
    p = platform or sys.platform
    return "mac" if p == "darwin" else "linux" if p.startswith("linux") else "other"


def _paths(platform=None, home=None) -> list:
    h = _home(home)
    if _platform(platform) == "mac":
        return [os.path.join(h, "Library", "LaunchAgents", f"{LABEL}.plist")]
    d = os.path.join(h, ".config", "systemd", "user")
    return [os.path.join(d, f"{UNIT}.service"), os.path.join(d, f"{UNIT}.timer"), os.path.join(d, f"{UNIT}.path")]


def command(db: str, inbox: str, library: str, python: str = None) -> list:
    return [python or sys.executable, "-m", "pettagam", "--db", os.path.abspath(db), "inbox",
            os.path.abspath(inbox), "--library", os.path.abspath(library)]


def _package_root() -> str:
    """Folder that holds the pettagam package, so `-m pettagam` works even if it isn't pip-installed."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _plist(cmd: list, inbox: str, every: int, log: str) -> str:
    args = "".join(f"\n    <string>{escape(a)}</string>" for a in cmd)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>{escape(_package_root())}</string></dict>
  <key>WatchPaths</key>
  <array><string>{escape(os.path.abspath(inbox))}</string></array>
  <key>StartInterval</key><integer>{int(every)}</integer>
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>{escape(log)}</string>
  <key>StandardErrorPath</key><string>{escape(log)}</string>
</dict>
</plist>
"""


def _quote(a: str) -> str:
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in a for c in ' "\\') else a


def _units(cmd: list, inbox: str, every: int, log: str) -> dict:
    exec_line = " ".join(_quote(a) for a in cmd)
    return {
        "service": f"""[Unit]
Description=Pettagam: file new files from the inbox into the library

[Service]
Type=oneshot
Environment=PYTHONPATH={_package_root()}
ExecStart={exec_line}
StandardOutput=append:{log}
StandardError=append:{log}
Nice=10
IOSchedulingClass=idle
""",
        "timer": f"""[Unit]
Description=Pettagam: check the inbox every {every} seconds

[Timer]
OnBootSec=60
OnUnitActiveSec={int(every)}
Unit={UNIT}.service

[Install]
WantedBy=timers.target
""",
        "path": f"""[Unit]
Description=Pettagam: sort the inbox when something arrives

[Path]
PathChanged={os.path.abspath(inbox)}
Unit={UNIT}.service

[Install]
WantedBy=paths.target
""",
    }


def _run(runner, args) -> bool:
    try:
        return (runner or RUNNER or subprocess.run)(args, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install(db: str, inbox: str, library: str, every: int = 300, platform=None, home=None,
            runner=None, python=None) -> dict:
    """Write and start the background job. Replaces an earlier one."""
    if not os.path.isdir(inbox):
        raise SystemExit(f"Inbox folder not found: {inbox}")
    every = max(60, int(every))
    plat = _platform(platform)
    if plat == "other":
        raise SystemExit("Automatic sorting is available on macOS and Linux. Use `pettagam inbox --watch` instead.")
    uninstall(platform=platform, home=home, runner=runner)
    h = _home(home)
    log = os.path.join(h, ".pettagam", "inbox-service.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    cmd = command(db, inbox, library, python)
    paths = _paths(platform, home)
    os.makedirs(os.path.dirname(paths[0]), exist_ok=True)
    if plat == "mac":
        with open(paths[0], "w", encoding="utf-8") as f:
            f.write(_plist(cmd, inbox, every, log))
        uid = os.getuid()
        started = _run(runner, ["launchctl", "bootstrap", f"gui/{uid}", paths[0]]) or \
            _run(runner, ["launchctl", "load", "-w", paths[0]])
    else:
        for p, text in zip(paths, _units(cmd, inbox, every, log).values()):
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        started = _run(runner, ["systemctl", "--user", "daemon-reload"]) and _run(
            runner, ["systemctl", "--user", "enable", "--now", f"{UNIT}.timer", f"{UNIT}.path"])
    state = {"inbox": os.path.abspath(inbox), "library": os.path.abspath(library), "db": os.path.abspath(db),
             "every": every, "log": log, "started": started}
    with open(_state_file(home), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return state


def uninstall(platform=None, home=None, runner=None) -> bool:
    """Stop and remove the background job. The inbox, library and log are left alone."""
    plat = _platform(platform)
    paths = [p for p in _paths(platform, home) if os.path.exists(p)]
    if plat == "mac" and paths:
        uid = os.getuid()
        _run(runner, ["launchctl", "bootout", f"gui/{uid}", paths[0]]) or \
            _run(runner, ["launchctl", "unload", "-w", paths[0]])
    elif plat == "linux" and paths:
        _run(runner, ["systemctl", "--user", "disable", "--now", f"{UNIT}.timer", f"{UNIT}.path"])
    for p in paths:
        os.remove(p)
    if plat == "linux" and paths:
        _run(runner, ["systemctl", "--user", "daemon-reload"])
    try:
        os.remove(_state_file(home))
    except OSError:
        pass
    return bool(paths)


def status(platform=None, home=None) -> dict:
    installed = any(os.path.exists(p) for p in _paths(platform, home))
    try:
        with open(_state_file(home), encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    text = (f"On: {state.get('inbox', 'the inbox')} is sorted when files arrive and every "
            f"{int(state.get('every', 300)) // 60} minutes." if installed else "Off.")
    return {"installed": installed, "text": text, **state}


def last_log(home=None, lines: int = 20) -> str:
    try:
        with open(os.path.join(_home(home), ".pettagam", "inbox-service.log"), encoding="utf-8",
                  errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""
