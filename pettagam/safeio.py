"""Careful file operations for every step that writes to the library, the inbox or an export folder.

- A copy is flushed to the disk and checked before it gets its name, and before anything relies on it
  (such as removing the inbox file it came from).
- A move or copy never replaces a file that is already there, even if one appears at the last moment.
- One step at a time writes to a library: placing, inbox sorting and undo take the same lock.
"""

import errno
import os
import shutil

from .hashing import hash_file

LOCK_NAME = "library.lock"


def fsync_file(path: str) -> None:
    """Make sure a file's content is on the disk itself, not just in memory (or the drive's cache on a Mac)."""
    with open(path, "rb") as f:
        try:
            import fcntl
            fcntl.fcntl(f.fileno(), fcntl.F_FULLFSYNC)  # macOS: plain fsync leaves it in the drive's cache
            return
        except (ImportError, AttributeError, OSError):
            pass
        os.fsync(f.fileno())


def fsync_dir(path: str) -> None:
    """Make a new name in a folder survive a power cut. Not every system allows this; then it is skipped."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def move_no_clobber(src: str, dst: str) -> None:
    """Rename src to dst on the same drive. Raises FileExistsError instead of replacing an existing dst.

    A hardlink then unlink does this atomically. Drives without hardlinks (exFAT, some network shares)
    fall back to checking first and renaming.
    """
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno == errno.EXDEV:
            raise
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "destination exists", dst) from None
        os.rename(src, dst)
        return
    os.unlink(src)


def durable_copy(src: str, dst: str, check: str = "size") -> None:
    """Copy src to dst by way of dst.part: flushed to disk, checked, then given its name.

    check is "size" (sizes match) or "hash" (the content matches, reading both in full). Never replaces
    an existing dst. On any failure the partial copy is removed and src is untouched.
    """
    tmp = dst + ".part"
    shutil.copy2(src, tmp)
    try:
        fsync_file(tmp)
        if os.path.getsize(tmp) != os.path.getsize(src) or (check == "hash" and hash_file(tmp) != hash_file(src)):
            raise OSError(errno.EIO, "the copy did not match the original", dst)
        move_no_clobber(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    fsync_dir(os.path.dirname(dst) or ".")


def same_drive(a: str, b: str) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


def move_file(src: str, dst: str) -> str:
    """Move src to dst, never replacing anything. Returns "move" (same drive) or "copy" (copied, checked
    in full, and only then removed from where it was)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "destination exists", dst)
    if same_drive(src, os.path.dirname(dst)):
        move_no_clobber(src, dst)
        fsync_dir(os.path.dirname(dst))
        return "move"
    durable_copy(src, dst, check="hash")
    os.remove(src)
    return "copy"


def private_dir(path: str) -> str:
    """Create a folder only this user can open (the catalog lists every file name, which is private)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        if os.stat(path).st_uid == os.getuid() and os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o700)
    except (AttributeError, OSError):  # Windows has no getuid; a folder we don't own is left alone
        pass
    return path


def library_lock(library: str):
    """Take the library's write lock without waiting. Returns the open lock file (close it to release),
    False when another step holds it, or None where locks aren't available (Windows)."""
    try:
        import fcntl
    except ImportError:
        return None
    meta = os.path.join(library, "_meta")
    os.makedirs(meta, exist_ok=True)
    f = open(os.path.join(meta, LOCK_NAME), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    return f


BUSY = "Another Pettagam step (placing, inbox sorting or undo) is writing to this library. Try again when it has finished."
