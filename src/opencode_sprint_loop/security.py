"""Credential detection and diagnostic redaction for durable controller data."""

from __future__ import annotations

import re
from typing import Any

from .errors import ControllerError


_ASCII_CASE = re.ASCII | re.IGNORECASE
_ASCII_WS = r"[ \t\n\r\f\v]"
_ASCII_VALUE = r"[\x21-\x7e]"
_SENSITIVE_FIELD = re.compile(
    r"(?:credential|password|secret|token|api[_-]?key|authorization)", _ASCII_CASE
)
_PROVIDER_TOKEN = (
    r"(?:"
    r"ghs_[A-Za-z0-9._-]{36,}|gh[opur]_[A-Za-z0-9]{36}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"gl(?:pat|cbt|ptt|rt|imt|soat|dt|rtr|ft|agent|wt|ffct|oas)-[A-Za-z0-9_-]{20,}|"
    r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}|"
    r"sk-ant-(?:api|oat)\d{2}-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9_-]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|hf_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|x(?:app|wfp)-[A-Za-z0-9-]{20,}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}"
    r")"
)
# Anchor a scheme to its first valid character. This keeps credential scans of
# arbitrary long non-URI text linear while accepting every valid URI scheme.
_URI = r"(?<![a-z0-9+.-])[a-z][a-z0-9+.-]*://"
_URI_QUERY_OR_FRAGMENT = (
    rf"{_URI}[\x21-\x22\x24-\x3e\x40-\x7e]+"
    rf"(?:\?#[\x21-\x7e]+|\?[\x21-\x22\x24-\x7e]+|#[\x21-\x7e]+)"
)
_CREDENTIAL_VALUE = re.compile(
    rf"(?:\b(?:authorization|proxy-authorization){_ASCII_WS}*:{_ASCII_WS}*"
    rf"(?:basic|bearer){_ASCII_WS}+{_ASCII_VALUE}+|"
    rf"{_URI}[!-\.0-?A-~]*@|"
    rf"{_URI_QUERY_OR_FRAGMENT}|"
    r"[?&#](?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)="
    rf"[\x21-\x22\x24-\x25\x27-\x7e]+|"
    r"(?<![a-z0-9_-])(?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)"
    rf"{_ASCII_WS}*(?:=|:){_ASCII_WS}*{_ASCII_VALUE}+|"
    rf"{_PROVIDER_TOKEN}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    _ASCII_CASE,
)


def contains_credential(value: str) -> bool:
    """Return whether text contains a common credential-bearing representation."""
    return bool(_CREDENTIAL_VALUE.search(value))


def external_utf8_bytes(value: Any, *, code: str, label: str) -> bytes:
    """Encode one external string or raise its caller's stable failure code.

    JSON permits escaped unpaired surrogates, but controller artifacts are UTF-8
    records.  Keep that mismatch at the external-data boundary rather than
    allowing ``UnicodeEncodeError`` to escape a lifecycle path.
    """
    if not isinstance(value, str):
        raise ControllerError(code, f"{label} is not a string")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ControllerError(code, f"{label} is not valid UTF-8") from error


def validate_external_utf8(value: Any, *, code: str, label: str) -> None:
    """Reject unencodable strings and keys in recursively retained external data."""
    if isinstance(value, str):
        external_utf8_bytes(value, code=code, label=label)
    elif isinstance(value, list):
        for item in value:
            validate_external_utf8(item, code=code, label=label)
    elif isinstance(value, dict):
        for key, item in value.items():
            external_utf8_bytes(key, code=code, label=label)
            validate_external_utf8(item, code=code, label=label)


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
                    or contains_credential(key)
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
        rf"(?ai)(\b(?:authorization|proxy-authorization){_ASCII_WS}*:{_ASCII_WS}*"
        rf"(?:basic|bearer){_ASCII_WS}+){_ASCII_VALUE}+",
        r"\1[REDACTED]",
        message,
    )
    message = re.sub(rf"(?ai)({_URI})[!-\.0-?A-~]*@", r"\1[REDACTED]@", message)
    # Query keys are not a reliable secret classifier, so no query value or
    # fragment may reach diagnostics even when it uses an unfamiliar name.
    message = re.sub(
        rf"(?ai)({_URI}[\x21-\x22\x24-\x3e\x40-\x7e]+)"
        rf"\?[\x21-\x22\x24-\x7e]*",
        r"\1?[REDACTED]",
        message,
    )
    message = re.sub(
        rf"(?ai)({_URI}[\x21-\x22\x24-\x7e]+)#[\x21-\x7e]*",
        r"\1#[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?ai)([?&#](?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)=)"
        r"[\x21-\x22\x24-\x25\x27-\x7e]+",
        r"\1[REDACTED]",
        message,
    )
    message = re.sub(rf"(?ai){_PROVIDER_TOKEN}", "[REDACTED]", message)
    return re.sub(
        rf"(?ai)((?<![a-z0-9_-])(?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)"
        rf"{_ASCII_WS}*(?:=|:){_ASCII_WS}*){_ASCII_VALUE}+",
        r"\1[REDACTED]",
        message,
    )


def redact_external_data(value: Any) -> Any:
    """Recursively replace recognizable credentials in untrusted transcript data.

    This is deliberately separate from ``validate_safe_data``: controller-authored
    records fail closed, while external transcript evidence remains useful after
    conventional credential values have been replaced.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            text_key = str(key)
            sanitized_key = "[REDACTED]" if contains_credential(text_key) else text_key
            if sanitized_key in result:
                # JSON cannot faithfully retain two external fields once key
                # redaction maps them to the same safe key.  Do not choose one
                # silently because that would make the transcript lossy and
                # dependent on input ordering.
                raise ControllerError(
                    "transcript_capture_failed",
                    "OpenCode transcript contains colliding sanitized object keys",
                )
            result[sanitized_key] = (
                "[REDACTED]" if _SENSITIVE_FIELD.search(text_key) else redact_external_data(child)
            )
        return result
    if isinstance(value, list):
        return [redact_external_data(child) for child in value]
    if isinstance(value, str):
        return "[REDACTED]" if contains_credential(value) else redact_diagnostic(value)
    return value
