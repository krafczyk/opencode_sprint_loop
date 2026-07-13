"""Human and JSON status projections from durable controller state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import __version__
from .config import SprintConfig
from .errors import ControllerError
from .events import load_events_at, validate_event_history
from .locking import exclusive_lock_pid
from .paths import RuntimePaths
from .safeio import open_directory, path_exists, require_current_directory
from .state import load_state_at, process_start_identity


def _no_run(root: Path) -> dict[str, Any]:
    """Return the stable JSON projection when no persisted run exists."""
    return {
        "schema_version": 1,
        "controller_version": __version__,
        "sprint_root": str(root),
        "run_exists": False,
        "process_running": False,
        "run_id": None,
        "sprint": None,
        "state": None,
        "reason": None,
        "active": None,
        "commits": None,
        "audit": None,
        "ci": None,
        "counters": None,
        "checklist": None,
        "last_event": None,
        "updated_at": None,
    }


def _process_running(run_lock: Path, paths: RuntimePaths, state: dict[str, Any]) -> bool:
    """Confirm the authoritative OS lock belongs to the persisted process."""
    process = state["process"]
    pid = process["pid"]
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    holder = exclusive_lock_pid(run_lock)
    if holder != pid:
        return False
    expected_start = process["process_start"]
    current_start = process_start_identity(pid)
    if expected_start is not None:
        return isinstance(expected_start, str) and expected_start == current_start
    if current_start is not None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def project_status(
    root: Path, config: SprintConfig, paths: RuntimePaths, run_lock: Path
) -> dict[str, Any]:
    """Load, validate, and project current state without mutating workflow data."""
    state, events = validate_persistence(paths, config)
    if state is None or events is None:
        return _no_run(root)
    event = events[-1]
    return {
        "schema_version": 1,
        "controller_version": __version__,
        "sprint_root": str(root),
        "run_exists": True,
        "process_running": _process_running(run_lock, paths, state),
        "run_id": state["run_id"],
        "sprint": {"multisprint": state["multisprint"], "index": state["sprint"]},
        "state": state["state"],
        "reason": None
        if state["reason"] is None
        else {
            "code": state["reason"]["code"],
            "message": state["reason"]["message"],
        },
        "active": {"role": None, "invocation_id": None, "session_id": None},
        "commits": state["commits"],
        "audit": {
            "phase": state["audit"]["phase"],
            "pre_ci_round": state["audit"]["pre_ci_round"],
            "pre_ci_max_rounds": state["audit"]["pre_ci_max_rounds"],
            "remaining_effort": state["audit"]["remaining_effort"],
        },
        "ci": {
            "status": state["ci"]["status"],
            "attempt": state["ci"]["attempt"],
            "commit_sha": state["ci"]["commit_sha"],
        },
        "counters": state["counters"],
        "checklist": {
            "satisfied": state["checklist"]["satisfied"],
            "partial": state["checklist"]["partial"],
            "unsatisfied": state["checklist"]["unsatisfied"],
            "not_evaluated": state["checklist"]["not_evaluated"],
            "assessed_at": state["checklist"]["assessed_at"],
        },
        "last_event": {
            "sequence": event["sequence"],
            "type": event["type"],
            "timestamp": event["timestamp"],
        },
        "updated_at": state["updated_at"],
    }


def validate_persistence(
    paths: RuntimePaths, config: SprintConfig
) -> tuple[dict[str, Any], list[dict[str, Any]]] | tuple[None, None]:
    """Load and cross-validate state/events or raise a corruption consistency error."""
    try:
        directory = open_directory(paths.info_dir)
    except FileNotFoundError:
        return None, None
    except OSError as error:
        raise ControllerError(
            "corrupt_state", f"Cannot inspect runtime directory: {paths.info_dir}"
        ) from error
    try:
        state_exists = path_exists(directory, paths.state.name)
        events_exists = path_exists(directory, paths.events.name)
        if not state_exists and not events_exists:
            return None, None
        if state_exists != events_exists:
            raise ControllerError(
                "inconsistent_persistence",
                "State and event log must either both exist or both be absent",
            )
        state = load_state_at(directory, paths.state.name, paths.state)
        if state["multisprint"] != config.multisprint or state["sprint"] != config.sprint:
            raise ControllerError(
                "inconsistent_persistence",
                "Persisted state does not match configured sprint identity",
            )
        expected_keys = {repository.name for repository in config.repositories}
        if (
            set(state["commits"]["local"]) != expected_keys
            or set(state["commits"]["pushed"]) != expected_keys
        ):
            raise ControllerError(
                "inconsistent_persistence",
                "Persisted commit maps do not match configured repository",
            )
        if state["audit"]["pre_ci_max_rounds"] != config.pre_ci_max_rounds:
            raise ControllerError(
                "inconsistent_persistence", "Persisted audit maximum does not match configuration"
            )
        events = load_events_at(directory, paths.events.name, paths.events)
        if not events:
            raise ControllerError("corrupt_event_log", "State exists but event log is empty")
        if events[-1]["sequence"] < state["last_event_sequence"]:
            raise ControllerError("corrupt_event_log", "Event log is behind persisted state")
        if events[-1]["sequence"] > state["last_event_sequence"]:
            raise ControllerError(
                "inconsistent_persistence", "Event log is ahead of persisted state"
            )
        validate_event_history(events)
        event = events[-1]
        if event["run_id"] != state["run_id"] or event["state"] != state["state"]:
            raise ControllerError("inconsistent_persistence", "State does not match its last event")
        if event["timestamp"] != state["updated_at"]:
            raise ControllerError(
                "inconsistent_persistence", "State update timestamp does not match its last event"
            )
        if event["payload"].get("reason") != state["reason"]:
            raise ControllerError(
                "inconsistent_persistence", "State reason does not match its last event"
            )
        require_current_directory(paths.info_dir, directory)
        return state, events
    finally:
        os.close(directory)


def format_status(status: dict[str, Any]) -> str:
    """Render concise human-readable status."""
    if not status["run_exists"]:
        return f"Sprint root: {status['sprint_root']}\nState: no run\n"
    sprint = status["sprint"]
    reason = status["reason"]
    lines = [
        f"Sprint root: {status['sprint_root']}",
        f"Sprint: {sprint['multisprint']} / {sprint['index']}",
        f"State: {status['state']}",
        f"Run: {status['run_id']}",
        f"Process running: {status['process_running']}",
    ]
    if reason is not None:
        lines.append(f"Reason: {reason['code']}: {reason['message']}")
    if status["last_event"] is not None:
        lines.append(
            f"Last event: {status['last_event']['type']} ({status['last_event']['timestamp']})"
        )
    return "\n".join(lines) + "\n"
