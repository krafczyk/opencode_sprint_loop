"""Durable state construction, validation, and atomic persistence."""

from __future__ import annotations

import os
import re
import socket
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import SprintConfig
from .errors import ControllerError
from .jsonio import dump_json, load_json_object_handle
from .safeio import open_directory, open_regular

_SENSITIVE_FIELD = re.compile(r"(?:credential|password|secret|token|api[_-]?key|authorization)", re.IGNORECASE)

STATE_NAMES = frozenset({
    "initializing", "validating", "implementing", "committing", "pre_ci_auditing", "pushing",
    "waiting_for_ci", "fixing_ci", "final_auditing", "paused", "blocked", "stopping",
    "stopped", "failed", "finished",
})
TERMINAL_STATES = frozenset({"stopped", "failed", "finished"})
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now() -> str:
    """Return a timezone-aware RFC 3339 UTC timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, field: str) -> None:
    """Validate one timezone-aware RFC 3339 timestamp."""
    if not isinstance(value, str) or not RFC3339_UTC.fullmatch(value):
        raise ControllerError("corrupt_state", f"State {field} is not an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ControllerError("corrupt_state", f"State {field} is not an RFC 3339 UTC timestamp") from error
    if parsed.tzinfo != UTC:
        raise ControllerError("corrupt_state", f"State {field} is not an RFC 3339 UTC timestamp")


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
    repositories = {repository.name: None for repository in config.repositories}
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
        "commits": {"local": dict(repositories), "pushed": dict(repositories)},
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
    _validate_safe_reason_details(fields["details"])


def _validate_safe_reason_details(value: Any) -> None:
    """Reject credential-bearing field names from durable reason details."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or _SENSITIVE_FIELD.search(key):
                raise ControllerError("corrupt_state", "State reason details contain credential-bearing fields")
            _validate_safe_reason_details(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_safe_reason_details(nested)


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
    try:
        descriptor, directory = open_regular(path, os.O_RDONLY)
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                return validate_state(load_json_object_handle(handle, path, code="corrupt_state"))
        finally:
            os.close(directory)
    except FileNotFoundError as error:
        raise ControllerError("corrupt_state", f"State file is missing: {path}") from error
    except OSError as error:
        raise ControllerError("corrupt_state", f"Cannot read state file: {path}") from error


def serialize_state(state: dict[str, Any]) -> str:
    """Validate and serialize state before any durable transition write begins."""
    validate_state(state)
    try:
        return dump_json(state)
    except (TypeError, ValueError, RecursionError) as error:
        raise ControllerError("persistence_failed", "State cannot be serialized") from error


def write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    """Atomically replace state or raise ``ControllerError`` without truncating prior state."""
    serialized = serialize_state(state)
    temporary_name: str | None = None
    directory: int | None = None
    try:
        directory = open_directory(path.parent, create=True)
        try:
            existing = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise ControllerError("persistence_failed", f"State file must be an unlinked regular file: {path}")
        except FileNotFoundError:
            pass
        temporary_name = f".state-{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            payload = serialized.encode("utf-8")
            if os.write(descriptor, payload) != len(payload):
                raise OSError("Short write while persisting state")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary_name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except OSError as error:
        raise ControllerError("persistence_failed", f"Could not persist state: {path}") from error
    finally:
        if temporary_name is not None and directory is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
        if directory is not None:
            os.close(directory)
