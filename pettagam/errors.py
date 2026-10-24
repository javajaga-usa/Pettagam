"""Plain-language explanations for the problems a person can actually fix: a drive that was unplugged,
a full disk, macOS not letting Python read a folder, the catalog being busy. Used by the command line and
the panel, which also keep the technical details for a bug report."""

import errno
import os
import sqlite3
import sys
import time
import traceback

HOME = os.path.join(os.path.expanduser("~"), ".pettagam")


def _where(e: OSError) -> str:
    return f" ({e.filename})" if getattr(e, "filename", None) else ""


def _on_drive(path) -> bool:
    return bool(path) and str(path).startswith(("/Volumes/", "/media/", "/mnt/", "/srv/", "/run/media/"))


def explain(e: BaseException) -> str:
    """One or two sentences saying what went wrong and what to do about it."""
    if isinstance(e, KeyboardInterrupt):
        return "Stopped."
    if isinstance(e, SystemExit):
        return str(e)
    if isinstance(e, sqlite3.OperationalError):
        text = str(e).lower()
        if "locked" in text or "busy" in text:
            return ("The catalog is busy: another Pettagam step (in the panel, the background inbox job or another "
                    "Terminal window) is using it. Wait for that to finish, then try again.")
        if "unable to open" in text:
            return ("Pettagam could not open its catalog. Check that the computer's disk isn't full, then restart "
                    "Pettagam. If this keeps happening, update Pettagam (pettagam update).")
        if "disk" in text and ("full" in text or "i/o" in text):
            return "The catalog could not be saved: the computer's disk is full or failing. Free some space and try again."
        return f"The catalog reported a problem: {e}."
    if isinstance(e, sqlite3.DatabaseError):
        return (f"The catalog file is damaged ({e}). Pettagam keeps a copy in the library's _meta folder: "
                "see 'Moving to Ubuntu later' in the README for how to start from it (pettagam relocate --catalog-from).")
    if isinstance(e, PermissionError):
        mac = (" On a Mac, open System Settings → Privacy & Security → Files and Folders (or Full Disk Access) "
               "and allow Terminal and Python, then try again.") if sys.platform == "darwin" else ""
        return f"Not allowed to read or change a file or folder{_where(e)}.{mac}"
    if isinstance(e, FileNotFoundError):
        if _on_drive(e.filename):
            return f"Something Pettagam needs is missing{_where(e)}. Is the drive plugged in? Plug it in and try again."
        return f"A file or folder Pettagam needs is missing{_where(e)}. It may have been moved or renamed."
    if isinstance(e, OSError):
        code = e.errno
        if code == errno.ENOSPC:
            return f"The drive is full{_where(e)}. Free some space (the health check shows how much is needed) and try again."
        if code == errno.EIO:
            return (f"The drive could not read or write a file{_where(e)}. It may have been unplugged, or it may be "
                    "failing: check the cable, run Check for damage, and copy what you can to another drive.")
        if code == errno.EROFS:
            return f"The drive only allows reading{_where(e)}. Choose a library on a drive Pettagam can write to."
        if code == errno.ENAMETOOLONG:
            return f"A file name or path is too long for the drive{_where(e)}."
        if code == errno.EMFILE:
            return "Too many files are open at once. Restart Pettagam, and update it if this keeps happening."
        if code in (errno.ENXIO, errno.ENODEV, getattr(errno, "ESTALE", -1)):
            return f"The drive went away{_where(e)}. Plug it back in and run the step again: it carries on."
        return f"{e.strerror or e}{_where(e)}."
    return f"{type(e).__name__}: {e}"


def save_details(e: BaseException, what: str = "", home: str = None) -> str | None:
    """Write the full technical details of E to ~/.pettagam/last-error.txt, for a bug report. Returns the path."""
    home = home or HOME
    try:
        os.makedirs(home, exist_ok=True)
        path = os.path.join(home, "last-error.txt")
        from . import __version__
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Pettagam {__version__}, Python {sys.version.split()[0]}, {sys.platform}\n"
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {what}\n\n")
            f.write("".join(traceback.format_exception(e)))
        return path
    except OSError:
        return None


def is_bug(e: BaseException) -> bool:
    """True for an error that is Pettagam's fault rather than the drive's, the disk's or the person's."""
    return not isinstance(e, (OSError, sqlite3.Error, SystemExit, KeyboardInterrupt))
