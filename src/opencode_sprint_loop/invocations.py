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
from .safeio import open_directory, open_regular
from .security import (
    contains_credential,
    external_utf8_bytes,
    redact_external_data,
    validate_external_utf8,
    validate_safe_data,
)
from .state import RFC3339_UTC, utc_now

MAX_PROMPT_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_STRING_BYTES = 1024 * 1024
_TERMINAL = {"completed", "blocked", "failed", "timed_out", "interrupted"}


def _bounded_string(value: Any, field: str, limit: int = 1024) -> str:
    """Validate one bounded, control-character-free invocation identifier."""
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ControllerError("invocation_record_failed", f"Invocation {field} is invalid")
    if (
        len(
            external_utf8_bytes(value, code="invocation_record_failed", label=f"Invocation {field}")
        )
        > limit
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
    if len(
        external_utf8_bytes(prompt, code="invocation_record_failed", label="Execution probe prompt")
    ) > MAX_PROMPT_BYTES or contains_credential(prompt):
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
        not isinstance(status, str)
        or status not in {"completed", "blocked", "failed"}
        or not isinstance(summary, str)
        or not summary
        or len(
            external_utf8_bytes(
                summary, code="invalid_agent_result", label="Execution probe summary"
            )
        )
        > 4096
    ):
        raise ControllerError(
            "invalid_agent_result", "Execution probe result status or summary is invalid"
        )
    if not isinstance(checks, list) or checks or len(checks) > 100:
        raise ControllerError("invalid_agent_result", "Execution probe checks must be empty")
    if (status == "completed" and reason is not None) or (
        status != "completed"
        and (
            not isinstance(reason, str)
            or not reason
            or len(
                external_utf8_bytes(
                    reason, code="invalid_agent_result", label="Execution probe blocking_reason"
                )
            )
            > 4096
        )
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
            or len(
                external_utf8_bytes(
                    key, code="invocation_record_failed", label="Invocation input commit name"
                )
            )
            > 1024
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
    encoded = external_utf8_bytes(value, code="transcript_capture_failed", label="Transcript text")
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


def validate_transcript_messages(
    messages: Any, expected_result: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Validate probe message envelopes, association, and forbidden-tool evidence."""
    if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
        raise ControllerError("transcript_capture_failed", "OpenCode transcript is malformed")
    user_messages: list[tuple[int, str]] = []
    assistant_results: list[tuple[int, dict[str, Any], Any]] = []
    for index, message in enumerate(messages):
        raw_info = message.get("info")
        if raw_info is not None and not isinstance(raw_info, dict):
            raise ControllerError("transcript_capture_failed", "OpenCode transcript is malformed")
        info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
        role = message.get("role", info.get("role"))
        identifier = message.get("id", info.get("id"))
        parts = message.get("parts")
        if (
            role not in {"user", "assistant"}
            or not isinstance(identifier, str)
            or not identifier
            or len(
                external_utf8_bytes(
                    identifier,
                    code="transcript_capture_failed",
                    label="Transcript message identifier",
                )
            )
            > 1024
            or not isinstance(parts, list)
        ):
            raise ControllerError("transcript_capture_failed", "OpenCode transcript is malformed")
        if role == "user":
            user_messages.append((index, identifier))
        message_results: list[Any] = []
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("type"), str):
                raise ControllerError(
                    "transcript_capture_failed", "OpenCode transcript is malformed"
                )
            kind = part["type"]
            if kind == "permission":
                raise ControllerError(
                    "unexpected_probe_tool",
                    "Execution probe transcript contains a permission request",
                )
            if kind == "tool":
                tool = part.get("tool", part.get("name"))
                if tool == "StructuredOutputError":
                    raise ControllerError(
                        "invalid_agent_result",
                        "OpenCode transcript reports structured output failure",
                    )
                if tool != "StructuredOutput":
                    raise ControllerError(
                        "unexpected_probe_tool",
                        "Execution probe transcript contains a forbidden tool",
                    )
            if kind in {"structured_output", "json_schema"}:
                message_results.append(part.get("value", part.get("output")))
        if info.get("structured") is not None:
            message_results.append(info["structured"])
        if message.get("structured_output") is not None:
            message_results.append(message["structured_output"])
        message_error = info.get("error", message.get("error"))
        if message_error == "StructuredOutputError" or (
            isinstance(message_error, dict) and message_error.get("name") == "StructuredOutputError"
        ):
            raise ControllerError(
                "invalid_agent_result", "OpenCode transcript reports structured output failure"
            )
        if len(message_results) > 1:
            raise ControllerError("transcript_capture_failed", "OpenCode transcript is malformed")
        if message_results:
            assistant_results.append((index, message, message_results[0]))
    if expected_result is not None:
        if len(user_messages) != 1 or len(assistant_results) != 1:
            raise ControllerError(
                "transcript_capture_failed",
                "OpenCode transcript does not contain the expected terminal response",
            )
        user_index, user_id = user_messages[0]
        assistant_index, assistant, captured_result = assistant_results[0]
        raw_info = assistant.get("info")
        info = raw_info if isinstance(raw_info, dict) else {}
        parent = assistant.get(
            "parentID", assistant.get("parent_id", info.get("parentID", info.get("parent_id")))
        )
        role = assistant.get("role", info.get("role"))
        if (
            role != "assistant"
            or assistant_index <= user_index
            or parent != user_id
            or info.get("error", assistant.get("error")) is not None
            or captured_result != expected_result
        ):
            raise ControllerError(
                "transcript_capture_failed",
                "OpenCode transcript terminal response is inconsistent",
            )
    return messages


def transcript_wrapper(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    expected_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Safely redact and bound opaque transcript evidence before semantic acceptance."""
    _bounded_string(session_id, "session_id")
    del expected_result
    if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
        raise ControllerError("transcript_capture_failed", "OpenCode transcript is malformed")
    validate_external_utf8(messages, code="transcript_capture_failed", label="OpenCode transcript")
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
    original_bytes = len(
        external_utf8_bytes(content, code="transcript_capture_failed", label="Sanitized transcript")
    )
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


def _record_error(message: str, error: BaseException | None = None) -> ControllerError:
    """Build the stable read-side invocation consistency error."""
    return ControllerError("inconsistent_invocation_record", message)


def _read_artifact(path: Path, limit: int) -> bytes:
    """Read one bounded single-link regular invocation artifact."""
    descriptor: int | None = None
    directory: int | None = None
    try:
        descriptor, directory = open_regular(path, os.O_RDONLY)
        size = os.fstat(descriptor).st_size
        if size > limit:
            raise _record_error(f"Invocation artifact exceeds its bound: {path.name}")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit or len(payload) != size:
            raise _record_error(f"Invocation artifact is incomplete: {path.name}")
        return payload
    except ControllerError as error:
        if error.code == "inconsistent_invocation_record":
            raise
        raise _record_error(f"Invocation artifact is unsafe: {path.name}", error) from error
    except (OSError, FileNotFoundError) as error:
        raise _record_error(f"Cannot read invocation artifact: {path.name}", error) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _load_artifact_object(path: Path, limit: int) -> dict[str, Any]:
    """Strictly decode one bounded invocation JSON object."""
    raw = _read_artifact(path, limit)

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise _record_error(f"Invocation artifact is malformed: {path.name}", error) from error
    if not isinstance(value, dict):
        raise _record_error(f"Invocation artifact is not an object: {path.name}")
    return value


def _validate_transcript_wrapper(
    wrapper: dict[str, Any], session_id: str, expected_result: dict[str, Any] | None
) -> None:
    """Validate a persisted transcript wrapper and its untruncated opaque content."""
    if (
        set(wrapper)
        != {
            "schema_version",
            "session_id",
            "format",
            "sanitized",
            "truncated",
            "original_bytes",
            "content",
        }
        or wrapper["schema_version"] != 1
        or isinstance(wrapper["schema_version"], bool)
        or wrapper["session_id"] != session_id
        or wrapper["format"] != "opencode-messages-json-v1"
        or wrapper["sanitized"] is not True
        or not isinstance(wrapper["truncated"], bool)
        or not isinstance(wrapper["original_bytes"], int)
        or isinstance(wrapper["original_bytes"], bool)
        or wrapper["original_bytes"] < 0
        or not isinstance(wrapper["content"], str)
    ):
        raise _record_error("Invocation transcript wrapper is inconsistent")
    content = wrapper["content"]
    if not wrapper["truncated"]:
        try:
            messages = json.loads(
                content,
                object_pairs_hook=lambda pairs: dict(pairs)
                if len({key for key, _ in pairs}) == len(pairs)
                else (_ for _ in ()).throw(ValueError("duplicate key")),
                parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            )
            validate_external_utf8(
                messages, code="inconsistent_invocation_record", label="Invocation transcript"
            )
            if expected_result is not None:
                validate_transcript_messages(messages, expected_result)
        except ControllerError as error:
            raise _record_error("Invocation transcript evidence is inconsistent", error) from error
        except (json.JSONDecodeError, ValueError, RecursionError) as error:
            raise _record_error("Invocation transcript content is malformed", error) from error
        canonical = json.dumps(
            messages, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        if canonical != content or wrapper["original_bytes"] != len(
            external_utf8_bytes(
                content, code="inconsistent_invocation_record", label="Invocation transcript"
            )
        ):
            raise _record_error("Invocation transcript content is not canonical")


def validate_invocation_records(
    root: Path,
    config: Any,
    state: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    """Cross-validate Sprint 2 invocation metadata, artifacts, state, and events."""
    base = root / "invocations" / config.multisprint / str(config.sprint)
    expected_name = "0001-auditor"
    started = [event for event in events if event["type"] == "agent.started"]
    terminals = [
        event for event in events if event["type"] in {"agent.completed", "agent.interrupted"}
    ]
    try:
        base_descriptor = open_directory(base)
    except FileNotFoundError:
        if started or terminals:
            raise _record_error("Invocation event history has no invocation record")
        return
    except (OSError, ControllerError) as error:
        raise _record_error("Invocation record directory is unsafe", error) from error
    try:
        entries = set(os.listdir(base_descriptor))
    finally:
        os.close(base_descriptor)
    if entries != {expected_name}:
        raise _record_error("Invocation record paths do not match the configured probe")
    paths = InvocationPaths(
        base / expected_name,
        base / expected_name / "metadata.json",
        base / expected_name / "prompt.md",
        base / expected_name / "result.json",
        base / expected_name / "transcript.json",
    )
    try:
        invocation_descriptor = open_directory(paths.directory)
        try:
            artifact_names = set(os.listdir(invocation_descriptor))
        finally:
            os.close(invocation_descriptor)
    except (OSError, ControllerError) as error:
        raise _record_error("Invocation artifact directory is unsafe", error) from error
    if not {"metadata.json", "prompt.md"} <= artifact_names or not artifact_names <= {
        "metadata.json",
        "prompt.md",
        "result.json",
        "transcript.json",
    }:
        raise _record_error("Invocation artifact paths do not match the probe contract")
    metadata = _load_artifact_object(paths.metadata, MAX_METADATA_BYTES)
    try:
        validate_metadata(metadata)
    except ControllerError as error:
        raise _record_error("Invocation metadata is invalid", error) from error
    expected_prompt = probe_prompt(config.multisprint, config.sprint, expected_name)
    prompt = _read_artifact(paths.prompt, MAX_PROMPT_BYTES)
    if prompt != expected_prompt.encode("utf-8"):
        raise _record_error("Invocation prompt does not match the configured probe")
    if (
        metadata["run_id"] != state["run_id"]
        or metadata["invocation_id"] != expected_name
        or metadata["sequence"] != 1
        or metadata["role"] != "auditor"
        or metadata["model"] != config.models["auditor"]
        or metadata["server_version"] != state["server"]["version"]
        or set(metadata["input_commits"]) != {config.repositories[0].name}
    ):
        raise _record_error("Invocation metadata identity is inconsistent")
    if len(started) > 1 or len(terminals) > 1:
        raise _record_error("Invocation event history has repeated lifecycle events")
    if terminals and not started:
        raise _record_error("Terminal invocation event has no matching start")
    if started:
        start_payload = started[0]["payload"]
        if any(
            start_payload[field] != metadata[field]
            for field in ("invocation_id", "role", "session_id")
        ):
            raise _record_error("Invocation metadata does not match its start event")
    if terminals:
        terminal = terminals[0]
        payload = terminal["payload"]
        if any(
            payload[field] != started[0]["payload"][field]
            for field in ("invocation_id", "role", "session_id")
        ):
            raise _record_error("Terminal invocation event does not match its start event")
        if terminal["type"] == "agent.completed":
            if (
                metadata["status"] not in {"completed", "blocked", "failed"}
                or payload["result_status"] != metadata["status"]
            ):
                raise _record_error("Completion event does not match terminal metadata")
        elif metadata["status"] not in {"timed_out", "interrupted"}:
            raise _record_error("Interruption event does not match terminal metadata")
    active = state["active_invocation"]
    if active is not None and any(
        active[field] != metadata[field]
        for field in ("invocation_id", "sequence", "role", "model", "session_id", "started_at")
    ):
        raise _record_error("Active state does not match invocation metadata")
    result_exists = os.path.lexists(paths.result)
    transcript_exists = os.path.lexists(paths.transcript)
    result: dict[str, Any] | None = None
    if result_exists:
        result = _load_artifact_object(paths.result, MAX_RESULT_BYTES)
        try:
            validate_result(result)
        except ControllerError as error:
            raise _record_error("Invocation result artifact is invalid", error) from error
    wrapper: dict[str, Any] | None = None
    if transcript_exists:
        wrapper = _load_artifact_object(paths.transcript, MAX_TRANSCRIPT_BYTES)
        if metadata["session_id"] is None:
            raise _record_error("Transcript exists without a known session")
        _validate_transcript_wrapper(wrapper, metadata["session_id"], result)
    metadata_terminal = metadata["status"] in _TERMINAL
    if metadata_terminal:
        if result_exists != metadata["result"]["available"]:
            raise _record_error("Invocation result availability contradicts metadata")
        if result is not None and result["status"] != metadata["result"]["status"]:
            raise _record_error("Invocation result status contradicts metadata")
        transcript_status = metadata["transcript"]["status"]
        if transcript_exists != (transcript_status in {"complete", "truncated"}):
            raise _record_error("Invocation transcript availability contradicts metadata")
        if wrapper is not None and wrapper["truncated"] != (transcript_status == "truncated"):
            raise _record_error("Invocation transcript truncation contradicts metadata")
    elif metadata["status"] == "planned" and (result_exists or transcript_exists):
        raise _record_error("Planned invocation has impossible write-ahead artifacts")
    if state["reason"] is not None and state["reason"]["code"] == "execution_not_implemented":
        if (
            not terminals
            or terminals[0]["type"] != "agent.completed"
            or metadata["status"] != "completed"
            or metadata["transcript"]["status"] not in {"complete", "truncated"}
        ):
            raise _record_error("Execution placeholder lacks a complete invocation record")
