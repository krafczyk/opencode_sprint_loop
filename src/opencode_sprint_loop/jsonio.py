"""Bounded JSON input and deterministic JSON serialization helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, BinaryIO

from .errors import ControllerError

MAX_JSON_BYTES = 1024 * 1024


def load_json_object_handle(handle: BinaryIO, path: Path, *, code: str) -> dict[str, Any]:
    """Load one bounded UTF-8 JSON object from an already-safe file handle."""
    try:
        if handle.seek(0, 2) > MAX_JSON_BYTES:
            raise ControllerError(code, f"JSON input exceeds 1 MiB: {path}")
        handle.seek(0)
        raw = handle.read(MAX_JSON_BYTES + 1)
    except OSError as error:
        raise ControllerError(code, f"Cannot inspect JSON input: {path}") from error
    if len(raw) > MAX_JSON_BYTES:
        raise ControllerError(code, f"JSON input exceeds 1 MiB: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ControllerError(code, f"JSON input is not UTF-8: {path}") from error
    except OSError as error:
        raise ControllerError(code, f"Cannot read JSON input: {path}") from error

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ControllerError(code, f"Duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"Non-finite JSON value {value}")

    try:
        data = json.loads(text, object_pairs_hook=reject_duplicates, parse_constant=reject_nonfinite)
    except (json.JSONDecodeError, ControllerError, RecursionError, ValueError) as error:
        if isinstance(error, ControllerError):
            raise
        detail = error.msg if isinstance(error, json.JSONDecodeError) else str(error)
        raise ControllerError(code, f"Malformed JSON in {path}: {detail}") from error
    if not isinstance(data, dict):
        raise ControllerError(code, f"JSON root must be an object: {path}")
    return data


def load_json_object(path: Path, *, code: str) -> dict[str, Any]:
    """Load one bounded UTF-8 JSON object while rejecting duplicate keys."""
    try:
        with path.open("rb") as handle:
            return load_json_object_handle(handle, path, code=code)
    except FileNotFoundError as error:
        raise ControllerError("missing_required_file", f"Required file is missing: {path}") from error
    except OSError as error:
        raise ControllerError(code, f"Cannot inspect JSON input: {path}") from error


def dump_json(data: object) -> str:
    """Serialize JSON deterministically with one trailing newline."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
