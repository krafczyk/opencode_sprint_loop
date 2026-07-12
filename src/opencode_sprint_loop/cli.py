"""Command-line interface for the Sprint Loop Controller foundation."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import NoReturn, Sequence

from . import __version__
from .config import SprintConfig, load_config
from .errors import ControllerError
from .git import validate_preflight, validate_root
from .locking import advisory_lock
from .paths import RuntimePaths, canonical_root, ensure_runtime_paths_safe, runtime_paths
from .safeio import open_directory
from .state import new_state, process_start_identity
from .status import format_status, project_status, validate_persistence
from .transitions import persist_initial, transition


class _ArgumentParser(argparse.ArgumentParser):
    """Translate parser failures into the controller's stable error contract."""

    def error(self, message: str) -> NoReturn:
        raise ControllerError("invalid_arguments", message)


def _parser() -> argparse.ArgumentParser:
    """Build the stable V1 command parser."""
    parser = _ArgumentParser(prog="sprint-loop")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--root", required=True)
    run.add_argument("--server-url", required=True)
    status = commands.add_parser("status")
    status.add_argument("--root", required=True)
    status.add_argument("--json", action="store_true")
    pause = commands.add_parser("pause")
    pause.add_argument("--root", required=True)
    resume = commands.add_parser("resume")
    resume.add_argument("--root", required=True)
    resume.add_argument("--server-url", required=True)
    stop = commands.add_parser("stop")
    stop.add_argument("--root", required=True)
    return parser


def _lock_paths(git_dir: Path) -> tuple[Path, Path]:
    """Return non-worktree run ownership and persistence lock locations."""
    base = git_dir / "opencode-sprint-loop"
    return base / "run.lock", base / "persistence.lock"


def _load_root_config(root_value: str) -> tuple[Path, SprintConfig, RuntimePaths, Path, Path]:
    """Validate root identity and load configuration needed by all commands."""
    root = canonical_root(root_value)
    repository = validate_root(root)
    config = load_config(root)
    paths = runtime_paths(root, config.multisprint, config.sprint)
    ensure_runtime_paths_safe(root, paths)
    run_lock, persistence_lock = _lock_paths(repository.git_dir)
    return root, config, paths, run_lock, persistence_lock


def _existing_run(paths: RuntimePaths, config: SprintConfig) -> None:
    """Reject any existing Sprint 1 state or event artifacts before preflight."""
    state_exists = paths.state.exists()
    events_exists = paths.events.exists()
    if state_exists and events_exists:
        validate_persistence(paths, config)
        raise ControllerError("run_already_exists", "A persisted Sprint 1 run already exists; resume policy is not implemented")
    if state_exists or events_exists:
        raise ControllerError("inconsistent_persistence", "Incomplete existing state or event artifacts require manual inspection")


def _write_lock_metadata(path: Path, state: dict[str, object]) -> None:
    """Write descriptive lock metadata after ownership and preflight succeed."""
    metadata = {
        "schema_version": 1,
        "run_id": state["run_id"],
        "pid": os.getpid(),
        "process_start": process_start_identity(os.getpid()),
        "hostname": socket.gethostname(),
        "started_at": state["created_at"],
    }
    temporary_name: str | None = None
    directory: int | None = None
    try:
        directory = open_directory(path.parent, create=True)
        temporary_name = f".lock-{secrets.token_hex(16)}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            payload = (json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8")
            if os.write(descriptor, payload) != len(payload):
                raise OSError("Short write while persisting lock metadata")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary_name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except OSError as error:
        raise ControllerError("persistence_failed", f"Could not persist lock metadata: {path}") from error
    finally:
        if temporary_name is not None and directory is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
        if directory is not None:
            os.close(directory)


def _persist_best_effort_failure(paths: RuntimePaths, config: SprintConfig, persistence_lock: Path) -> None:
    """Record a safe failed transition only when the current durable pair is consistent."""
    if not paths.state.exists() or not paths.events.exists():
        return
    try:
        state, _ = validate_persistence(paths, config)
        if state["state"] not in {"initializing", "validating"}:
            return
        transition(
            state,
            paths.events,
            paths.state,
            persistence_lock,
            "failed",
            reason={
                "code": "internal_error",
                "message": "Controller failed after durable state became available.",
                "details": {},
            },
        )
    except (ControllerError, OSError):
        # Preserve the original failure and never overwrite inconsistent evidence.
        return


def _run(root_value: str, server_url: str) -> int:
    """Execute the intentional Sprint 1 placeholder workflow without network access."""
    if not server_url:
        raise ControllerError("invalid_arguments", "--server-url must be non-empty")
    root, config, paths, run_lock, persistence_lock = _load_root_config(root_value)
    with advisory_lock(persistence_lock, exclusive=False):
        _existing_run(paths, config)
    allowed = {paths.lock_metadata.relative_to(root).as_posix()} if paths.lock_metadata.exists() else set()
    validate_preflight(root, config, require_clean=True, allowed_root_untracked=allowed)
    with advisory_lock(run_lock, exclusive=True, blocking=False):
        try:
            # Reload after ownership so a concurrent clean configuration change
            # cannot direct this run to stale runtime paths or repository data.
            root, config, paths, post_lock_run_lock, post_lock_persistence_lock = _load_root_config(root_value)
            if post_lock_run_lock != run_lock or post_lock_persistence_lock != persistence_lock:
                raise ControllerError("internal_error", "Controller lock location changed during preflight")
            _existing_run(paths, config)
            allowed = {paths.lock_metadata.relative_to(root).as_posix()} if paths.lock_metadata.exists() else set()
            validate_preflight(root, config, require_clean=True, allowed_root_untracked=allowed)
            # The first transition includes metadata creation under one exclusive
            # persistence lock so status sees either no run or a complete state.
            with advisory_lock(persistence_lock, exclusive=True):
                state = new_state(config)
                _write_lock_metadata(paths.lock_metadata, state)
                state = persist_initial(state, paths.events, paths.state, persistence_lock, lock_held=True)
            state = transition(state, paths.events, paths.state, persistence_lock, "validating")
            state = transition(
                state,
                paths.events,
                paths.state,
                persistence_lock,
                "blocked",
                reason={
                    "code": "execution_not_implemented",
                    "message": "Sprint execution begins in a later implementation sprint.",
                    "details": {},
                },
            )
        except BaseException:
            _persist_best_effort_failure(paths, config, persistence_lock)
            raise
    sys.stderr.write("execution_not_implemented: Sprint execution begins in a later implementation sprint.\n")
    return 4


def _status(root_value: str, as_json: bool) -> int:
    """Print current durable status without requiring a clean worktree."""
    root, config, paths, run_lock, persistence_lock = _load_root_config(root_value)
    with advisory_lock(persistence_lock, exclusive=False):
        status = project_status(root, config, paths, run_lock)
    if as_json:
        sys.stdout.write(json.dumps(status, sort_keys=True) + "\n")
    else:
        sys.stdout.write(format_status(status))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and convert expected controller failures to safe diagnostics."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
        if arguments.command == "run":
            return _run(arguments.root, arguments.server_url)
        if arguments.command == "status":
            return _status(arguments.root, arguments.json)
        if arguments.command == "resume" and not arguments.server_url:
            raise ControllerError("invalid_arguments", "--server-url must be non-empty")
        # Reserved controls still validate their root and configuration before
        # reporting that their Sprint 1 coordination semantics are unavailable.
        _load_root_config(arguments.root)
        raise ControllerError("feature_not_implemented", f"{arguments.command} is not implemented in Sprint 1")
    except ControllerError as error:
        sys.stderr.write(f"{error.code}: {error.message}\n")
        return 2
    except SystemExit:
        raise
    except Exception:
        sys.stderr.write("internal_error: Unexpected controller failure; inspect local controller logs and retry\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
