"""Guarded durable workflow transitions and Sprint 2 observations."""

from __future__ import annotations

import copy
import os
import stat
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from .errors import ControllerError
from .events import append_event_at, load_events_at, transition_event, validate_event_history
from .locking import persistence_lock as persistence_advisory_lock
from .safeio import open_directory, require_current_directory
from .state import (
    load_state_at,
    remove_state_atomic_at,
    serialize_state,
    validate_state,
    write_state_atomic_at,
)

WorkflowState: TypeAlias = Literal["initializing", "validating", "blocked", "failed"]
EventType: TypeAlias = Literal["run.started", "state.entered", "run.blocked"]

_ALLOWED: dict[tuple[WorkflowState, WorkflowState], EventType] = {
    ("initializing", "validating"): "state.entered",
    ("validating", "blocked"): "run.blocked",
    ("initializing", "failed"): "state.entered",
    ("validating", "failed"): "state.entered",
}


def _artifact_identity(directory: int, name: str, path: Path) -> tuple[int, int]:
    """Return the regular single-link identity validated for a durable artifact."""
    details = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise ControllerError("persistence_failed", f"Runtime artifact is unsafe: {path}")
    return details.st_dev, details.st_ino


def _require_artifact_identity(
    directory: int, name: str, path: Path, expected: tuple[int, int]
) -> None:
    """Fail when a durable artifact path no longer names its validated inode."""
    if _artifact_identity(directory, name, path) != expected:
        raise ControllerError("persistence_failed", f"Runtime artifact changed: {path}")


def _load_durable_pair(
    directory: int, events_path: Path, state_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return one validated state/event pair and history or fail closed."""
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
    return state, events


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
            event_identity = append_event_at(
                directory, events_path.name, events_path, event, require_absent=True
            )
            installed_state = write_state_atomic_at(
                directory, state_path.name, state_path, serialized, require_absent=True
            )
            try:
                _require_artifact_identity(directory, events_path.name, events_path, event_identity)
            except ControllerError:
                remove_state_atomic_at(
                    directory,
                    state_path.name,
                    state_path,
                    expected_identity=installed_state,
                )
                raise
            require_current_directory(state_path.parent, directory)
            return next_state
        finally:
            os.close(directory)

    if lock_held:
        return persist()
    with persistence_advisory_lock(persistence_lock, exclusive=True) as lock:
        lock.ensure_current()
        return persist()


def transition(
    state: dict[str, Any],
    events_path: Path,
    state_path: Path,
    persistence_lock: Path,
    destination: WorkflowState,
    *,
    reason: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one guarded event/state transition from validated durable data."""
    del state  # The durable snapshot, not a potentially stale caller copy, is authoritative.
    with persistence_advisory_lock(persistence_lock, exclusive=True) as lock:
        lock.ensure_current()
        directory = open_directory(state_path.parent)
        try:
            current, events = _load_durable_pair(directory, events_path, state_path)
            events_identity = _artifact_identity(directory, events_path.name, events_path)
            state_identity = _artifact_identity(directory, state_path.name, state_path)
            source = cast(WorkflowState, current["state"])
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
            validate_event_history([*events, event])
            previous_serialized = serialize_state(current)
            lock.ensure_current()
            event_identity = append_event_at(
                directory,
                events_path.name,
                events_path,
                event,
                expected_identity=events_identity,
            )
            lock.ensure_current()
            installed_state = write_state_atomic_at(
                directory,
                state_path.name,
                state_path,
                serialized,
                expected_identity=state_identity,
            )
            try:
                _require_artifact_identity(directory, events_path.name, events_path, event_identity)
            except ControllerError:
                write_state_atomic_at(
                    directory,
                    state_path.name,
                    state_path,
                    previous_serialized,
                    expected_identity=installed_state,
                )
                raise
            lock.ensure_current()
            require_current_directory(state_path.parent, directory)
            return next_state
        finally:
            os.close(directory)


def observe(
    state: dict[str, Any],
    events_path: Path,
    state_path: Path,
    persistence_lock: Path,
    event_type: str,
    payload: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    """Persist one validated ``validating -> validating`` observation.

    The caller supplies only small safe state fields (server or active invocation)
    and all external I/O has already completed before this short critical section.
    """
    del state
    allowed = {"server.validated", "agent.started", "agent.completed", "agent.interrupted"}
    if event_type not in allowed:
        raise ControllerError("internal_error", "Invalid same-state observation")
    with persistence_advisory_lock(persistence_lock, exclusive=True) as lock:
        lock.ensure_current()
        directory = open_directory(state_path.parent)
        try:
            current, events = _load_durable_pair(directory, events_path, state_path)
            if current["state"] != "validating":
                raise ControllerError(
                    "inconsistent_persistence", "Sprint 2 observation requires validating state"
                )
            events_identity = _artifact_identity(directory, events_path.name, events_path)
            state_identity = _artifact_identity(directory, state_path.name, state_path)
            next_state = copy.deepcopy(current)
            if event_type == "server.validated":
                if (
                    set(update) != {"server"}
                    or current["server"]
                    != {
                        "url": None,
                        "version": None,
                    }
                    or not isinstance(update["server"], dict)
                    or update["server"].get("version") != payload.get("server_version")
                ):
                    raise ControllerError("internal_error", "Invalid server validation update")
            elif event_type == "agent.started":
                if set(update) != {"active_invocation"} or current["active_invocation"] is not None:
                    raise ControllerError("internal_error", "Invalid agent start update")
                active = update["active_invocation"]
                if not isinstance(active, dict) or any(
                    active.get(field) != payload.get(field)
                    for field in ("invocation_id", "role", "session_id")
                ):
                    raise ControllerError("internal_error", "Agent start identity is inconsistent")
            elif (
                set(update) != {"active_invocation"}
                or update["active_invocation"] is not None
                or current["active_invocation"] is None
            ):
                raise ControllerError("internal_error", "Invalid agent terminal update")
            elif any(
                current["active_invocation"].get(field) != payload.get(field)
                for field in ("invocation_id", "role", "session_id")
            ):
                raise ControllerError("internal_error", "Agent terminal identity is inconsistent")
            for key, value in update.items():
                next_state[key] = value
            event = transition_event(next_state, event_type, "validating", payload)
            next_state["last_event_sequence"] = event["sequence"]
            next_state["updated_at"] = event["timestamp"]
            validate_event_history([*events, event])
            serialized = serialize_state(next_state)
            event_identity = append_event_at(
                directory, events_path.name, events_path, event, expected_identity=events_identity
            )
            installed_state = write_state_atomic_at(
                directory, state_path.name, state_path, serialized, expected_identity=state_identity
            )
            try:
                _require_artifact_identity(directory, events_path.name, events_path, event_identity)
            except ControllerError:
                write_state_atomic_at(
                    directory,
                    state_path.name,
                    state_path,
                    serialize_state(current),
                    expected_identity=installed_state,
                )
                raise
            lock.ensure_current()
            require_current_directory(state_path.parent, directory)
            return next_state
        finally:
            os.close(directory)
