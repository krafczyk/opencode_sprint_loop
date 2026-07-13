"""Append-only event log validation and durable append operations."""

from __future__ import annotations

import json
import os
import stat
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ControllerError
from .jsonio import MAX_JSON_BYTES
from .safeio import open_directory, open_regular_at
from .security import validate_safe_data
from .state import RFC3339_UTC, STATE_NAMES, utc_now

EVENT_TYPES = frozenset(
    {
        "run.started",
        "state.entered",
        "server.validated",
        "agent.started",
        "agent.completed",
        "agent.interrupted",
        "git.committed",
        "git.pushed",
        "ci.discovered",
        "ci.completed",
        "audit.completed",
        "run.paused",
        "run.blocked",
        "run.stopped",
        "run.finished",
    }
)

_SPRINT_ONE_TRANSITIONS = {
    ("initializing", "validating"): "state.entered",
    ("validating", "blocked"): "run.blocked",
    ("initializing", "failed"): "state.entered",
    ("validating", "failed"): "state.entered",
}

_EVENT_FIELDS = {"schema_version", "sequence", "timestamp", "run_id", "type", "state", "payload"}


def _validate_safe_payload(value: Any, *, code: str) -> None:
    """Reject credential-bearing fields and values from durable event payloads."""
    validate_safe_data(value, code=code, label="Event payload")


def validate_event(event: dict[str, Any], *, code: str = "corrupt_event_log") -> dict[str, Any]:
    """Validate one complete event envelope before durable use or append."""
    if set(event) != _EVENT_FIELDS:
        raise ControllerError(code, "Event fields do not match the schema")
    if (
        not isinstance(event["schema_version"], int)
        or isinstance(event["schema_version"], bool)
        or event["schema_version"] != 1
    ):
        raise ControllerError(code, "Event schema version is unsupported")
    if (
        not isinstance(event["sequence"], int)
        or isinstance(event["sequence"], bool)
        or event["sequence"] <= 0
    ):
        raise ControllerError(code, "Event sequence must be a positive integer")
    if not isinstance(event["run_id"], str):
        raise ControllerError(code, "Event run_id is invalid")
    try:
        uuid.UUID(event["run_id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise ControllerError(code, "Event run_id is invalid") from error
    if not isinstance(event["timestamp"], str) or not RFC3339_UTC.fullmatch(event["timestamp"]):
        raise ControllerError(code, "Event timestamp is invalid")
    try:
        timestamp = event["timestamp"]
        parsed = datetime.fromisoformat(
            timestamp.removesuffix("Z") + "+00:00" if timestamp.endswith("Z") else timestamp
        )
    except ValueError as error:
        raise ControllerError(code, "Event timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise ControllerError(code, "Event timestamp is invalid")
    if not isinstance(event["type"], str) or event["type"] not in EVENT_TYPES:
        raise ControllerError(code, "Event type is invalid")
    if not isinstance(event["state"], str) or event["state"] not in STATE_NAMES:
        raise ControllerError(code, "Event state is invalid")
    if not isinstance(event["payload"], dict):
        raise ControllerError(code, "Event payload must be an object")
    _validate_safe_payload(event["payload"], code=code)
    return event


def load_events(path: Path) -> list[dict[str, Any]]:
    """Load and validate an append-only JSONL event log."""
    try:
        directory = open_directory(path.parent)
        try:
            return load_events_at(directory, path.name, path)
        finally:
            os.close(directory)
    except FileNotFoundError:
        return []
    except OSError as error:
        raise ControllerError("corrupt_event_log", f"Cannot read event log: {path}") from error


def load_events_at(directory: int, name: str, path: Path) -> list[dict[str, Any]]:
    """Load events through one already-open runtime directory descriptor."""
    events: list[dict[str, Any]] = []
    descriptor = open_regular_at(directory, name, os.O_RDONLY)
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        number = 0
        while True:
            raw = handle.readline(MAX_JSON_BYTES + 1)
            if not raw:
                break
            number += 1
            if len(raw) > MAX_JSON_BYTES:
                raise ControllerError("corrupt_event_log", f"Event line {number} exceeds 1 MiB")
            if not raw.endswith(b"\n"):
                raise ControllerError("corrupt_event_log", f"Event line {number} is partial")

            def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ControllerError(
                            "corrupt_event_log",
                            f"Duplicate JSON key {key!r} in event line {number}",
                        )
                    result[key] = value
                return result

            try:
                event = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=reject_duplicates,
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
                raise ControllerError(
                    "corrupt_event_log", f"Malformed event line {number}"
                ) from error
            if not isinstance(event, dict):
                raise ControllerError("corrupt_event_log", f"Event line {number} is not an object")
            events.append(event)
    expected = 1
    run_id: str | None = None
    for event in events:
        validate_event(event)
        if event["sequence"] != expected:
            raise ControllerError("corrupt_event_log", "Event log has non-monotonic sequence")
        if run_id is not None and event["run_id"] != run_id:
            raise ControllerError("corrupt_event_log", "Event log has inconsistent run IDs")
        run_id = event["run_id"]
        expected += 1
    return events


def validate_event_history(events: list[dict[str, Any]]) -> None:
    """Require persisted Sprint 1 events to describe a reachable history."""
    if not events:
        raise ControllerError("corrupt_event_log", "State exists but event log is empty")
    first = events[0]
    if first["type"] != "run.started" or first["state"] != "initializing":
        raise ControllerError("corrupt_event_log", "First event must start an initializing run")
    if first["payload"] != {"previous_state": None}:
        raise ControllerError("corrupt_event_log", "Initial event payload is invalid")
    previous = "initializing"
    for event in events[1:]:
        destination = event["state"]
        expected_type = _SPRINT_ONE_TRANSITIONS.get((previous, destination))
        if event["type"] != expected_type:
            raise ControllerError(
                "corrupt_event_log", "Event does not describe an allowed Sprint 1 transition"
            )
        payload = event["payload"]
        if payload.get("previous_state") != previous:
            raise ControllerError("corrupt_event_log", "Event prior state is inconsistent")
        if destination in {"blocked", "failed"}:
            reason = payload.get("reason")
            if not isinstance(reason, dict) or not reason.get("code") or not reason.get("message"):
                raise ControllerError(
                    "corrupt_event_log", "Blocked or failed event requires a reason"
                )
        previous = destination


def append_event(path: Path, event: dict[str, Any]) -> None:
    """Durably append one event or raise ``ControllerError`` without rewriting history."""
    validate_event(event, code="persistence_failed")
    directory: int | None = None
    try:
        directory = open_directory(path.parent, create=True)
        append_event_at(directory, path.name, path, event)
    except OSError as error:
        raise ControllerError("persistence_failed", f"Could not append event: {path}") from error
    finally:
        if directory is not None:
            os.close(directory)


def append_event_at(
    directory: int,
    name: str,
    path: Path,
    event: dict[str, Any],
    *,
    expected_identity: tuple[int, int] | None = None,
    require_absent: bool = False,
) -> tuple[int, int]:
    """Durably append one event through an already-open runtime directory.

    ``expected_identity`` binds an append to the previously validated file;
    ``require_absent`` atomically creates the first event log without accepting
    a file installed by another process.
    """
    validate_event(event, code="persistence_failed")
    try:
        serialized = (
            json.dumps(event, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ControllerError("persistence_failed", "Event cannot be serialized") from error
    if len(serialized) > MAX_JSON_BYTES:
        raise ControllerError("persistence_failed", "Event exceeds 1 MiB")
    try:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if require_absent:
            flags |= os.O_EXCL
        descriptor = open_regular_at(directory, name, flags)
        try:
            details = os.fstat(descriptor)
            identity = (details.st_dev, details.st_ino)
            if require_absent and expected_identity is not None:
                raise ControllerError("persistence_failed", "Initial event log identity is invalid")
            if expected_identity is not None and identity != expected_identity:
                raise ControllerError(
                    "persistence_failed", f"Event log changed during append: {path}"
                )
            written = os.write(descriptor, serialized)
            if written != len(serialized):
                raise ControllerError("persistence_failed", "Short write while appending event")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != identity
        ):
            raise ControllerError("persistence_failed", f"Event log changed during append: {path}")
        return identity
    except OSError as error:
        raise ControllerError("persistence_failed", f"Could not append event: {path}") from error


def transition_event(
    state: dict[str, Any], event_type: str, destination: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Build the next event associated with one state transition."""
    return {
        "schema_version": 1,
        "sequence": state["last_event_sequence"] + 1,
        "timestamp": utc_now(),
        "run_id": state["run_id"],
        "type": event_type,
        "state": destination,
        "payload": payload,
    }
