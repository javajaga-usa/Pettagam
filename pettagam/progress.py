"""Long steps: how far they got and how long is left, a way to stop them, and keeping the computer awake.

Every step reports progress through a plain callable that takes one line of text. Progress wraps such a
callable: when a line says "N of M" (or "N/M") it adds the percentage and the time left, and when someone
asked the step to stop, the next line raises Stopped instead. Steps report at least every 10 seconds, so a
stop takes effect within about that long, and each step leaves the catalog so that running it again
carries on or starts cleanly.
"""

import contextlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time

FRACTION = re.compile(r"(\d[\d,]*)\s*(?:of|/)\s*(?:about\s+)?(\d[\d,]*)")


class Stopped(Exception):
    """The person asked the running step to stop."""

    def __init__(self, msg: str = "Stopped on request."):
        super().__init__(msg)


def time_left(seconds: float) -> str:
    """'less than a minute', 'about 12 min', 'about 1 h 20 min'."""
    if seconds < 60:
        return "less than a minute"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"about {minutes} min"
    return f"about {minutes // 60} h {minutes % 60:02d} min"


class Progress:
    """A progress callable: progress(line). See the module docstring."""

    def __init__(self, sink=print, clock=time.time):
        self.sink, self.clock = sink, clock
        self.stop_event = threading.Event()
        self.percent = self.eta = None
        self._phase = None  # (words around the numbers, total) of the "N of M" lines being counted
        self._base = (clock(), 0)  # when and at what count the current phase started
        self._last = clock()

    def stop(self) -> None:
        self.stop_event.set()

    def check(self) -> None:
        """Raise Stopped if a stop was asked for. Steps call this between pieces of work."""
        if self.stop_event.is_set():
            raise Stopped()

    def __call__(self, msg: str) -> None:
        self.check()
        now = self.clock()
        m = FRACTION.search(msg)
        if m:
            done, total = (int(g.replace(",", "")) for g in m.groups())
            key = (FRACTION.sub("#", msg.split("(")[0]).strip(), total)
            if key != self._phase:  # a new kind of count started, at about the time of the line before
                self._phase, self._base = key, (self._last, 0)
            if total:
                self.percent = min(99, int(100 * done / total)) if done < total else 99
                t0, n0 = self._base
                rate = (done - n0) / (now - t0) if now > t0 and done > n0 else 0
                self.eta = (total - done) / rate if rate and done < total else None
                left = f", {time_left(self.eta)} left" if self.eta is not None else ""
                msg = f"{msg} · {self.percent}%{left}"
        else:
            self._phase, self.percent, self.eta = None, None, None
        self._last = now
        self.sink(msg)


def stop_if_asked(progress) -> None:
    """Let a plain callable (print, list.append) through; ask a Progress whether to stop."""
    if isinstance(progress, Progress):
        progress.check()


@contextlib.contextmanager
def keep_awake():
    """Stop a Mac from going to sleep while a long step runs (the display may still turn off)."""
    proc = None
    if sys.platform == "darwin" and shutil.which("caffeinate") and not os.environ.get("PETTAGAM_ALLOW_SLEEP"):
        try:
            # -i: no idle sleep; -w: give up by itself if this process ends without saying so.
            proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            proc = None
    try:
        yield proc
    finally:
        if proc:
            with contextlib.suppress(OSError):
                proc.terminate()
