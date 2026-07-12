"""Guarded Sprint 1 workflow transitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import ControllerError
from .events import append_event, transition_event
from .state import utc_now, write_state_atomic

_ALLOWED = {
    ("initializing", "validating"): "state.entered",
    ("validating", "blocked"): "run.blocked",
}


def persist_initial(state: dict[str, Any], events_path: Path, state_path: Path) -> dict[str, Any]:
    """Persist the no-run to initializing transition."""
    event = transition_event(state, "run.started", "initializing", {"previous_state": None})
    append_event(events_path, event)
    state["last_event_sequence"] = event["sequence"]
    state["updated_at"] = event["timestamp"]
    write_state_atomic(state_path, state)
    return state


def transition(state: dict[str, Any], events_path: Path, state_path: Path, destination: str, *, reason: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist one allowed event-first state transition."""
    source = state["state"]
    event_type = _ALLOWED.get((source, destination))
    if event_type is None:
        raise ControllerError("internal_error", f"Disallowed state transition: {source} -> {destination}")
    if destination == "blocked" and reason is None:
        raise ControllerError("internal_error", "Blocked transition requires a reason")
    payload: dict[str, Any] = {"previous_state": source}
    if reason is not None:
        payload["reason"] = reason
    event = transition_event(state, event_type, destination, payload)
    append_event(events_path, event)
    state["state"] = destination
    state["reason"] = reason
    state["last_event_sequence"] = event["sequence"]
    state["updated_at"] = event["timestamp"]
    if destination == "blocked":
        state["process"]["active"] = False
    write_state_atomic(state_path, state)
    return state
