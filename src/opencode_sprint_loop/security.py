"""Credential detection and diagnostic redaction for durable controller data."""

from __future__ import annotations

import re
from typing import Any

from .errors import ControllerError


_SENSITIVE_FIELD = re.compile(
    r"(?:credential|password|secret|token|api[_-]?key|authorization)", re.IGNORECASE
)
_PROVIDER_TOKEN = (
    r"(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"
)
_CREDENTIAL_VALUE = re.compile(
    r"(?:\b(?:authorization|proxy-authorization)\s*:\s*(?:basic|bearer)\s+\S+|"
    r"https?://[^/\s@]+@|"
    r"[?&#](?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)=[^&#\s]+|"
    r"(?<![a-z0-9_-])(?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)\s*(?:=|:)\s*\S+|"
    rf"{_PROVIDER_TOKEN}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


def contains_credential(value: str) -> bool:
    """Return whether text contains a common credential-bearing representation."""
    return bool(_CREDENTIAL_VALUE.search(value))


def validate_safe_data(
    value: Any,
    *,
    code: str,
    label: str,
    dynamic_key_paths: frozenset[tuple[str, ...]] = frozenset(),
) -> None:
    """Reject credentials while permitting declared paths to use validated dynamic keys."""

    def visit(nested_value: Any, path: tuple[str, ...]) -> None:
        if isinstance(nested_value, dict):
            dynamic_keys = path in dynamic_key_paths
            for key, child in nested_value.items():
                if (
                    not isinstance(key, str)
                    or (dynamic_keys and contains_credential(key))
                    or (not dynamic_keys and _SENSITIVE_FIELD.search(key))
                ):
                    raise ControllerError(
                        code, f"{label} must not contain credential-bearing fields"
                    )
                visit(child, (*path, key))
        elif isinstance(nested_value, list):
            for child in nested_value:
                visit(child, (*path, "[]"))
        elif isinstance(nested_value, str) and contains_credential(nested_value):
            raise ControllerError(code, f"{label} must not contain credential-bearing values")

    visit(value, ())


def redact_diagnostic(message: str) -> str:
    """Redact credentials plus all URI query values and fragments."""
    message = re.sub(
        r"(?i)(\b(?:authorization|proxy-authorization)\s*:\s*(?:basic|bearer)\s+)\S+",
        r"\1[REDACTED]",
        message,
    )
    uri = r"[a-z][a-z0-9+.-]*://"
    message = re.sub(rf"(?i)({uri})[^/\s@]+@", r"\1[REDACTED]@", message)
    # Query keys are not a reliable secret classifier, so no query value or
    # fragment may reach diagnostics even when it uses an unfamiliar name.
    message = re.sub(rf"(?i)({uri}[^\s?#]+)\?[^#\s]*", r"\1?[REDACTED]", message)
    message = re.sub(rf"(?i)({uri}[^\s#]+)#[^\s]*", r"\1#[REDACTED]", message)
    message = re.sub(
        r"(?i)([?&#](?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)=)[^&#\s]+",
        r"\1[REDACTED]",
        message,
    )
    message = re.sub(rf"(?i){_PROVIDER_TOKEN}", "[REDACTED]", message)
    return re.sub(
        r"(?i)((?<![a-z0-9_-])(?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)\s*(?:=|:)\s*)\S+",
        r"\1[REDACTED]",
        message,
    )
