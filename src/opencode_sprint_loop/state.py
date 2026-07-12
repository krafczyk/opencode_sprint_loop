"""Durable state construction, validation, and atomic persistence."""

from __future__ import annotations

import os
import socket
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import SprintConfig
from .errors import ControllerError
from .jsonio import dump_json, load_json_object

STATE_NAMES = frozenset({
    "initializing", "validating", "implementing", "committing", "pre_ci_auditing", "pushing",
    "waiting_for_ci", "fixing_ci", "final_auditing", "paused", "blocked", "stopping",
    "stopped", "failed", "finished",
})
TERMINAL_STATES = frozenset({"stopped", "failed", "finished"})


def utc_now() -> str:
    """Return a timezone-aware RFC 3339 UTC timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, field: str) -> None:
    """Validate one timezone-aware RFC 3339 timestamp."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ControllerError("corrupt_state", f"State {field} is not an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ControllerError("corrupt_state", f"State {field} is not an RFC 3339 UTC timestamp") from error


def process_start_identity(pid: int) -> str | None:
    """Return an opaque Linux boot/process start identity when available."""
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return f"{boot_id}:{fields[21]}"
    except (FileNotFoundError, IndexError, OSError):
        return None


def new_state(config: SprintConfig) -> dict[str, Any]:
    """Build a complete initial state with all Sprint 1 status fields reserved."""
    timestamp = utc_now()
    repository = config.repository.name
    return {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "multisprint": config.multisprint,
        "sprint": config.sprint,
        "state": "initializing",
        "reason": None,
        "process": {
            "pid": os.getpid(),
            "process_start": process_start_identity(os.getpid()),
            "hostname": socket.gethostname(),
            "active": True,
        },
        "server": {"url": None, "version": None},
        "active_invocation": None,
        "commits": {"local": {repository: None}, "pushed": {repository: None}},
        "audit": {
            "phase": None,
            "pre_ci_round": 0,
            "pre_ci_max_rounds": config.pre_ci_max_rounds,
            "latest_report": None,
            "remaining_effort": None,
        },
        "ci": {"attempt": 0, "commit_sha": None, "status": "not_started", "checks": []},
        "counters": {"implementation_cycles": 0, "ci_fix_attempts": 0},
        "checklist": {"satisfied": 0, "partial": 0, "unsatisfied": 0, "not_evaluated": 0, "assessed_at": None, "items": []},
        "control": {"requested": None, "requested_at": None, "resume_state": None},
        "last_event_sequence": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
        "terminal_result": None,
    }


_REQUIRED = {
    "schema_version", "run_id", "multisprint", "sprint", "state", "reason", "process", "server",
    "active_invocation", "commits", "audit", "ci", "counters", "checklist", "control",
    "last_event_sequence", "created_at", "updated_at", "terminal_result",
}


def validate_state(data: dict[str, Any]) -> dict[str, Any]:
    """Validate persisted state before it influences controller decisions."""
    missing = _REQUIRED - set(data)
    if missing:
        raise ControllerError("corrupt_state", f"State is missing required field: {sorted(missing)[0]}")
    if not isinstance(data["schema_version"], int) or isinstance(data["schema_version"], bool) or data["schema_version"] != 1:
        raise ControllerError("unsupported_state_schema", "Unsupported state schema version")
    try:
        uuid.UUID(data["run_id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise ControllerError("corrupt_state", "State run_id is not a UUID") from error
    if not isinstance(data["state"], str) or data["state"] not in STATE_NAMES:
        raise ControllerError("corrupt_state", f"Unknown workflow state: {data['state']!r}")
    if data["state"] in {"blocked", "failed", "stopped"}:
        reason = data["reason"]
        if not isinstance(reason, dict) or not isinstance(reason.get("code"), str) or not isinstance(reason.get("message"), str):
            raise ControllerError("corrupt_state", "Blocked, failed, and stopped states require a structured reason")
    if data["reason"] is not None:
        reason = data["reason"]
        if not isinstance(reason, dict) or not isinstance(reason.get("code"), str) or not isinstance(reason.get("message"), str) or not isinstance(reason.get("details", {}), dict):
            raise ControllerError("corrupt_state", "State reason is invalid")
    if not isinstance(data["last_event_sequence"], int) or isinstance(data["last_event_sequence"], bool) or data["last_event_sequence"] < 0:
        raise ControllerError("corrupt_state", "State last_event_sequence must be a non-negative integer")
    if not isinstance(data["multisprint"], str) or not isinstance(data["sprint"], int) or isinstance(data["sprint"], bool):
        raise ControllerError("corrupt_state", "State sprint identity is invalid")
    _timestamp(data["created_at"], "created_at")
    _timestamp(data["updated_at"], "updated_at")
    if not isinstance(data["process"], dict) or not isinstance(data["process"].get("active"), bool):
        raise ControllerError("corrupt_state", "State process object is invalid")
    process_required = {"pid", "process_start", "hostname", "active"}
    if process_required - set(data["process"]) or not isinstance(data["process"]["pid"], int) or not isinstance(data["process"]["hostname"], str):
        raise ControllerError("corrupt_state", "State process fields are invalid")
    if data["process"]["process_start"] is not None and not isinstance(data["process"]["process_start"], str):
        raise ControllerError("corrupt_state", "State process_start is invalid")
    if not isinstance(data["server"], dict) or {"url", "version"} - set(data["server"]):
        raise ControllerError("corrupt_state", "State server object is invalid")
    if any(data["server"][field] is not None and not isinstance(data["server"][field], str) for field in ("url", "version")):
        raise ControllerError("corrupt_state", "State server fields are invalid")
    if data["active_invocation"] is not None and not isinstance(data["active_invocation"], dict):
        raise ControllerError("corrupt_state", "State active_invocation is invalid")
    for field in ("commits", "audit", "ci", "counters", "checklist", "control"):
        if not isinstance(data[field], dict):
            raise ControllerError("corrupt_state", f"State {field} object is invalid")
    required_nested = {
        "commits": {"local", "pushed"},
        "audit": {"phase", "pre_ci_round", "pre_ci_max_rounds", "latest_report", "remaining_effort"},
        "ci": {"attempt", "commit_sha", "status", "checks"},
        "counters": {"implementation_cycles", "ci_fix_attempts"},
        "checklist": {"satisfied", "partial", "unsatisfied", "not_evaluated", "assessed_at", "items"},
        "control": {"requested", "requested_at", "resume_state"},
    }
    for field, required in required_nested.items():
        if required - set(data[field]):
            raise ControllerError("corrupt_state", f"State {field} is missing required fields")
    if not isinstance(data["commits"]["local"], dict) or not isinstance(data["commits"]["pushed"], dict):
        raise ControllerError("corrupt_state", "State commit maps are invalid")
    if any(value is not None and not isinstance(value, str) for commit_map in data["commits"].values() for value in commit_map.values()):
        raise ControllerError("corrupt_state", "State commit values are invalid")
    if not all(isinstance(data["audit"][field], int) and not isinstance(data["audit"][field], bool) and data["audit"][field] >= 0 for field in ("pre_ci_round", "pre_ci_max_rounds")):
        raise ControllerError("corrupt_state", "State audit counters are invalid")
    if not isinstance(data["ci"]["attempt"], int) or isinstance(data["ci"]["attempt"], bool) or not isinstance(data["ci"]["checks"], list):
        raise ControllerError("corrupt_state", "State CI fields are invalid")
    if not isinstance(data["ci"]["status"], str) or data["ci"]["commit_sha"] is not None and not isinstance(data["ci"]["commit_sha"], str):
        raise ControllerError("corrupt_state", "State CI metadata is invalid")
    if not all(isinstance(data["counters"][field], int) and not isinstance(data["counters"][field], bool) and data["counters"][field] >= 0 for field in data["counters"]):
        raise ControllerError("corrupt_state", "State counters are invalid")
    if not isinstance(data["checklist"]["items"], list):
        raise ControllerError("corrupt_state", "State checklist items are invalid")
    if not all(isinstance(data["checklist"][field], int) and not isinstance(data["checklist"][field], bool) and data["checklist"][field] >= 0 for field in ("satisfied", "partial", "unsatisfied", "not_evaluated")):
        raise ControllerError("corrupt_state", "State checklist counters are invalid")
    if data["checklist"]["assessed_at"] is not None:
        _timestamp(data["checklist"]["assessed_at"], "checklist.assessed_at")
    if data["audit"]["phase"] is not None and not isinstance(data["audit"]["phase"], str) or data["audit"]["latest_report"] is not None and not isinstance(data["audit"]["latest_report"], str) or data["audit"]["remaining_effort"] is not None and not isinstance(data["audit"]["remaining_effort"], str):
        raise ControllerError("corrupt_state", "State audit metadata is invalid")
    if data["control"]["requested"] is not None and not isinstance(data["control"]["requested"], str):
        raise ControllerError("corrupt_state", "State control request is invalid")
    if data["control"]["requested_at"] is not None:
        _timestamp(data["control"]["requested_at"], "control.requested_at")
    if data["control"]["resume_state"] is not None and data["control"]["resume_state"] not in STATE_NAMES:
        raise ControllerError("corrupt_state", "State control resume_state is invalid")
    if data["terminal_result"] is not None and not isinstance(data["terminal_result"], dict):
        raise ControllerError("corrupt_state", "State terminal_result is invalid")
    return data


def load_state(path: Path) -> dict[str, Any]:
    """Load and validate a current state snapshot."""
    return validate_state(load_json_object(path, code="corrupt_state"))


def write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    """Atomically replace state after complete validation and durable flushing."""
    validate_state(state)
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(dump_json(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise ControllerError("persistence_failed", f"Could not persist state: {path}") from error
