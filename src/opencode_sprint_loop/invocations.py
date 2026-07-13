"""Durable, bounded Sprint 2 execution-probe invocation artifacts."""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ControllerError
from .safeio import open_directory
from .security import contains_credential, redact_external_data, validate_safe_data
from .state import RFC3339_UTC, utc_now

MAX_PROMPT_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_STRING_BYTES = 1024 * 1024
_TERMINAL = {"completed", "blocked", "failed", "timed_out", "interrupted"}


def _bounded_string(value: Any, field: str, limit: int = 1024) -> str:
    """Validate one bounded, control-character-free invocation identifier."""
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > limit
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ControllerError("invocation_record_failed", f"Invocation {field} is invalid")
    return value


def probe_title(multisprint: str, sprint: int, sequence: int) -> str:
    """Return the deterministic recognizable title for an Auditor probe."""
    return f"[{multisprint}/{sprint}] auditor {sequence:04d} execution probe"


def probe_prompt(multisprint: str, sprint: int, invocation_id: str) -> str:
    """Build the exact non-mutating, credential-free Sprint 2 probe prompt."""
    prompt = (
        f"Sprint {multisprint}/{sprint}; invocation {invocation_id}.\n\n"
        "This is an OpenCode execution-layer probe, not a sprint audit. Do not use repository, "
        "shell, web, task, or external-mutation tools. Do not modify any repository or external "
        "service. The controller enforces a wildcard-deny permission override; only OpenCode's "
        "built-in StructuredOutput mechanism is permitted. Return exactly the requested JSON schema "
        "result with an empty checks array.\n"
    )
    validate_prompt(prompt)
    return prompt


def validate_prompt(prompt: str) -> None:
    """Reject oversized or credential-bearing controller-authored prompt text."""
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES or contains_credential(prompt):
        raise ControllerError(
            "invocation_record_failed", "Execution probe prompt is unsafe or too large"
        )


def validate_result(value: Any) -> dict[str, Any]:
    """Independently validate the exact Sprint 2 structured probe result shape."""
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "status",
        "summary",
        "checks",
        "blocking_reason",
    }:
        raise ControllerError(
            "invalid_agent_result", "Execution probe result does not match its schema"
        )
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise ControllerError("invalid_agent_result", "Execution probe schema version is invalid")
    status = value["status"]
    summary = value["summary"]
    checks = value["checks"]
    reason = value["blocking_reason"]
    if (
        status not in {"completed", "blocked", "failed"}
        or not isinstance(summary, str)
        or not summary
        or len(summary.encode()) > 4096
    ):
        raise ControllerError(
            "invalid_agent_result", "Execution probe result status or summary is invalid"
        )
    if not isinstance(checks, list) or checks or len(checks) > 100:
        raise ControllerError("invalid_agent_result", "Execution probe checks must be empty")
    if (status == "completed" and reason is not None) or (
        status != "completed"
        and (not isinstance(reason, str) or not reason or len(reason.encode()) > 4096)
    ):
        raise ControllerError("invalid_agent_result", "Execution probe blocking_reason is invalid")
    try:
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise ControllerError(
            "invalid_agent_result", "Execution probe result is not JSON"
        ) from error
    if (
        len(encoded) > MAX_RESULT_BYTES
        or contains_credential(summary)
        or (isinstance(reason, str) and contains_credential(reason))
    ):
        raise ControllerError(
            "invalid_agent_result", "Execution probe result is unsafe or too large"
        )
    return value


def _atomic_write(path: Path, payload: bytes, *, replace: bool) -> None:
    """Atomically install one complete owner-only artifact through an anchored directory."""
    directory: int | None = None
    temporary_name = f".{path.name}-{uuid.uuid4().hex}.tmp"
    try:
        directory = open_directory(path.parent, create=True)
        try:
            existing = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            not replace or not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1
        ):
            raise ControllerError(
                "invocation_record_failed", "Invocation artifact already exists or is unsafe"
            )
        if replace and existing is None:
            raise ControllerError(
                "invocation_record_failed", "Invocation metadata disappeared during replacement"
            )
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short invocation artifact write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if replace:
            current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if existing is None or (current.st_dev, current.st_ino) != (
                existing.st_dev,
                existing.st_ino,
            ):
                raise ControllerError(
                    "invocation_record_failed", "Invocation metadata changed during replacement"
                )
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
        else:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            os.unlink(temporary_name, dir_fd=directory)
        os.fsync(directory)
    except OSError as error:
        raise ControllerError(
            "invocation_record_failed", f"Could not persist invocation artifact: {path.name}"
        ) from error
    finally:
        if directory is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)


@dataclass(frozen=True, slots=True)
class InvocationPaths:
    """Controller-derived locations for one immutable execution-probe record."""

    directory: Path
    metadata: Path
    prompt: Path
    result: Path
    transcript: Path


def allocate_paths(
    root: Path, multisprint: str, sprint: int, sequence: int, role: str
) -> InvocationPaths:
    """Allocate one never-reused invocation directory beneath the sprint root."""
    _bounded_string(multisprint, "multisprint", 64)
    if (
        not isinstance(sprint, int)
        or isinstance(sprint, bool)
        or sprint <= 0
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence <= 0
        or role != "auditor"
    ):
        raise ControllerError("invocation_record_failed", "Execution probe identity is invalid")
    directory = root / "invocations" / multisprint / str(sprint) / f"{sequence:04d}-{role}"
    if directory.exists():
        raise ControllerError(
            "invocation_record_failed", "Execution probe invocation directory already exists"
        )
    return InvocationPaths(
        directory,
        directory / "metadata.json",
        directory / "prompt.md",
        directory / "result.json",
        directory / "transcript.json",
    )


def new_metadata(
    run_id: str, invocation_id: str, sequence: int, model: str, server_version: str, repository: str
) -> dict[str, Any]:
    """Create the planned exact metadata shape before any external session exists."""
    return {
        "schema_version": 1,
        "run_id": run_id,
        "invocation_id": invocation_id,
        "sequence": sequence,
        "purpose": "execution_probe",
        "role": "auditor",
        "model": model,
        "session_id": None,
        "server_version": server_version,
        "input_commits": {repository: None},
        "status": "planned",
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
        "result": {"available": False, "status": None},
        "transcript": {"status": "pending", "truncated": False},
        "error": None,
    }


def validate_metadata(metadata: dict[str, Any]) -> None:
    """Validate the stable metadata envelope before every atomic replacement."""
    fields = {
        "schema_version",
        "run_id",
        "invocation_id",
        "sequence",
        "purpose",
        "role",
        "model",
        "session_id",
        "server_version",
        "input_commits",
        "status",
        "created_at",
        "started_at",
        "completed_at",
        "result",
        "transcript",
        "error",
    }
    if (
        set(metadata) != fields
        or not isinstance(metadata["schema_version"], int)
        or isinstance(metadata["schema_version"], bool)
        or metadata["schema_version"] != 1
        or metadata["purpose"] != "execution_probe"
        or metadata["role"] != "auditor"
    ):
        raise ControllerError("invocation_record_failed", "Invocation metadata schema is invalid")
    run_id = _bounded_string(metadata["run_id"], "run_id")
    try:
        uuid.UUID(run_id)
    except ValueError as error:
        raise ControllerError("invocation_record_failed", "Invocation run_id is invalid") from error
    _bounded_string(metadata["invocation_id"], "invocation_id")
    _bounded_string(metadata["model"], "model")
    _bounded_string(metadata["server_version"], "server_version")
    if (
        not isinstance(metadata["sequence"], int)
        or isinstance(metadata["sequence"], bool)
        or metadata["sequence"] <= 0
        or metadata["status"] not in {"planned", "session_created", "running", *_TERMINAL}
    ):
        raise ControllerError(
            "invocation_record_failed", "Invocation metadata lifecycle is invalid"
        )
    if metadata["invocation_id"] != f"{metadata['sequence']:04d}-auditor":
        raise ControllerError("invocation_record_failed", "Invocation metadata identity is invalid")
    if metadata["session_id"] is not None:
        _bounded_string(metadata["session_id"], "session_id")
    timestamps = {
        name: _metadata_timestamp(metadata[name], name)
        for name in ("created_at", "started_at", "completed_at")
    }
    if timestamps["created_at"] is None:
        raise ControllerError("invocation_record_failed", "Invocation created_at is required")
    input_commits = metadata["input_commits"]
    if (
        not isinstance(input_commits, dict)
        or len(input_commits) != 1
        or any(
            not isinstance(key, str)
            or not key
            or len(key.encode("utf-8")) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in key)
            or value is not None
            for key, value in input_commits.items()
        )
    ):
        raise ControllerError("invocation_record_failed", "Invocation input commits are invalid")
    result = metadata["result"]
    transcript = metadata["transcript"]
    if not isinstance(result, dict) or set(result) != {"available", "status"}:
        raise ControllerError(
            "invocation_record_failed", "Invocation metadata artifacts are invalid"
        )
    if (
        not isinstance(result["available"], bool)
        or result["status"] not in {None, "completed", "blocked", "failed"}
        or result["available"] != (result["status"] is not None)
    ):
        raise ControllerError("invocation_record_failed", "Invocation result metadata is invalid")
    if not isinstance(transcript, dict) or set(transcript) != {"status", "truncated"}:
        raise ControllerError(
            "invocation_record_failed", "Invocation transcript metadata is invalid"
        )
    if (
        transcript["status"] not in {"pending", "complete", "truncated", "unavailable"}
        or not isinstance(transcript["truncated"], bool)
        or transcript["truncated"] != (transcript["status"] == "truncated")
    ):
        raise ControllerError("invocation_record_failed", "Invocation transcript status is invalid")
    error_value = metadata["error"]
    if error_value is not None:
        if not isinstance(error_value, dict) or set(error_value) != {"code", "message"}:
            raise ControllerError(
                "invocation_record_failed", "Invocation error metadata is invalid"
            )
        _bounded_string(error_value["code"], "error.code")
        _bounded_string(error_value["message"], "error.message", 4096)
    status = metadata["status"]
    has_session = metadata["session_id"] is not None
    has_started = timestamps["started_at"] is not None
    has_completed = timestamps["completed_at"] is not None
    if has_session != has_started:
        raise ControllerError(
            "invocation_record_failed", "Invocation session timestamps are inconsistent"
        )
    if status == "planned":
        valid_lifecycle = (
            not has_session
            and not has_completed
            and result == {"available": False, "status": None}
            and transcript == {"status": "pending", "truncated": False}
            and error_value is None
        )
    elif status in {"session_created", "running"}:
        valid_lifecycle = (
            has_session
            and not has_completed
            and result == {"available": False, "status": None}
            and transcript == {"status": "pending", "truncated": False}
            and error_value is None
        )
    else:
        valid_result = result["available"] and result["status"] == status
        if status in {"completed", "blocked"}:
            valid_lifecycle = has_session and has_completed and valid_result
        elif status == "failed":
            valid_lifecycle = has_completed and (
                valid_result and has_session or not result["available"]
            )
        else:
            valid_lifecycle = has_session and has_completed and not result["available"]
        valid_lifecycle = valid_lifecycle and transcript["status"] != "pending"
        if not result["available"]:
            valid_lifecycle = valid_lifecycle and error_value is not None
    if not valid_lifecycle:
        raise ControllerError(
            "invocation_record_failed", "Invocation metadata lifecycle is inconsistent"
        )
    ordered = [value for value in timestamps.values() if value is not None]
    if ordered != sorted(ordered):
        raise ControllerError("invocation_record_failed", "Invocation timestamps are out of order")
    validate_safe_data(
        metadata,
        code="invocation_record_failed",
        label="Invocation metadata",
        dynamic_key_paths=frozenset({("input_commits",)}),
    )


def _metadata_timestamp(value: Any, field: str) -> datetime | None:
    """Parse one nullable RFC 3339 UTC metadata timestamp."""
    if value is None:
        return None
    if not isinstance(value, str) or not RFC3339_UTC.fullmatch(value):
        raise ControllerError("invocation_record_failed", f"Invocation {field} is invalid")
    try:
        parsed = datetime.fromisoformat(
            value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as error:
        raise ControllerError(
            "invocation_record_failed", f"Invocation {field} is invalid"
        ) from error
    if parsed.tzinfo is None:
        raise ControllerError("invocation_record_failed", f"Invocation {field} is invalid")
    return parsed


def write_metadata(paths: InvocationPaths, metadata: dict[str, Any]) -> None:
    """Atomically write validated metadata, replacing only the lifecycle file."""
    validate_metadata(metadata)
    encoded = (
        json.dumps(metadata, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    ).encode()
    if len(encoded) > MAX_METADATA_BYTES:
        raise ControllerError("invocation_record_failed", "Invocation metadata exceeds 1 MiB")
    _atomic_write(paths.metadata, encoded, replace=paths.metadata.exists())


def write_prompt(paths: InvocationPaths, prompt: str) -> None:
    """Persist exact newline-terminated sanitized prompt bytes before session creation."""
    validate_prompt(prompt)
    _atomic_write(
        paths.prompt, (prompt if prompt.endswith("\n") else prompt + "\n").encode(), replace=False
    )


def write_result(paths: InvocationPaths, result: dict[str, Any]) -> None:
    """Persist only an independently validated structured result."""
    validate_result(result)
    _atomic_write(
        paths.result,
        (json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode(),
        replace=False,
    )


def _truncate_text(value: str, limit: int) -> tuple[str, bool]:
    """Truncate UTF-8 safely with the documented visible marker."""
    encoded = value.encode()
    if len(encoded) <= limit:
        return value, False
    marker = "\n[TRUNCATED]"
    prefix = encoded[: max(0, limit - len(marker.encode()))]
    while prefix:
        try:
            return prefix.decode() + marker, True
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return marker, True


def transcript_wrapper(session_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Recursively redact and deterministically bound opaque message evidence."""
    _bounded_string(session_id, "session_id")
    if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
        raise ControllerError("transcript_capture_failed", "OpenCode transcript is malformed")
    sanitized = redact_external_data(messages)
    truncated = False

    def bound(value: Any) -> Any:
        nonlocal truncated
        if isinstance(value, str):
            text, was_truncated = _truncate_text(value, MAX_STRING_BYTES)
            truncated |= was_truncated
            return text
        if isinstance(value, list):
            return [bound(item) for item in value]
        if isinstance(value, dict):
            return {str(key): bound(item) for key, item in value.items()}
        return value

    try:
        content = json.dumps(
            bound(sanitized),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ControllerError(
            "transcript_capture_failed", "OpenCode transcript is malformed"
        ) from error
    original_bytes = len(content.encode())
    wrapper = {
        "schema_version": 1,
        "session_id": session_id,
        "format": "opencode-messages-json-v1",
        "sanitized": True,
        "truncated": truncated,
        "original_bytes": original_bytes,
        "content": content,
    }

    def serialized_size(candidate: str) -> int:
        wrapper["content"] = candidate
        return (
            len(json.dumps(wrapper, sort_keys=True, ensure_ascii=True, allow_nan=False).encode())
            + 1
        )

    if serialized_size(content) > MAX_TRANSCRIPT_BYTES:
        marker = "\n[TRUNCATED]"
        empty_json_string = json.dumps("", ensure_ascii=True).encode()
        fixed_bytes = serialized_size("") - len(empty_json_string)
        marker_inner = json.dumps(marker, ensure_ascii=True).encode()[1:-1]
        escaped_content = json.dumps(content, ensure_ascii=True).encode()[1:-1]
        prefix = escaped_content[
            : max(0, MAX_TRANSCRIPT_BYTES - fixed_bytes - 2 - len(marker_inner))
        ]
        while True:
            try:
                decoded_prefix = json.loads(b'"' + prefix + b'"')
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                prefix = prefix[:-1]
        wrapper["content"] = decoded_prefix + marker
        wrapper["truncated"] = True
    return wrapper


def write_transcript(paths: InvocationPaths, wrapper: dict[str, Any]) -> None:
    """Persist one sanitized bounded transcript wrapper without replacement."""
    encoded = (
        json.dumps(wrapper, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    ).encode()
    if len(encoded) > MAX_TRANSCRIPT_BYTES:
        raise ControllerError("transcript_capture_failed", "Sanitized transcript exceeds 8 MiB")
    _atomic_write(paths.transcript, encoded, replace=False)
