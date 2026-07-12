"""Bounded JSON input and deterministic JSON serialization helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import ControllerError

MAX_JSON_BYTES = 1024 * 1024


def load_json_object(path: Path, *, code: str) -> dict[str, Any]:
    """Load one bounded UTF-8 JSON object while rejecting duplicate keys."""
    try:
        size = path.stat().st_size
    except FileNotFoundError as error:
        raise ControllerError("missing_required_file", f"Required file is missing: {path}") from error
    if size > MAX_JSON_BYTES:
        raise ControllerError(code, f"JSON input exceeds 1 MiB: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ControllerError(code, f"JSON input is not UTF-8: {path}") from error

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ControllerError(code, f"Duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    try:
        data = json.loads(text, object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, ControllerError) as error:
        if isinstance(error, ControllerError):
            raise
        raise ControllerError(code, f"Malformed JSON in {path}: {error.msg}") from error
    if not isinstance(data, dict):
        raise ControllerError(code, f"JSON root must be an object: {path}")
    return data


def dump_json(data: object) -> str:
    """Serialize JSON deterministically with one trailing newline."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
