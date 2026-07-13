"""Linux advisory locks for run ownership and persistence consistency."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .errors import ControllerError
from .safeio import open_directory, require_current_directory


@dataclass(frozen=True, slots=True)
class AdvisoryLock:
    """One acquired advisory lock whose anchor can be revalidated before use."""

    path: Path
    descriptor: int

    def ensure_current(self) -> None:
        """Fail closed if the lock anchor was replaced after acquisition."""
        require_current_directory(self.path, self.descriptor)


@dataclass(frozen=True, slots=True)
class OwnershipLock:
    """A run lock protected by a separate controller-owned namespace lock."""

    namespace: AdvisoryLock
    run: AdvisoryLock

    def ensure_current(self) -> None:
        """Fail closed if either ownership anchor changed after acquisition."""
        self.namespace.ensure_current()
        self.run.ensure_current()


@contextmanager
def advisory_lock(
    path: Path, *, exclusive: bool, blocking: bool = True, create: bool = True
) -> Iterator[AdvisoryLock]:
    """Hold a Linux advisory lock, creating its anchor when requested.

    Yields an anchor that callers must revalidate before durable mutation. Raises
    ``ControllerError`` when the anchor cannot be used or ownership is contested.
    """
    descriptor: int | None = None
    try:
        descriptor = open_directory(path, create=create)
    except FileNotFoundError:
        if not create:
            raise
        raise ControllerError(
            "persistence_failed", f"Cannot create lock directory: {path}"
        ) from None
    except OSError as error:
        raise ControllerError(
            "persistence_failed", f"Cannot create lock directory: {path}"
        ) from error
    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(descriptor, flags)
    except BlockingIOError as error:
        os.close(descriptor)
        raise ControllerError(
            "run_already_active",
            "Another Sprint Loop Controller process owns this sprint; wait for it to exit or use resume when available",
        ) from error
    except BaseException:
        os.close(descriptor)
        raise
    lock = AdvisoryLock(path, descriptor)
    try:
        lock.ensure_current()
        yield lock
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def ownership_lock(path: Path, *, blocking: bool = True) -> Iterator[OwnershipLock]:
    """Acquire exclusive controller ownership resilient to run-anchor replacement."""
    namespace_path = path.parent / "ownership"
    with advisory_lock(namespace_path, exclusive=True, blocking=blocking) as namespace:
        with advisory_lock(path, exclusive=True, blocking=blocking) as run:
            lock = OwnershipLock(namespace, run)
            lock.ensure_current()
            yield lock


@contextmanager
def persistence_lock(
    path: Path, *, exclusive: bool, blocking: bool = True, create: bool = True
) -> Iterator[AdvisoryLock]:
    """Serialize persistence readers and writers despite persistence-anchor replacement.

    A sibling namespace lock protects the replaceable visible persistence anchor.
    Raises ``ControllerError`` for contention or unsafe filesystem changes.
    """
    namespace = path.parent / "persistence-namespace"
    with advisory_lock(namespace, exclusive=exclusive, blocking=blocking, create=create):
        with advisory_lock(path, exclusive=exclusive, blocking=blocking, create=create) as lock:
            lock.ensure_current()
            yield lock


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
        raise ControllerError(
            "persistence_failed", "Cannot inspect Linux ownership locks"
        ) from error
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
        if (major, minor, inode_value) == (
            os.major(details.st_dev),
            os.minor(details.st_dev),
            details.st_ino,
        ):
            return pid
    return None


def is_exclusively_locked(path: Path) -> bool:
    """Return whether a process holds the anchor's exclusive advisory lock."""
    return exclusive_lock_pid(path) is not None
