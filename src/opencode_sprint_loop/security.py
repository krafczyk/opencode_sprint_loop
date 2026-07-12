"""Credential detection and diagnostic redaction for durable controller data."""

from __future__ import annotations

import re
from typing import Any

from .errors import ControllerError


_SENSITIVE_FIELD = re.compile(r"(?:credential|password|secret|token|api[_-]?key|authorization)", re.IGNORECASE)
_PROVIDER_TOKEN = r"(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"
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


def validate_safe_data(value: Any, *, code: str, label: str) -> None:
    """Reject credential-bearing fields and values before durable use."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or _SENSITIVE_FIELD.search(key):
                raise ControllerError(code, f"{label} must not contain credential-bearing fields")
            validate_safe_data(nested, code=code, label=label)
    elif isinstance(value, list):
        for nested in value:
            validate_safe_data(nested, code=code, label=label)
    elif isinstance(value, str) and contains_credential(value):
        raise ControllerError(code, f"{label} must not contain credential-bearing values")


def redact_diagnostic(message: str) -> str:
    """Redact common credentials from diagnostics before writing standard error."""
    message = re.sub(
        r"(?i)(\b(?:authorization|proxy-authorization)\s*:\s*(?:basic|bearer)\s+)\S+",
        r"\1[REDACTED]",
        message,
    )
    message = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", message)
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
