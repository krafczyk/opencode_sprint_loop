"""Path validation and runtime path derivation."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import ControllerError


def canonical_root(value: str) -> Path:
    """Return an existing canonical sprint root directory."""
    path = Path(value).expanduser()
    if not path.exists():
        raise ControllerError("root_not_found", f"Sprint root does not exist: {path.absolute()}")
    if not path.is_dir():
        raise ControllerError("root_not_found", f"Sprint root is not a directory: {path.absolute()}")
    return path.resolve()


def resolve_within(root: Path, value: str, *, field: str, require_exists: bool = False) -> Path:
    """Resolve a relative path and reject escapes from the canonical root."""
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ControllerError("invalid_config", f"{field} must be a relative path within {root}")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ControllerError("invalid_config", f"{field} resolves outside sprint root: {value}") from error
    if require_exists and not resolved.exists():
        raise ControllerError("missing_required_file", f"{field} does not exist: {resolved}")
    return resolved


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Validated filesystem locations for one configured sprint."""

    info_dir: Path
    state: Path
    events: Path
    lock_metadata: Path


def runtime_paths(root: Path, multisprint: str, sprint: int) -> RuntimePaths:
    """Derive runtime locations from validated sprint identity."""
    info_dir = root / "info" / multisprint / str(sprint)
    return RuntimePaths(
        info_dir=info_dir,
        state=info_dir / "state.json",
        events=info_dir / "events.jsonl",
        lock_metadata=info_dir / "lock.json",
    )


def ensure_runtime_paths_safe(root: Path, paths: RuntimePaths) -> None:
    """Reject existing runtime path symlinks or non-directory path components."""
    components = (root / "info", root / "info" / paths.info_dir.parent.name, paths.info_dir)
    for component in components:
        if os.path.lexists(component):
            mode = os.lstat(component).st_mode
            if stat.S_ISLNK(mode):
                raise ControllerError("inconsistent_persistence", f"Runtime path must not be a symlink: {component}")
            if not stat.S_ISDIR(mode):
                raise ControllerError("inconsistent_persistence", f"Runtime path must be a directory: {component}")
    for artifact in (paths.state, paths.events, paths.lock_metadata):
        if os.path.lexists(artifact):
            mode = os.lstat(artifact).st_mode
            if not stat.S_ISREG(mode):
                raise ControllerError("inconsistent_persistence", f"Runtime artifact must be a regular file: {artifact}")
