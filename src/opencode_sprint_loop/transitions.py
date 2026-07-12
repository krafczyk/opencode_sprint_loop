"""Guarded Sprint 1 workflow transitions."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from .errors import ControllerError
from .events import append_event_at, load_events_at, transition_event, validate_event_history
from .locking import advisory_lock
from .safeio import open_directory, require_current_directory
from .state import load_state_at, serialize_state, validate_state, write_state_atomic_at

_ALLOWED = {
    ("initializing", "validating"): "state.entered",
    ("validating", "blocked"): "run.blocked",
    ("initializing", "failed"): "state.entered",
    ("validating", "failed"): "state.entered",
}


def _load_durable_pair(directory: int, events_path: Path, state_path: Path) -> dict[str, Any]:
    """Return one validated state/event pair or fail closed on inconsistency."""
    state = load_state_at(directory, state_path.name, state_path)
    events = load_events_at(directory, events_path.name, events_path)
    if not events:
        raise ControllerError("corrupt_event_log", "State exists but event log is empty")
    event = events[-1]
    if event["sequence"] < state["last_event_sequence"]:
        raise ControllerError("corrupt_event_log", "Event log is behind persisted state")
    if event["sequence"] > state["last_event_sequence"]:
        raise ControllerError("inconsistent_persistence", "Event log is ahead of persisted state")
    validate_event_history(events)
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
        directory = open_directory(state_path.parent, create=True)
        try:
            try:
                os.stat(events_path.name, dir_fd=directory, follow_symlinks=False)
                events_exist = True
            except FileNotFoundError:
                events_exist = False
            try:
                os.stat(state_path.name, dir_fd=directory, follow_symlinks=False)
                state_exists = True
            except FileNotFoundError:
                state_exists = False
            if events_exist or state_exists:
                raise ControllerError(
                    "inconsistent_persistence",
                    "Initial transition requires no existing state or event log",
                )
            validate_state(state)
            if (
                state["state"] != "initializing"
                or state["last_event_sequence"] != 0
                or state["reason"] is not None
            ):
                raise ControllerError(
                    "internal_error", "Initial transition requires a new initializing state"
                )
            next_state = copy.deepcopy(state)
            event = transition_event(
                next_state, "run.started", "initializing", {"previous_state": None}
            )
            next_state["last_event_sequence"] = event["sequence"]
            next_state["updated_at"] = event["timestamp"]
            serialized = serialize_state(next_state)
            append_event_at(directory, events_path.name, events_path, event)
            write_state_atomic_at(directory, state_path.name, state_path, serialized)
            require_current_directory(state_path.parent, directory)
            return next_state
        finally:
            os.close(directory)

    if lock_held:
        return persist()
    with advisory_lock(persistence_lock, exclusive=True) as lock:
        lock.ensure_current()
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
    with advisory_lock(persistence_lock, exclusive=True) as lock:
        lock.ensure_current()
        directory = open_directory(state_path.parent)
        try:
            current = _load_durable_pair(directory, events_path, state_path)
            source = current["state"]
            event_type = _ALLOWED.get((source, destination))
            if event_type is None:
                raise ControllerError(
                    "internal_error", f"Disallowed state transition: {source} -> {destination}"
                )
            if destination in {"blocked", "failed"} and reason is None:
                raise ControllerError(
                    "internal_error", f"{destination.capitalize()} transition requires a reason"
                )
            next_state = copy.deepcopy(current)
            payload: dict[str, Any] = {"previous_state": source}
            if reason is not None:
                payload["reason"] = reason
            event = transition_event(next_state, event_type, destination, payload)
            next_state["state"] = destination
            next_state["reason"] = reason
            next_state["last_event_sequence"] = event["sequence"]
            next_state["updated_at"] = event["timestamp"]
            if destination in {"blocked", "failed"}:
                next_state["process"]["active"] = False
            serialized = serialize_state(next_state)
            append_event_at(directory, events_path.name, events_path, event)
            write_state_atomic_at(directory, state_path.name, state_path, serialized)
            require_current_directory(state_path.parent, directory)
            return next_state
        finally:
            os.close(directory)
