"""Linux advisory locks for run ownership and persistence consistency."""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import ControllerError
from .safeio import open_directory, open_regular_at


@contextmanager
def advisory_lock(path: Path, *, exclusive: bool, blocking: bool = True) -> Iterator[None]:
    """Hold a Linux advisory lock or raise ``ControllerError``; creates only its lock file."""
    try:
        directory = open_directory(path.parent, create=True)
        descriptor = open_regular_at(directory, path.name, os.O_RDWR | os.O_CREAT | os.O_APPEND)
        handle = os.fdopen(descriptor, "a+", encoding="utf-8")
    except OSError as error:
        raise ControllerError("persistence_failed", f"Cannot create lock file: {path}") from error
    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
        locked = os.fstat(handle.fileno())
        current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != (locked.st_dev, locked.st_ino)
        ):
            raise ControllerError("persistence_failed", f"Lock file changed during acquisition: {path}")
    except BlockingIOError as error:
        handle.close()
        os.close(directory)
        raise ControllerError("run_already_active", "Another Sprint Loop Controller process owns this sprint") from error
    except BaseException:
        handle.close()
        os.close(directory)
        raise
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        os.close(directory)


def is_exclusively_locked(path: Path) -> bool:
    """Return whether another process currently holds the run ownership lock."""
    try:
        with advisory_lock(path, exclusive=True, blocking=False):
            return False
    except ControllerError as error:
        if error.code == "run_already_active":
            return True
        raise
