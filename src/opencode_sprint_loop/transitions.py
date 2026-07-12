"""Guarded Sprint 1 workflow transitions."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .errors import ControllerError
from .events import append_event, load_events, transition_event
from .locking import advisory_lock
from .state import load_state, write_state_atomic

_ALLOWED = {
    ("initializing", "validating"): "state.entered",
    ("validating", "blocked"): "run.blocked",
    ("initializing", "failed"): "state.entered",
    ("validating", "failed"): "state.entered",
}


def _load_durable_pair(events_path: Path, state_path: Path) -> dict[str, Any]:
    """Return one validated state/event pair or fail closed on inconsistency."""
    state = load_state(state_path)
    events = load_events(events_path)
    if not events:
        raise ControllerError("corrupt_event_log", "State exists but event log is empty")
    event = events[-1]
    if event["sequence"] < state["last_event_sequence"]:
        raise ControllerError("corrupt_event_log", "Event log is behind persisted state")
    if event["sequence"] > state["last_event_sequence"]:
        raise ControllerError("inconsistent_persistence", "Event log is ahead of persisted state")
    if event["run_id"] != state["run_id"] or event["state"] != state["state"]:
        raise ControllerError("inconsistent_persistence", "State does not match its last event")
    return state


def persist_initial(
    state: dict[str, Any],
    events_path: Path,
    state_path: Path,
    persistence_lock: Path,
    *,
    lock_held: bool = False,
) -> dict[str, Any]:
    """Persist no-run to initializing while holding the first-transition lock."""
    def persist() -> dict[str, Any]:
        next_state = copy.deepcopy(state)
        event = transition_event(next_state, "run.started", "initializing", {"previous_state": None})
        append_event(events_path, event)
        next_state["last_event_sequence"] = event["sequence"]
        next_state["updated_at"] = event["timestamp"]
        write_state_atomic(state_path, next_state)
        return next_state

    if lock_held:
        return persist()
    with advisory_lock(persistence_lock, exclusive=True):
        return persist()


def transition(
    state: dict[str, Any],
    events_path: Path,
    state_path: Path,
    persistence_lock: Path,
    destination: str,
    *,
    reason: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one guarded event/state transition from validated durable data."""
    del state  # The durable snapshot, not a potentially stale caller copy, is authoritative.
    with advisory_lock(persistence_lock, exclusive=True):
        current = _load_durable_pair(events_path, state_path)
        source = current["state"]
        event_type = _ALLOWED.get((source, destination))
        if event_type is None:
            raise ControllerError("internal_error", f"Disallowed state transition: {source} -> {destination}")
        if destination in {"blocked", "failed"} and reason is None:
            raise ControllerError("internal_error", f"{destination.capitalize()} transition requires a reason")
        next_state = copy.deepcopy(current)
        payload: dict[str, Any] = {"previous_state": source}
        if reason is not None:
            payload["reason"] = reason
        event = transition_event(next_state, event_type, destination, payload)
        append_event(events_path, event)
        next_state["state"] = destination
        next_state["reason"] = reason
        next_state["last_event_sequence"] = event["sequence"]
        next_state["updated_at"] = event["timestamp"]
        if destination in {"blocked", "failed"}:
            next_state["process"]["active"] = False
        write_state_atomic(state_path, next_state)
        return next_state
