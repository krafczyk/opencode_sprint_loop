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
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        _, separator, fields_after_name = stat.rpartition(")")
        if not separator:
            return None
        # Field 22 is process start time; fields after the name begin at field 3.
        return f"{boot_id}:{fields_after_name.split()[19]}"
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


def _exact_fields(data: Any, fields: set[str], label: str) -> dict[str, Any]:
    """Require stable schema-version-one fields while allowing future additions."""
    if not isinstance(data, dict) or fields - set(data):
        raise ControllerError("corrupt_state", f"State {label} fields are invalid")
    return data


def _nonnegative_int(value: Any, field: str) -> int:
    """Validate a non-negative persisted counter without accepting booleans."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ControllerError("corrupt_state", f"State {field} must be a non-negative integer")
    return value


def _nullable_string(value: Any, field: str) -> None:
    """Validate a nullable string field in the durable schema."""
    if value is not None and not isinstance(value, str):
        raise ControllerError("corrupt_state", f"State {field} is invalid")


def _validate_reason(reason: Any) -> None:
    """Validate the structured, non-secret reason persisted for a transition."""
    fields = _exact_fields(reason, {"code", "message", "details"}, "reason")
    if not isinstance(fields["code"], str) or not fields["code"]:
        raise ControllerError("corrupt_state", "State reason code is invalid")
    if not isinstance(fields["message"], str) or not fields["message"]:
        raise ControllerError("corrupt_state", "State reason message is invalid")
    if not isinstance(fields["details"], dict):
        raise ControllerError("corrupt_state", "State reason details are invalid")


def validate_state(data: dict[str, Any]) -> dict[str, Any]:
    """Validate persisted state before it influences controller decisions."""
    if not isinstance(data, dict) or "schema_version" not in data:
        raise ControllerError("corrupt_state", "State is missing schema_version")
    if not isinstance(data["schema_version"], int) or isinstance(data["schema_version"], bool) or data["schema_version"] != 1:
        raise ControllerError("unsupported_state_schema", "Unsupported state schema version")
    _exact_fields(data, _REQUIRED, "top-level")
    try:
        uuid.UUID(data["run_id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise ControllerError("corrupt_state", "State run_id is not a UUID") from error
    if not isinstance(data["state"], str) or data["state"] not in STATE_NAMES:
        raise ControllerError("corrupt_state", f"Unknown workflow state: {data['state']!r}")
    if data["state"] in {"blocked", "failed", "stopped"} and data["reason"] is None:
        raise ControllerError("corrupt_state", "Blocked, failed, and stopped states require a reason")
    if data["reason"] is not None:
        _validate_reason(data["reason"])
    _nonnegative_int(data["last_event_sequence"], "last_event_sequence")
    if not isinstance(data["multisprint"], str) or not data["multisprint"] or not isinstance(data["sprint"], int) or isinstance(data["sprint"], bool) or data["sprint"] <= 0:
        raise ControllerError("corrupt_state", "State sprint identity is invalid")
    _timestamp(data["created_at"], "created_at")
    _timestamp(data["updated_at"], "updated_at")
    process = _exact_fields(data["process"], {"pid", "process_start", "hostname", "active"}, "process")
    if not isinstance(process["active"], bool) or not isinstance(process["pid"], int) or isinstance(process["pid"], bool) or process["pid"] <= 0 or not isinstance(process["hostname"], str) or not process["hostname"]:
        raise ControllerError("corrupt_state", "State process fields are invalid")
    if process["process_start"] is not None and (not isinstance(process["process_start"], str) or not process["process_start"]):
        raise ControllerError("corrupt_state", "State process_start is invalid")
    server = _exact_fields(data["server"], {"url", "version"}, "server")
    _nullable_string(server["url"], "server.url")
    _nullable_string(server["version"], "server.version")
    if data["active_invocation"] is not None and not isinstance(data["active_invocation"], dict):
        raise ControllerError("corrupt_state", "Sprint 1 active_invocation must be null")

    commits = _exact_fields(data["commits"], {"local", "pushed"}, "commits")
    if not isinstance(commits["local"], dict) or not isinstance(commits["pushed"], dict):
        raise ControllerError("corrupt_state", "State commit maps are invalid")
    if any(not isinstance(key, str) or not key or value is not None and not isinstance(value, str) for commit_map in commits.values() for key, value in commit_map.items()):
        raise ControllerError("corrupt_state", "State commit values are invalid")

    audit = _exact_fields(data["audit"], {"phase", "pre_ci_round", "pre_ci_max_rounds", "latest_report", "remaining_effort"}, "audit")
    if _nonnegative_int(audit["pre_ci_round"], "audit.pre_ci_round") < 0 or not isinstance(audit["pre_ci_max_rounds"], int) or isinstance(audit["pre_ci_max_rounds"], bool) or audit["pre_ci_max_rounds"] <= 0:
        raise ControllerError("corrupt_state", "State audit counters are invalid")
    _nullable_string(audit["phase"], "audit.phase")
    _nullable_string(audit["latest_report"], "audit.latest_report")
    _nullable_string(audit["remaining_effort"], "audit.remaining_effort")

    ci = _exact_fields(data["ci"], {"attempt", "commit_sha", "status", "checks"}, "ci")
    if _nonnegative_int(ci["attempt"], "ci.attempt") < 0 or not isinstance(ci["checks"], list):
        raise ControllerError("corrupt_state", "State CI fields are invalid")
    if not isinstance(ci["status"], str) or ci["commit_sha"] is not None and not isinstance(ci["commit_sha"], str):
        raise ControllerError("corrupt_state", "State CI metadata is invalid")

    counters = _exact_fields(data["counters"], {"implementation_cycles", "ci_fix_attempts"}, "counters")
    _nonnegative_int(counters["implementation_cycles"], "counters.implementation_cycles")
    _nonnegative_int(counters["ci_fix_attempts"], "counters.ci_fix_attempts")

    checklist = _exact_fields(data["checklist"], {"satisfied", "partial", "unsatisfied", "not_evaluated", "assessed_at", "items"}, "checklist")
    if not isinstance(checklist["items"], list):
        raise ControllerError("corrupt_state", "State checklist items are invalid")
    for field in ("satisfied", "partial", "unsatisfied", "not_evaluated"):
        _nonnegative_int(checklist[field], f"checklist.{field}")
    if checklist["assessed_at"] is not None:
        _timestamp(checklist["assessed_at"], "checklist.assessed_at")

    control = _exact_fields(data["control"], {"requested", "requested_at", "resume_state"}, "control")
    _nullable_string(control["requested"], "control.requested")
    if control["requested_at"] is not None:
        _timestamp(control["requested_at"], "control.requested_at")
    if control["resume_state"] is not None and control["resume_state"] not in STATE_NAMES:
        raise ControllerError("corrupt_state", "State control resume_state is invalid")
    if data["state"] not in TERMINAL_STATES and data["terminal_result"] is not None:
        raise ControllerError("corrupt_state", "State terminal_result must be null for non-terminal states")
    if data["terminal_result"] is not None and not isinstance(data["terminal_result"], dict):
        raise ControllerError("corrupt_state", "State terminal_result is invalid")
    return data


def load_state(path: Path) -> dict[str, Any]:
    """Load and validate a current state snapshot."""
    return validate_state(load_json_object(path, code="corrupt_state"))


def write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    """Atomically replace state or raise ``ControllerError`` without truncating prior state."""
    validate_state(state)
    try:
        serialized = dump_json(state)
    except (TypeError, ValueError, RecursionError) as error:
        raise ControllerError("persistence_failed", "State cannot be serialized") from error
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(serialized)
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
