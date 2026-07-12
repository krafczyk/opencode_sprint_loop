"""Linux advisory locks for run ownership and persistence consistency."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import ControllerError
from .safeio import open_directory


@contextmanager
def advisory_lock(path: Path, *, exclusive: bool, blocking: bool = True) -> Iterator[None]:
    """Hold a Linux advisory lock on a dedicated non-worktree directory."""
    descriptor: int | None = None
    try:
        descriptor = open_directory(path, create=True)
    except OSError as error:
        raise ControllerError("persistence_failed", f"Cannot create lock directory: {path}") from error
    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(descriptor, flags)
    except BlockingIOError as error:
        os.close(descriptor)
        raise ControllerError("run_already_active", "Another Sprint Loop Controller process owns this sprint; wait for it to exit or use resume when available") from error
    except BaseException:
        os.close(descriptor)
        raise
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def exclusive_lock_pid(path: Path) -> int | None:
    """Return the Linux PID holding an exclusive advisory lock, if any."""
    try:
        descriptor = open_directory(path)
    except FileNotFoundError:
        return None
    try:
        details = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        records = Path("/proc/locks").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ControllerError("persistence_failed", "Cannot inspect Linux ownership locks") from error
    for record in records:
        fields = record.split()
        if len(fields) < 6 or fields[1:4] != ["FLOCK", "ADVISORY", "WRITE"]:
            continue
        try:
            pid = int(fields[4])
            device, inode = fields[5].rsplit(":", 1)
            major, minor = (int(value, 16) for value in device.split(":"))
            inode_value = int(inode)
        except (ValueError, IndexError):
            continue
        if (major, minor, inode_value) == (os.major(details.st_dev), os.minor(details.st_dev), details.st_ino):
            return pid
    return None


def is_exclusively_locked(path: Path) -> bool:
    """Return whether a process holds the anchor's exclusive advisory lock."""
    return exclusive_lock_pid(path) is not None
