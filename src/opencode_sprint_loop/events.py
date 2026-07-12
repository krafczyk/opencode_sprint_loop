"""Append-only event log validation and durable append operations."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ControllerError
from .jsonio import MAX_JSON_BYTES
from .state import RFC3339_UTC, STATE_NAMES, utc_now

EVENT_TYPES = frozenset({
    "run.started", "state.entered", "server.validated", "agent.started", "agent.completed",
    "agent.interrupted", "git.committed", "git.pushed", "ci.discovered", "ci.completed",
    "audit.completed", "run.paused", "run.blocked", "run.stopped", "run.finished",
})

_EVENT_FIELDS = {"schema_version", "sequence", "timestamp", "run_id", "type", "state", "payload"}


def validate_event(event: dict[str, Any], *, code: str = "corrupt_event_log") -> dict[str, Any]:
    """Validate one complete event envelope before durable use or append."""
    if set(event) != _EVENT_FIELDS:
        raise ControllerError(code, "Event fields do not match the schema")
    if not isinstance(event["schema_version"], int) or isinstance(event["schema_version"], bool) or event["schema_version"] != 1:
        raise ControllerError(code, "Event schema version is unsupported")
    if not isinstance(event["sequence"], int) or isinstance(event["sequence"], bool) or event["sequence"] <= 0:
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
        parsed = datetime.fromisoformat(event["timestamp"].removesuffix("Z") + "+00:00")
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
    return event


def load_events(path: Path) -> list[dict[str, Any]]:
    """Load and validate an append-only JSONL event log."""
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
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
                            raise ControllerError("corrupt_event_log", f"Duplicate JSON key {key!r} in event line {number}")
                        result[key] = value
                    return result
                try:
                    event = json.loads(
                        raw.decode("utf-8"),
                        object_pairs_hook=reject_duplicates,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
                    raise ControllerError("corrupt_event_log", f"Malformed event line {number}") from error
                if not isinstance(event, dict):
                    raise ControllerError("corrupt_event_log", f"Event line {number} is not an object")
                events.append(event)
    except OSError as error:
        raise ControllerError("corrupt_event_log", f"Cannot read event log: {path}") from error
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


def append_event(path: Path, event: dict[str, Any]) -> None:
    """Durably append one event or raise ``ControllerError`` without rewriting history."""
    validate_event(event, code="persistence_failed")
    try:
        serialized = json.dumps(event, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
    except (TypeError, ValueError, RecursionError) as error:
        raise ControllerError("persistence_failed", "Event cannot be serialized") from error
    if len(serialized) > MAX_JSON_BYTES:
        raise ControllerError("persistence_failed", "Event exceeds 1 MiB")
    try:
        if os.path.lexists(path) and path.is_symlink():
            raise ControllerError("persistence_failed", f"Event log must not be a symlink: {path}")
        created = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(
                path.name,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                written = os.write(descriptor, serialized)
                if written != len(serialized):
                    raise ControllerError("persistence_failed", "Short write while appending event")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory)
        if created:
            directory_descriptor = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    except OSError as error:
        raise ControllerError("persistence_failed", f"Could not append event: {path}") from error


def transition_event(state: dict[str, Any], event_type: str, destination: str, payload: dict[str, Any]) -> dict[str, Any]:
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
