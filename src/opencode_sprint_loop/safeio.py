"""Descriptor-anchored filesystem helpers for controller runtime artifacts."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .errors import ControllerError


def open_directory(path: Path, *, create: bool = False) -> int:
    """Open an absolute non-symlink directory and return a caller-owned descriptor.

    Creates missing components with owner-only permissions when ``create`` is true.
    Raises ``OSError`` when a component is unsafe or unavailable.
    """
    absolute = path.absolute()
    if not absolute.is_absolute():  # pragma: no cover - Path.absolute guarantees this.
        raise ControllerError("persistence_failed", f"Directory path must be absolute: {path}")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in absolute.parts[1:]:
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    # Another controller may have created this lock-path component
                    # after our failed open. Re-open it with the same no-follow rules.
                    pass
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            os.close(descriptor)
            descriptor = child
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def open_regular(
    path: Path,
    flags: int,
    *,
    create_parent: bool = False,
    mode: int = 0o600,
) -> tuple[int, int]:
    """Open a single-link regular file through an anchored parent descriptor.

    The caller owns and must close both returned descriptors. Rejecting hardlinks
    avoids reading from or appending to a controller artifact aliased elsewhere.
    A zero link count is a safe, complete snapshot unlinked by a concurrent atomic
    replacement after this descriptor was opened.
    """
    directory = open_directory(path.parent, create=create_parent)
    try:
        descriptor = open_regular_at(directory, path.name, flags, mode=mode)
        return descriptor, directory
    except BaseException:
        os.close(directory)
        raise


def open_regular_at(directory: int, name: str, flags: int, *, mode: int = 0o600) -> int:
    """Open one single-link regular file relative to an anchored directory.

    The caller owns the returned descriptor. Nonblocking open prevents FIFOs
    substituted after preflight from stalling controller operations.
    """
    descriptor = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode, dir_fd=directory)
    details = os.fstat(descriptor)
    readable_snapshot = flags & os.O_ACCMODE == os.O_RDONLY and details.st_nlink == 0
    if not stat.S_ISREG(details.st_mode) or (details.st_nlink != 1 and not readable_snapshot):
        os.close(descriptor)
        raise ControllerError(
            "persistence_failed", f"Runtime artifact must be an unlinked regular file: {name}"
        )
    return descriptor


def path_exists(directory: int, name: str) -> bool:
    """Return whether a directory entry exists without following a symlink."""
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def require_current_directory(path: Path, directory: int) -> None:
    """Raise ``ControllerError`` if path no longer identifies the open directory."""
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ControllerError(
            "persistence_failed", f"Runtime directory changed during operation: {path}"
        ) from error
    anchored = os.fstat(directory)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        anchored.st_dev,
        anchored.st_ino,
    ):
        raise ControllerError(
            "persistence_failed", f"Runtime directory changed during operation: {path}"
        )
