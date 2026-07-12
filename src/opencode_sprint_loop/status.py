"""Human and JSON status projections from durable controller state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import SprintConfig
from .errors import ControllerError
from .events import load_events
from .locking import is_exclusively_locked
from .paths import RuntimePaths
from .state import load_state


def _no_run(root: Path) -> dict[str, Any]:
    """Return the stable JSON projection when no persisted run exists."""
    return {
        "schema_version": 1,
        "controller_version": "0.1.0",
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
        "controller_version": "0.1.0",
        "sprint_root": str(root),
        "run_exists": True,
        "process_running": is_exclusively_locked(run_lock),
        "run_id": state["run_id"],
        "sprint": {"multisprint": state["multisprint"], "index": state["sprint"]},
        "state": state["state"],
        "reason": state["reason"],
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
        "checklist": state["checklist"],
        "last_event": {"sequence": event["sequence"], "type": event["type"], "timestamp": event["timestamp"]},
        "updated_at": state["updated_at"],
    }


def validate_persistence(paths: RuntimePaths, config: SprintConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load and cross-validate state and events without projecting status."""
    state = load_state(paths.state)
    if state["multisprint"] != config.multisprint or state["sprint"] != config.sprint:
        raise ControllerError("inconsistent_persistence", "Persisted state does not match configured sprint identity")
    expected_keys = {config.repository.name}
    if set(state["commits"]["local"]) != expected_keys or set(state["commits"]["pushed"]) != expected_keys:
        raise ControllerError("inconsistent_persistence", "Persisted commit maps do not match configured repository")
    events = load_events(paths.events)
    if not events:
        raise ControllerError("corrupt_event_log", "State exists but event log is empty")
    if events[-1]["sequence"] < state["last_event_sequence"]:
        raise ControllerError("corrupt_event_log", "Event log is behind persisted state")
    if events[-1]["sequence"] > state["last_event_sequence"]:
        raise ControllerError("inconsistent_persistence", "Event log is ahead of persisted state")
    event = events[-1]
    if event["run_id"] != state["run_id"] or event["state"] != state["state"]:
        raise ControllerError("inconsistent_persistence", "State does not match its last event")
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
