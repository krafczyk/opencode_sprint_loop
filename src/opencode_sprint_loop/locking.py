"""Linux advisory locks for run ownership and persistence consistency."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import ControllerError


@contextmanager
def advisory_lock(path: Path, *, exclusive: bool, blocking: bool = True) -> Iterator[None]:
    """Hold a Linux advisory lock, creating only its non-worktree lock file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
    except OSError as error:
        raise ControllerError("persistence_failed", f"Cannot create lock file: {path}") from error
    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError as error:
        handle.close()
        raise ControllerError("run_already_active", "Another Sprint Loop Controller process owns this sprint") from error
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def is_exclusively_locked(path: Path) -> bool:
    """Return whether another process currently holds the run ownership lock."""
    try:
        with advisory_lock(path, exclusive=True, blocking=False):
            return False
    except ControllerError as error:
        if error.code == "run_already_active":
            return True
        raise
