"""Linux advisory locks for run ownership and persistence consistency."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import ControllerError


@contextmanager
def advisory_lock(path: Path, *, exclusive: bool, blocking: bool = True) -> Iterator[None]:
    """Hold a Linux advisory lock or raise ``ControllerError``; creates only its lock file."""
    if os.path.lexists(path.parent) and path.parent.is_symlink():
        raise ControllerError("persistence_failed", f"Lock directory must not be a symlink: {path.parent}")
    if os.path.lexists(path) and path.is_symlink():
        raise ControllerError("persistence_failed", f"Lock file must not be a symlink: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(
                path.name,
                os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
        finally:
            os.close(directory)
        handle = os.fdopen(descriptor, "a+", encoding="utf-8")
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
