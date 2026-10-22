"""Sort the inbox automatically in the background, without the panel or a Terminal window open.

On a Mac this is a LaunchAgent: it runs when something lands in the inbox, and every few minutes to
catch files that were still being copied. On Ubuntu it is a systemd user timer plus a path watch.
Both run `pettagam inbox` once per trigger, so a pass never overlaps another.

Alongside it, a weekly damage check (`pettagam fixity --minutes 30`) reads part of the library each
Sunday night, so over a few weeks every file is checked again, with a notification if any is damaged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from xml.sax.saxutils import escape

LABEL = "local.pettagam.inbox"
FIXITY_LABEL = "local.pettagam.fixity"
RUNNER = None  # tests swap in a fake so launchctl/systemctl are never really called
UNIT = "pettagam-inbox"
FIXITY_UNIT = "pettagam-fixity"
FIXITY_MINUTES = 30
LOG_MAX = 5 * 1024 * 1024  # the background log is started afresh beyond this, keeping one older file


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
        d = os.path.join(h, "Library", "LaunchAgents")
        return [os.path.join(d, f"{LABEL}.plist"), os.path.join(d, f"{FIXITY_LABEL}.plist")]
    d = os.path.join(h, ".config", "systemd", "user")
    return [os.path.join(d, f"{UNIT}.service"), os.path.join(d, f"{UNIT}.timer"), os.path.join(d, f"{UNIT}.path"),
            os.path.join(d, f"{FIXITY_UNIT}.service"), os.path.join(d, f"{FIXITY_UNIT}.timer")]


def command(db: str, inbox: str, library: str, python: str = None, notify: bool = True) -> list:
    return [python or sys.executable, "-m", "pettagam", "--db", os.path.abspath(db), "inbox",
            os.path.abspath(inbox), "--library", os.path.abspath(library)] + (["--notify"] if notify else [])


def fixity_command(db: str, library: str, python: str = None) -> list:
    return [python or sys.executable, "-m", "pettagam", "--db", os.path.abspath(db), "fixity",
            "--library", os.path.abspath(library), "--minutes", str(FIXITY_MINUTES), "--notify"]


def _package_root() -> str:
    """Folder that holds the pettagam package, so `-m pettagam` works even if it isn't pip-installed."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _plist(cmd: list, inbox: str, every: int, log: str, label: str = LABEL) -> str:
    args = "".join(f"\n    <string>{escape(a)}</string>" for a in cmd)
    if label == LABEL:
        when = f"""  <key>WatchPaths</key>
  <array><string>{escape(os.path.abspath(inbox))}</string></array>
  <key>StartInterval</key><integer>{int(every)}</integer>
  <key>RunAtLoad</key><true/>"""
    else:  # the damage check: Sunday at 3:00, or at the next wake if the Mac was asleep
        when = """  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>{escape(_package_root())}</string></dict>
{when}
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>{escape(log)}</string>
  <key>StandardErrorPath</key><string>{escape(log)}</string>
</dict>
</plist>
"""


def _quote(a: str) -> str:
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in a for c in ' "\\') else a


def _units(cmd: list, inbox: str, every: int, log: str, fixity_cmd: list = None) -> dict:
    exec_line = " ".join(_quote(a) for a in cmd)
    fixity_line = " ".join(_quote(a) for a in fixity_cmd or [])
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
        "fixity-service": f"""[Unit]
Description=Pettagam: weekly check of the library for silent damage

[Service]
Type=oneshot
Environment=PYTHONPATH={_package_root()}
ExecStart={fixity_line}
StandardOutput=append:{log}
StandardError=append:{log}
Nice=19
IOSchedulingClass=idle
""",
        "fixity-timer": """[Unit]
Description=Pettagam: weekly damage check

[Timer]
OnCalendar=Sun *-*-* 03:00:00
Persistent=true
Unit=pettagam-fixity.service

[Install]
WantedBy=timers.target
""",
    }


def _run(runner, args) -> bool:
    try:
        return (runner or RUNNER or subprocess.run)(args, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install(db: str, inbox: str, library: str, every: int = 300, platform=None, home=None,
            runner=None, python=None, notify: bool = True) -> dict:
    """Write and start the background job. Replaces an earlier one."""
    if not os.path.isdir(inbox):
        raise SystemExit(f"Inbox folder not found: {inbox}")
    every = max(60, int(every))
    plat = _platform(platform)
    if plat == "other":
        raise SystemExit("Automatic sorting is available on macOS and Linux. Use `pettagam inbox --watch` instead.")
    uninstall(platform=platform, home=home, runner=runner)
    h = _home(home)
    log = _log_path(home)
    from .safeio import private_dir
    private_dir(os.path.dirname(log))
    cmd = command(db, inbox, library, python, notify)
    paths = _paths(platform, home)
    os.makedirs(os.path.dirname(paths[0]), exist_ok=True)
    fx = fixity_command(db, library, python)
    if plat == "mac":
        started = True
        for p, label, c in zip(paths, (LABEL, FIXITY_LABEL), (cmd, fx)):
            with open(p, "w", encoding="utf-8") as f:
                f.write(_plist(c, inbox, every, log, label))
            uid = os.getuid()
            started = (_run(runner, ["launchctl", "bootstrap", f"gui/{uid}", p]) or
                       _run(runner, ["launchctl", "load", "-w", p])) and started
    else:
        for p, text in zip(paths, _units(cmd, inbox, every, log, fx).values()):
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        started = _run(runner, ["systemctl", "--user", "daemon-reload"]) and _run(
            runner, ["systemctl", "--user", "enable", "--now", f"{UNIT}.timer", f"{UNIT}.path", f"{FIXITY_UNIT}.timer"])
    state = {"inbox": os.path.abspath(inbox), "library": os.path.abspath(library), "db": os.path.abspath(db),
             "every": every, "log": log, "started": started, "notify": notify, "python": cmd[0]}
    with open(_state_file(home), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return state


def refresh(platform=None, home=None, runner=None, python=None):
    """Rewrite a background job that is on so it uses this Python (after an upgrade or a new .venv).
    Returns the new state, or None when the job is off or its inbox is gone."""
    st = status(platform, home)
    if not st["installed"] or not st.get("inbox") or not os.path.isdir(st["inbox"]):
        return None
    return install(st["db"], st["inbox"], st["library"], every=st.get("every", 300), platform=platform, home=home,
                   runner=runner, python=python, notify=st.get("notify", True))


def uninstall(platform=None, home=None, runner=None) -> bool:
    """Stop and remove the background job. The inbox, library and log are left alone."""
    plat = _platform(platform)
    paths = [p for p in _paths(platform, home) if os.path.exists(p)]
    if plat == "mac":
        uid = os.getuid()
        for p in paths:
            _run(runner, ["launchctl", "bootout", f"gui/{uid}", p]) or _run(runner, ["launchctl", "unload", "-w", p])
    elif plat == "linux" and paths:
        _run(runner, ["systemctl", "--user", "disable", "--now", f"{UNIT}.timer", f"{UNIT}.path", f"{FIXITY_UNIT}.timer"])
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
    installed = os.path.exists(_paths(platform, home)[0])
    try:
        with open(_state_file(home), encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    text = (f"On: {state.get('inbox', 'the inbox')} is sorted when files arrive and every "
            f"{int(state.get('every', 300)) // 60} minutes; the library is checked for damage on Sunday nights."
            if installed else "Off.")
    return {"installed": installed, "text": text, **state}


def _log_path(home=None) -> str:
    return os.path.join(_home(home), ".pettagam", "inbox-service.log")


def rotate_log(home=None, max_bytes: int = LOG_MAX) -> bool:
    """Keep the background log small: past max_bytes it becomes inbox-service.log.1 (replacing the older
    one) and a new log starts with the next run. Returns True when it rotated."""
    log = _log_path(home)
    try:
        if os.path.getsize(log) <= max_bytes:
            return False
        os.replace(log, log + ".1")
        return True
    except OSError:
        return False


def last_log(home=None, lines: int = 20) -> str:
    try:
        with open(_log_path(home), encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""
