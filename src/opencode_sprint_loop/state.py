"""Durable state construction, validation, and atomic persistence."""

from __future__ import annotations

import ctypes
import ipaddress
import os
import re
import socket
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import SprintConfig
from .errors import ControllerError
from .jsonio import dump_json, load_json_object_handle
from .safeio import open_directory, open_regular_at
from .security import validate_safe_data


STATE_NAMES = frozenset(
    {
        "initializing",
        "validating",
        "implementing",
        "committing",
        "pre_ci_auditing",
        "pushing",
        "waiting_for_ci",
        "fixing_ci",
        "final_auditing",
        "paused",
        "blocked",
        "stopping",
        "stopped",
        "failed",
        "finished",
    }
)
TERMINAL_STATES = frozenset({"stopped", "failed", "finished"})
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$")
SUPPORTED_SERVER_VERSION = re.compile(r"^1\.17\.\d+$")


def utc_now() -> str:
    """Return a timezone-aware RFC 3339 UTC timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, field: str) -> None:
    """Validate one timezone-aware RFC 3339 timestamp."""
    if not is_rfc3339_utc(value):
        raise ControllerError("corrupt_state", f"State {field} is not an RFC 3339 UTC timestamp")


def is_rfc3339_utc(value: Any) -> bool:
    """Return whether ``value`` is a complete RFC 3339 UTC timestamp."""
    if not isinstance(value, str) or not RFC3339_UTC.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(
            value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        return False
    return parsed.tzinfo == UTC


def _is_normalized_server_origin(value: Any) -> bool:
    """Return whether a persisted server URL is a normalized credential-free origin."""
    if not isinstance(value, str) or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return False
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.removesuffix(".").split(".")
        if not labels or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        ):
            return False
    else:
        host = address.compressed
    if port == 0:
        return False
    if ":" in host:
        host = f"[{host}]"
    default = 80 if parsed.scheme == "http" else 443
    normalized = f"{parsed.scheme}://{host}{'' if port is None or port == default else f':{port}'}"
    return value == normalized


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
        "checklist": {
            "satisfied": 0,
            "partial": 0,
            "unsatisfied": 0,
            "not_evaluated": 0,
            "assessed_at": None,
            "items": [],
        },
        "control": {"requested": None, "requested_at": None, "resume_state": None},
        "last_event_sequence": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
        "terminal_result": None,
    }


_REQUIRED = {
    "schema_version",
    "run_id",
    "multisprint",
    "sprint",
    "state",
    "reason",
    "process",
    "server",
    "active_invocation",
    "commits",
    "audit",
    "ci",
    "counters",
    "checklist",
    "control",
    "last_event_sequence",
    "created_at",
    "updated_at",
    "terminal_result",
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
    validate_safe_data(fields, code="corrupt_state", label="State reason")


def validate_state(data: dict[str, Any]) -> dict[str, Any]:
    """Validate persisted state before it influences controller decisions."""
    if not isinstance(data, dict) or "schema_version" not in data:
        raise ControllerError("corrupt_state", "State is missing schema_version")
    if (
        not isinstance(data["schema_version"], int)
        or isinstance(data["schema_version"], bool)
        or data["schema_version"] != 1
    ):
        raise ControllerError("unsupported_state_schema", "Unsupported state schema version")
    _exact_fields(data, _REQUIRED, "top-level")
    try:
        uuid.UUID(data["run_id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise ControllerError("corrupt_state", "State run_id is not a UUID") from error
    if not isinstance(data["state"], str) or data["state"] not in STATE_NAMES:
        raise ControllerError("corrupt_state", f"Unknown workflow state: {data['state']!r}")
    if data["state"] in {"blocked", "failed", "stopped"} and data["reason"] is None:
        raise ControllerError(
            "corrupt_state", "Blocked, failed, and stopped states require a reason"
        )
    if data["reason"] is not None:
        _validate_reason(data["reason"])
    _nonnegative_int(data["last_event_sequence"], "last_event_sequence")
    if (
        not isinstance(data["multisprint"], str)
        or not data["multisprint"]
        or not isinstance(data["sprint"], int)
        or isinstance(data["sprint"], bool)
        or data["sprint"] <= 0
    ):
        raise ControllerError("corrupt_state", "State sprint identity is invalid")
    _timestamp(data["created_at"], "created_at")
    _timestamp(data["updated_at"], "updated_at")
    process = _exact_fields(
        data["process"], {"pid", "process_start", "hostname", "active"}, "process"
    )
    if (
        not isinstance(process["active"], bool)
        or not isinstance(process["pid"], int)
        or isinstance(process["pid"], bool)
        or process["pid"] <= 0
        or not isinstance(process["hostname"], str)
        or not process["hostname"]
    ):
        raise ControllerError("corrupt_state", "State process fields are invalid")
    if process["process_start"] is not None and (
        not isinstance(process["process_start"], str) or not process["process_start"]
    ):
        raise ControllerError("corrupt_state", "State process_start is invalid")
    server = _exact_fields(data["server"], {"url", "version"}, "server")
    if (server["url"] is None) != (server["version"] is None):
        raise ControllerError("corrupt_state", "State server identity must be complete or null")
    if server["url"] is not None and (
        not _is_normalized_server_origin(server["url"])
        or not isinstance(server["version"], str)
        or not SUPPORTED_SERVER_VERSION.fullmatch(server["version"])
    ):
        raise ControllerError("corrupt_state", "State server identity is invalid")
    active = data["active_invocation"]
    if active is not None:
        active_fields = _exact_fields(
            active,
            {"invocation_id", "sequence", "role", "model", "session_id", "status", "started_at"},
            "active_invocation",
        )
        for field in ("invocation_id", "role", "model", "session_id"):
            value = active_fields[field]
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode()) > 1024
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ControllerError(
                    "corrupt_state", f"State active invocation {field} is invalid"
                )
        if (
            not isinstance(active_fields["sequence"], int)
            or isinstance(active_fields["sequence"], bool)
            or active_fields["sequence"] <= 0
            or active_fields["status"] != "running"
        ):
            raise ControllerError("corrupt_state", "State active invocation lifecycle is invalid")
        _timestamp(active_fields["started_at"], "active_invocation.started_at")

    commits = _exact_fields(data["commits"], {"local", "pushed"}, "commits")
    if not isinstance(commits["local"], dict) or not isinstance(commits["pushed"], dict):
        raise ControllerError("corrupt_state", "State commit maps are invalid")
    if any(
        not isinstance(key, str) or not key or value is not None
        for commit_map in commits.values()
        for key, value in commit_map.items()
    ):
        raise ControllerError("corrupt_state", "State commit values are invalid")

    audit = _exact_fields(
        data["audit"],
        {"phase", "pre_ci_round", "pre_ci_max_rounds", "latest_report", "remaining_effort"},
        "audit",
    )
    if (
        _nonnegative_int(audit["pre_ci_round"], "audit.pre_ci_round") < 0
        or not isinstance(audit["pre_ci_max_rounds"], int)
        or isinstance(audit["pre_ci_max_rounds"], bool)
        or audit["pre_ci_max_rounds"] <= 0
    ):
        raise ControllerError("corrupt_state", "State audit counters are invalid")
    if audit["pre_ci_round"] != 0 or any(
        audit[field] is not None for field in ("phase", "latest_report", "remaining_effort")
    ):
        raise ControllerError("corrupt_state", "Sprint 1 audit fields must be reserved")

    ci = _exact_fields(data["ci"], {"attempt", "commit_sha", "status", "checks"}, "ci")
    if _nonnegative_int(ci["attempt"], "ci.attempt") < 0 or not isinstance(ci["checks"], list):
        raise ControllerError("corrupt_state", "State CI fields are invalid")
    if (
        ci["attempt"] != 0
        or ci["commit_sha"] is not None
        or ci["status"] != "not_started"
        or ci["checks"] != []
    ):
        raise ControllerError("corrupt_state", "State CI metadata is invalid")

    counters = _exact_fields(
        data["counters"], {"implementation_cycles", "ci_fix_attempts"}, "counters"
    )
    _nonnegative_int(counters["implementation_cycles"], "counters.implementation_cycles")
    _nonnegative_int(counters["ci_fix_attempts"], "counters.ci_fix_attempts")
    if counters["implementation_cycles"] != 0 or counters["ci_fix_attempts"] != 0:
        raise ControllerError("corrupt_state", "Sprint 1 counters must be zero")

    checklist = _exact_fields(
        data["checklist"],
        {"satisfied", "partial", "unsatisfied", "not_evaluated", "assessed_at", "items"},
        "checklist",
    )
    if not isinstance(checklist["items"], list):
        raise ControllerError("corrupt_state", "State checklist items are invalid")
    for field in ("satisfied", "partial", "unsatisfied", "not_evaluated"):
        _nonnegative_int(checklist[field], f"checklist.{field}")
    if checklist["assessed_at"] is not None:
        _timestamp(checklist["assessed_at"], "checklist.assessed_at")
    if (
        any(
            checklist[field] != 0
            for field in ("satisfied", "partial", "unsatisfied", "not_evaluated")
        )
        or checklist["assessed_at"] is not None
        or checklist["items"] != []
    ):
        raise ControllerError("corrupt_state", "Sprint 1 checklist fields must be reserved")

    control = _exact_fields(
        data["control"], {"requested", "requested_at", "resume_state"}, "control"
    )
    if any(control[field] is not None for field in ("requested", "requested_at", "resume_state")):
        raise ControllerError("corrupt_state", "Sprint 1 control fields must be null")
    if data["terminal_result"] is not None:
        raise ControllerError("corrupt_state", "Sprint 1 terminal_result must be null")
    if data["state"] in TERMINAL_STATES | {"blocked"} and process["active"]:
        raise ControllerError(
            "corrupt_state", "Blocked and terminal Sprint 1 states must be inactive"
        )
    validate_safe_data(
        data,
        code="corrupt_state",
        label="State",
        dynamic_key_paths=frozenset({("commits", "local"), ("commits", "pushed")}),
    )
    return data


def load_state(path: Path) -> dict[str, Any]:
    """Load and validate a current state snapshot."""
    try:
        directory = open_directory(path.parent)
        try:
            return load_state_at(directory, path.name, path)
        finally:
            os.close(directory)
    except FileNotFoundError as error:
        raise ControllerError("corrupt_state", f"State file is missing: {path}") from error
    except OSError as error:
        raise ControllerError("corrupt_state", f"Cannot read state file: {path}") from error


def load_state_at(directory: int, name: str, path: Path) -> dict[str, Any]:
    """Load state through one directory descriptor or raise path-specific corruption."""
    try:
        descriptor = open_regular_at(directory, name, os.O_RDONLY)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            return validate_state(load_json_object_handle(handle, path, code="corrupt_state"))
    except OSError as error:
        raise ControllerError("corrupt_state", f"Cannot read state file: {path}") from error


def serialize_state(state: dict[str, Any]) -> str:
    """Validate and serialize state before any durable transition write begins."""
    validate_state(state)
    try:
        serialized = dump_json(state)
    except (TypeError, ValueError, RecursionError) as error:
        raise ControllerError("persistence_failed", "State cannot be serialized") from error
    if len(serialized.encode("utf-8")) > 1024 * 1024:
        raise ControllerError("persistence_failed", "State exceeds 1 MiB")
    return serialized


def write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    """Atomically replace state or raise ``ControllerError`` without truncating prior state."""
    serialized = serialize_state(state)
    directory: int | None = None
    try:
        directory = open_directory(path.parent, create=True)
        write_state_atomic_at(directory, path.name, path, serialized)
    except OSError as error:
        raise ControllerError("persistence_failed", f"Could not persist state: {path}") from error
    finally:
        if directory is not None:
            os.close(directory)


def _rename_exchange(directory: int, first: str, second: str) -> None:
    """Atomically exchange two names or fail without replacing either path."""
    function = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if function is None:
        raise ControllerError("persistence_failed", "Atomic state replacement is unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    if (
        function(
            directory,
            os.fsencode(first),
            directory,
            os.fsencode(second),
            0x2,  # RENAME_EXCHANGE
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def write_state_atomic_at(
    directory: int,
    name: str,
    path: Path,
    serialized: str,
    *,
    expected_identity: tuple[int, int] | None = None,
    require_absent: bool = False,
) -> tuple[int, int]:
    """Atomically replace validated state without overwriting a swapped path.

    The first state is installed by an exclusive hard link. Later replacements
    use Linux ``RENAME_EXCHANGE`` and restore an unexpected replacement before
    reporting the persistence failure.
    """
    temporary_name: str | None = None
    exchange_pending = False
    try:
        existing_identity: tuple[int, int] | None = None
        try:
            existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise ControllerError(
                    "persistence_failed", f"State file must be an unlinked regular file: {path}"
                )
            existing_identity = (existing.st_dev, existing.st_ino)
        except FileNotFoundError:
            pass
        if require_absent and existing_identity is not None:
            raise ControllerError(
                "persistence_failed", f"Initial state file already exists: {path}"
            )
        if expected_identity is not None and existing_identity != expected_identity:
            raise ControllerError(
                "persistence_failed", f"State file changed during transition: {path}"
            )
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
            installed = os.fstat(descriptor)
            installed_identity = (installed.st_dev, installed.st_ino)
        finally:
            os.close(descriptor)
        if existing_identity is None:
            try:
                os.link(temporary_name, name, src_dir_fd=directory, dst_dir_fd=directory)
            except FileExistsError as error:
                raise ControllerError(
                    "persistence_failed", f"State file changed during transition: {path}"
                ) from error
        else:
            _rename_exchange(directory, temporary_name, name)
            exchange_pending = True
            displaced = os.stat(temporary_name, dir_fd=directory, follow_symlinks=False)
            if (displaced.st_dev, displaced.st_ino) != existing_identity:
                _rename_exchange(directory, temporary_name, name)
                exchange_pending = False
                raise ControllerError(
                    "persistence_failed", f"State file changed during transition: {path}"
                )
            exchange_pending = False
        os.fsync(directory)
        os.unlink(temporary_name, dir_fd=directory)
        temporary_name = None
        os.fsync(directory)
        return installed_identity
    except OSError as error:
        raise ControllerError("persistence_failed", f"Could not persist state: {path}") from error
    finally:
        if exchange_pending and temporary_name is not None:
            try:
                _rename_exchange(directory, temporary_name, name)
            except (OSError, ControllerError):
                temporary_name = None
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
            else:
                try:
                    os.fsync(directory)
                except OSError:
                    pass


def remove_state_atomic_at(
    directory: int,
    name: str,
    path: Path,
    *,
    expected_identity: tuple[int, int],
) -> None:
    """Remove an exclusively created state without deleting a substituted path."""
    temporary_name = f".state-rollback-{uuid.uuid4().hex}.tmp"
    descriptor = -1
    exchanged = False
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _rename_exchange(directory, temporary_name, name)
        exchanged = True
        displaced = os.stat(temporary_name, dir_fd=directory, follow_symlinks=False)
        if (displaced.st_dev, displaced.st_ino) != expected_identity:
            _rename_exchange(directory, temporary_name, name)
            exchanged = False
            raise ControllerError(
                "persistence_failed", f"State file changed during transition rollback: {path}"
            )
        os.unlink(temporary_name, dir_fd=directory)
        exchanged = False
        temporary_name = ""
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)
    except OSError as error:
        raise ControllerError("persistence_failed", f"Could not roll back state: {path}") from error
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if exchanged:
            try:
                _rename_exchange(directory, temporary_name, name)
            except (OSError, ControllerError):
                temporary_name = ""
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
