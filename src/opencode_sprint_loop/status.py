"""Human and JSON status projections from durable controller state."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

from . import __version__
from .config import SprintConfig
from .errors import ControllerError
from .events import load_events, validate_event_history
from .jsonio import load_json_object_handle
from .locking import is_exclusively_locked
from .paths import RuntimePaths
from .safeio import open_regular
from .state import load_state, process_start_identity


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
    """Confirm lock ownership belongs to the persisted run when identity is available."""
    if not is_exclusively_locked(run_lock):
        return False
    try:
        descriptor, directory = open_regular(paths.lock_metadata, os.O_RDONLY)
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                metadata = load_json_object_handle(handle, paths.lock_metadata, code="corrupt_state")
        finally:
            os.close(directory)
    except (ControllerError, FileNotFoundError):
        return False
    process = state["process"]
    required = {"schema_version", "run_id", "pid", "process_start", "hostname", "started_at"}
    if set(metadata) != required or metadata["schema_version"] != 1:
        return False
    if metadata["run_id"] != state["run_id"] or metadata["hostname"] != socket.gethostname():
        return False
    if metadata["pid"] != process["pid"] or metadata["process_start"] != process["process_start"]:
        return False
    if not isinstance(metadata["pid"], int) or isinstance(metadata["pid"], bool) or metadata["pid"] <= 0:
        return False
    expected_start = metadata["process_start"]
    current_start = process_start_identity(metadata["pid"])
    if expected_start is not None:
        return isinstance(expected_start, str) and expected_start == current_start
    try:
        os.kill(metadata["pid"], 0)
    except OSError:
        return False
    return True


def project_status(root: Path, config: SprintConfig, paths: RuntimePaths, run_lock: Path) -> dict[str, Any]:
    """Load, validate, and project current state without mutating workflow data."""
    state_exists = paths.state.exists()
    events_exists = paths.events.exists()
    if not state_exists and not events_exists:
        return _no_run(root)
    if state_exists != events_exists:
        raise ControllerError("inconsistent_persistence", "State and event log must either both exist or both be absent")
    state, events = validate_persistence(paths, config)
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
        "reason": None if state["reason"] is None else {
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
        "ci": {"status": state["ci"]["status"], "attempt": state["ci"]["attempt"], "commit_sha": state["ci"]["commit_sha"]},
        "counters": state["counters"],
        "checklist": {
            "satisfied": state["checklist"]["satisfied"],
            "partial": state["checklist"]["partial"],
            "unsatisfied": state["checklist"]["unsatisfied"],
            "not_evaluated": state["checklist"]["not_evaluated"],
            "assessed_at": state["checklist"]["assessed_at"],
        },
        "last_event": {"sequence": event["sequence"], "type": event["type"], "timestamp": event["timestamp"]},
        "updated_at": state["updated_at"],
    }


def validate_persistence(paths: RuntimePaths, config: SprintConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load and cross-validate state and events without projecting status."""
    state = load_state(paths.state)
    if state["multisprint"] != config.multisprint or state["sprint"] != config.sprint:
        raise ControllerError("inconsistent_persistence", "Persisted state does not match configured sprint identity")
    expected_keys = {repository.name for repository in config.repositories}
    if set(state["commits"]["local"]) != expected_keys or set(state["commits"]["pushed"]) != expected_keys:
        raise ControllerError("inconsistent_persistence", "Persisted commit maps do not match configured repository")
    events = load_events(paths.events)
    if not events:
        raise ControllerError("corrupt_event_log", "State exists but event log is empty")
    if events[-1]["sequence"] < state["last_event_sequence"]:
        raise ControllerError("corrupt_event_log", "Event log is behind persisted state")
    if events[-1]["sequence"] > state["last_event_sequence"]:
        raise ControllerError("inconsistent_persistence", "Event log is ahead of persisted state")
    validate_event_history(events)
    event = events[-1]
    if event["run_id"] != state["run_id"] or event["state"] != state["state"]:
        raise ControllerError("inconsistent_persistence", "State does not match its last event")
    if event["timestamp"] != state["updated_at"]:
        raise ControllerError("inconsistent_persistence", "State update timestamp does not match its last event")
    return state, events


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
        lines.append(f"Last event: {status['last_event']['type']} ({status['last_event']['timestamp']})")
    return "\n".join(lines) + "\n"
