"""Standard-library HTTP adapter for the documented Sprint 2 OpenCode API."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .agent_runner import (
    AbortObservation,
    CreatedSession,
    InvocationObservation,
    InvocationRequest,
    ServerValidationRequest,
    TranscriptCapture,
    ValidatedServer,
)
from .errors import ControllerError
from .security import external_utf8_bytes

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10
# Sprint 2 supports only SemVer release versions in the documented 1.17.x
# compatibility window.  In particular, leading-zero numeric components are
# malformed rather than an alternate spelling of a supported release.
_VERSION = re.compile(r"^1\.17\.(?:0|[1-9]\d*)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PERMISSIONS = ({"permission": "*", "pattern": "*", "action": "deny"},)


def _bounded_identifier(value: Any) -> TypeGuard[str]:
    """Return whether one server identifier is safe for durable controller records."""
    if not isinstance(value, str) or not value or _CONTROL.search(value):
        return False
    try:
        return (
            len(
                external_utf8_bytes(
                    value, code="malformed_server_response", label="OpenCode server identifier"
                )
            )
            <= 1024
        )
    except ControllerError:
        # Strict JSON permits escaped unpaired surrogates.  They are not valid
        # durable external identifiers, but must remain an expected response
        # validation failure rather than escaping as a controller exception.
        return False


def _server_absolute_path(value: Any, label: str) -> Path:
    """Require one non-empty absolute server path before canonicalizing it."""
    if not isinstance(value, str) or not value:
        raise ControllerError("malformed_server_response", f"OpenCode {label} path is invalid")
    path = Path(value)
    if not path.is_absolute():
        raise ControllerError("malformed_server_response", f"OpenCode {label} path is not absolute")
    try:
        return path.resolve()
    except (OSError, RuntimeError, UnicodeEncodeError) as error:
        raise ControllerError(
            "malformed_server_response", f"OpenCode {label} path is invalid"
        ) from error


def parse_server_url(value: str) -> str:
    """Validate and normalize a credential-free absolute OpenCode origin."""
    if (
        not isinstance(value, str)
        or not value
        or _CONTROL.search(value)
        or re.search(r"%(?![0-9A-Fa-f]{2})", value)
    ):
        raise ControllerError(
            "invalid_server_url", "Server URL must be a valid credential-free origin"
        )
    try:
        parsed = urlsplit(value)
        if parsed.netloc.rsplit("@", 1)[-1].endswith(":"):
            raise ControllerError("invalid_server_url", "Server URL has an empty port")
        port = parsed.port
    except ControllerError:
        raise
    except ValueError as error:
        raise ControllerError("invalid_server_url", "Server URL has an invalid port") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ControllerError(
            "invalid_server_url",
            "Server URL must be an absolute HTTP or HTTPS origin without credentials",
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ControllerError(
            "invalid_server_url", "Server URL must not contain a path, query, or fragment"
        )
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
            raise ControllerError("invalid_server_url", "Server URL host is invalid")
    else:
        host = address.compressed
    if port == 0:
        raise ControllerError("invalid_server_url", "Server URL has an invalid port")
    if ":" in host:
        host = f"[{host}]"
    default = 80 if parsed.scheme == "http" else 443
    suffix = "" if port is None or port == default else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


@dataclass(frozen=True, slots=True)
class HTTPAuthentication:
    """In-memory Basic authentication assembled from inherited environment only."""

    username: str | None
    password: str | None

    @classmethod
    def from_environment(cls) -> "HTTPAuthentication":
        """Read supported inherited credentials without persisting or logging them."""
        password = os.environ.get("OPENCODE_SERVER_PASSWORD") or None
        username = os.environ.get("OPENCODE_SERVER_USERNAME") or None
        if username and not password:
            raise ControllerError(
                "invalid_server_authentication", "OpenCode username requires a password"
            )
        return cls(username or ("opencode" if password else None), password)

    def header(self) -> str | None:
        """Build the Basic header only at request construction time."""
        if self.password is None or self.username is None:
            return None
        value = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return f"Basic {value}"


class _NoRedirect(HTTPRedirectHandler):
    """Reject redirects before urllib can forward an authorization header."""

    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class OpenCodeServerRunner:
    """Synchronous HTTP ``AgentRunner`` using only documented OpenCode endpoints."""

    def __init__(self, server_url: str, *, timeout_seconds: int = REQUEST_TIMEOUT_SECONDS) -> None:
        """Construct the adapter after URL and inherited-auth validation, without I/O."""
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ControllerError("invalid_server_url", "Server request timeout must be positive")
        self.origin = parse_server_url(server_url)
        self.authentication = HTTPAuthentication.from_environment()
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_NoRedirect())

    def _url(self, path: str) -> str:
        """Build a fixed endpoint URL beneath the validated origin."""
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ControllerError("internal_error", "Invalid controller-owned OpenCode endpoint")
        return self.origin + path

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        http_error_code: str = "server_unavailable",
        deadline: float | None = None,
    ) -> Any:
        """Send one bounded JSON request and normalize transport failures safely."""
        payload = (
            None
            if body is None
            else json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
        )
        request = Request(self._url(path), data=payload, method=method)
        request.add_header("Accept", "application/json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        authorization = self.authentication.header()
        if authorization is not None:
            request.add_header("Authorization", authorization)
        timeout = float(self.timeout_seconds)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControllerError(
                    "invocation_timed_out", "OpenCode invocation exceeded its configured timeout"
                )
            timeout = min(timeout, remaining)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                if response.geturl() != self._url(path):
                    raise ControllerError(
                        "server_api_incompatible", "OpenCode server redirected a request"
                    )
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            try:
                if error.code == 401:
                    raise ControllerError(
                        "server_authentication_failed", "OpenCode server authentication failed"
                    ) from error
                if 300 <= error.code < 400:
                    raise ControllerError(
                        "server_api_incompatible", "OpenCode server redirected a request"
                    ) from error
                raise ControllerError(
                    http_error_code, f"OpenCode server returned HTTP {error.code}"
                ) from error
            finally:
                error.close()
        except (URLError, TimeoutError, socket.timeout, OSError) as error:
            if deadline is not None and time.monotonic() >= deadline:
                raise ControllerError(
                    "invocation_timed_out", "OpenCode invocation exceeded its configured timeout"
                ) from error
            raise ControllerError("server_unavailable", "OpenCode server is unavailable") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ControllerError("server_response_too_large", "OpenCode response exceeds 8 MiB")
        if not raw:
            return None
        try:
            return json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=self._no_duplicates,
                parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
            raise ControllerError(
                "malformed_server_response", "OpenCode server returned malformed JSON"
            ) from error

    @staticmethod
    def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """Reject duplicate response keys before response adaptation."""
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    @staticmethod
    def _object(value: Any, operation: str) -> dict[str, Any]:
        """Require a JSON object from one documented operation."""
        if not isinstance(value, dict):
            raise ControllerError(
                "malformed_server_response", f"OpenCode {operation} response is not an object"
            )
        return value

    def validate_server(self, request: ServerValidationRequest) -> ValidatedServer:
        """Validate health, default workspace, agents, and advertised configured models."""
        health = self._object(self._request("GET", "/global/health"), "health")
        version = health.get("version")
        if not isinstance(health.get("healthy"), bool) or not isinstance(version, str):
            raise ControllerError(
                "malformed_server_response", "OpenCode health response fields are invalid"
            )
        if health["healthy"] is not True:
            raise ControllerError("server_unhealthy", "OpenCode server health is not healthy")
        if not _VERSION.fullmatch(version):
            raise ControllerError(
                "unsupported_server_version", "OpenCode server version is not supported"
            )
        location = self._object(self._request("GET", "/path"), "path")
        try:
            directory_value = location["directory"]
            worktree_value = location["worktree"]
        except KeyError as error:
            raise ControllerError(
                "malformed_server_response", "OpenCode path response is invalid"
            ) from error
        directory = _server_absolute_path(directory_value, "directory")
        worktree = _server_absolute_path(worktree_value, "worktree")
        if directory != request.sprint_root or worktree != request.sprint_root:
            raise ControllerError(
                "wrong_server_workspace",
                "OpenCode server default workspace does not match sprint root",
            )
        agents_response = self._request("GET", "/agent")
        agents = (
            agents_response.get("agents") if isinstance(agents_response, dict) else agents_response
        )
        if not isinstance(agents, list):
            raise ControllerError("malformed_server_response", "OpenCode agent response is invalid")
        names: list[str] = []
        for agent in agents:
            name = agent.get("name") if isinstance(agent, dict) else None
            mode = agent.get("mode", "all") if isinstance(agent, dict) else None
            disabled = agent.get("disable", False) if isinstance(agent, dict) else None
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(mode, str)
                or mode not in {"all", "primary", "subagent"}
                or not isinstance(disabled, bool)
                or disabled
            ):
                raise ControllerError(
                    "malformed_server_response", "OpenCode agent record is invalid"
                )
            names.append(name)
        for configured in request.agents.values():
            if names.count(configured) != 1:
                raise ControllerError(
                    "configured_agent_unavailable", "A configured OpenCode agent is unavailable"
                )
        self._validate_models(request.models)
        return ValidatedServer(self.origin, version)

    def _validate_models(self, models: dict[str, str]) -> None:
        """Check all configured provider/model pairs against documented capabilities."""
        configured = self._object(
            self._request("GET", "/config/providers"), "provider configuration"
        )
        provider_state = self._object(self._request("GET", "/provider"), "provider")
        configured_records = configured.get("providers")
        all_records = provider_state.get("all")
        connected = provider_state.get("connected")
        if (
            not isinstance(configured_records, list)
            or not isinstance(all_records, list)
            or not isinstance(connected, list)
        ):
            raise ControllerError(
                "malformed_server_response", "OpenCode provider response is invalid"
            )
        if any(
            not isinstance(provider_id, str) or not provider_id for provider_id in connected
        ) or len(set(connected)) != len(connected):
            raise ControllerError(
                "malformed_server_response", "OpenCode provider configuration is invalid"
            )
        configured_providers = self._provider_records(configured_records)
        available = self._provider_records(all_records)
        connected_ids = set(connected)
        for value in models.values():
            provider_id, _, model_id = value.partition("/")
            configured_provider = configured_providers.get(provider_id)
            available_provider = available.get(provider_id)
            configured_models: dict[str, Any] = (
                configured_provider["models"] if configured_provider is not None else {}
            )
            available_models: dict[str, Any] = (
                available_provider["models"] if available_provider is not None else {}
            )
            if (
                configured_provider is None
                or available_provider is None
                or provider_id not in connected_ids
                or model_id not in configured_models
                or model_id not in available_models
            ):
                raise ControllerError(
                    "configured_model_unavailable", "A configured OpenCode model is unavailable"
                )

    @staticmethod
    def _provider_records(records: list[Any]) -> dict[str, dict[str, Any]]:
        """Validate documented provider records and index them by provider ID."""
        indexed: dict[str, dict[str, Any]] = {}
        for provider in records:
            if (
                not isinstance(provider, dict)
                or not isinstance(provider.get("id"), str)
                or not provider["id"]
                or not isinstance(provider.get("models"), dict)
                or any(
                    not isinstance(model_id, str) or not model_id or not isinstance(model, dict)
                    for model_id, model in provider["models"].items()
                )
                or provider["id"] in indexed
            ):
                raise ControllerError(
                    "malformed_server_response", "OpenCode provider record is invalid"
                )
            indexed[provider["id"]] = provider
        return indexed

    def existing_session_ids(self) -> set[str]:
        """Return the default-workspace session identifiers before a create request."""
        response = self._request("GET", "/session")
        sessions = response.get("sessions") if isinstance(response, dict) else response
        if not isinstance(sessions, list):
            raise ControllerError(
                "malformed_server_response", "OpenCode session response is invalid"
            )
        result: set[str] = set()
        for session in sessions:
            identifier = session.get("id") if isinstance(session, dict) else None
            if not _bounded_identifier(identifier):
                raise ControllerError(
                    "malformed_server_response", "OpenCode session identifier is invalid"
                )
            if identifier in result:
                raise ControllerError(
                    "malformed_server_response", "OpenCode session identifiers are duplicated"
                )
            result.add(identifier)
        return result

    def create_session(self, request: InvocationRequest) -> CreatedSession:
        """Create and fully validate a fresh wildcard-denied top-level session."""
        try:
            response = self._object(
                self._request(
                    "POST",
                    "/session",
                    {"title": request.title, "permission": list(_PERMISSIONS)},
                    http_error_code="session_creation_failed",
                ),
                "session creation",
            )
        except ControllerError as error:
            # A received HTTP response is a definitive rejection.  Only a
            # transport failure leaves POST /session's outcome unknowable.
            if error.code == "server_unavailable":
                raise ControllerError(
                    "session_creation_ambiguous", "Session creation outcome is unknown"
                ) from error
            raise
        identifier = response.get("id")
        title = response.get("title")
        parent = self._consistent_alias(response, "parentID", "parent_id")
        permissions = self._consistent_alias(response, "permission", "permissions")
        try:
            directory_value = response["directory"]
        except KeyError as error:
            raise ControllerError(
                "malformed_server_response", "Created session directory is invalid"
            ) from error
        directory = _server_absolute_path(directory_value, "created session directory")
        if (
            not _bounded_identifier(identifier)
            or title != request.title
            or parent is not None
            or directory != request.sprint_root
        ):
            raise ControllerError(
                "session_creation_failed", "Created session does not match the requested probe"
            )
        if permissions != list(_PERMISSIONS) and permissions != tuple(_PERMISSIONS):
            raise ControllerError(
                "session_creation_failed",
                "Created session does not enforce wildcard-deny permissions",
            )
        return CreatedSession(identifier, title, directory, _PERMISSIONS)

    @staticmethod
    def _consistent_alias(response: dict[str, Any], first: str, second: str) -> Any:
        """Return one API spelling while rejecting contradictory duplicate aliases."""
        if first in response and second in response and response[first] != response[second]:
            raise ControllerError(
                "session_creation_failed",
                "Created session contains inconsistent compatibility fields",
            )
        return response[first] if first in response else response.get(second)

    def submit_prompt(
        self, session: CreatedSession, request: InvocationRequest, *, deadline: float | None = None
    ) -> None:
        """Submit the fixed structured-output request after session durability is complete."""
        provider_id, _, model_id = request.model.partition("/")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "status", "summary", "checks", "blocking_reason"],
            "properties": {
                "schema_version": {"type": "integer", "const": 1},
                "status": {"enum": ["completed", "blocked", "failed"]},
                "summary": {"type": "string"},
                "checks": {"type": "array"},
                "blocking_reason": {"type": ["string", "null"]},
            },
        }
        try:
            response = self._request(
                "POST",
                f"/session/{quote(session.session_id, safe='')}/prompt_async",
                {
                    "agent": request.role,
                    "model": {"providerID": provider_id, "modelID": model_id},
                    "parts": [{"type": "text", "text": request.prompt}],
                    "format": {"type": "json_schema", "schema": schema},
                },
                http_error_code="prompt_submission_failed",
                deadline=deadline,
            )
        except ControllerError as error:
            if error.code == "server_unavailable":
                raise ControllerError(
                    "prompt_submission_failed", "Probe prompt submission outcome is unknown"
                ) from error
            raise
        if not (
            response is None
            or response == {}
            or (isinstance(response, dict) and response.get("accepted") is True)
        ):
            raise ControllerError(
                "prompt_submission_failed", "OpenCode did not accept the asynchronous probe prompt"
            )

    def _messages(
        self, session: CreatedSession, *, deadline: float | None = None
    ) -> list[dict[str, Any]]:
        """Fetch the documented message collection without interpreting prose as output."""
        response = self._request(
            "GET", f"/session/{quote(session.session_id, safe='')}/message", deadline=deadline
        )
        messages = response.get("messages") if isinstance(response, dict) else response
        if not isinstance(messages, list) or not all(
            isinstance(message, dict) for message in messages
        ):
            raise ControllerError(
                "malformed_server_response", "OpenCode message response is invalid"
            )
        return messages

    def observe(
        self, session: CreatedSession, *, deadline: float | None = None
    ) -> InvocationObservation:
        """Read one status map and message collection, detecting structured and tool evidence."""
        status_response = self._request("GET", "/session/status", deadline=deadline)
        statuses = (
            status_response.get("statuses", status_response)
            if isinstance(status_response, dict)
            else None
        )
        if not isinstance(statuses, dict):
            raise ControllerError(
                "malformed_server_response", "OpenCode status response is invalid"
            )
        if session.session_id not in statuses:
            status = None
        else:
            value = statuses[session.session_id]
            if not isinstance(value, dict) or not isinstance(value.get("type"), str):
                raise ControllerError(
                    "malformed_server_response", "OpenCode session status entry is invalid"
                )
            status = value["type"]
        if status is not None and not isinstance(status, str):
            raise ControllerError("malformed_server_response", "OpenCode session status is invalid")
        messages = self._messages(session, deadline=deadline)
        result: Any | None = None
        structured_error = False
        unexpected_tool = False
        terminal_assistant_error = False
        user_messages: list[tuple[int, str]] = []
        assistant_messages: list[tuple[int, dict[str, Any]]] = []
        assistant_results: list[tuple[int, dict[str, Any], Any]] = []
        for index, message in enumerate(messages):
            raw_info = message.get("info")
            info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
            role = message.get("role", info.get("role"))
            identifier = message.get("id", info.get("id"))
            if role == "user" and isinstance(identifier, str) and identifier:
                user_messages.append((index, identifier))
            if role == "assistant":
                assistant_messages.append((index, message))
            message_results: list[Any] = []
            if info.get("structured") is not None:
                message_results.append(info["structured"])
            for part in (
                message.get("parts", []) if isinstance(message.get("parts", []), list) else []
            ):
                if not isinstance(part, dict):
                    raise ControllerError(
                        "malformed_server_response", "OpenCode message part is invalid"
                    )
                kind = part.get("type")
                if kind in {"structured_output", "json_schema"}:
                    message_results.append(part.get("value", part.get("output")))
                if kind == "tool":
                    tool = part.get("tool", part.get("name"))
                    if tool == "StructuredOutputError":
                        structured_error = True
                    elif tool != "StructuredOutput":
                        unexpected_tool = True
                if kind == "permission":
                    unexpected_tool = True
            if message.get("structured_output") is not None:
                message_results.append(message["structured_output"])
            message_error = info.get("error", message.get("error"))
            if message_error == "StructuredOutputError" or (
                isinstance(message_error, dict)
                and message_error.get("name") == "StructuredOutputError"
            ):
                structured_error = True
            if len(message_results) > 1:
                raise ControllerError(
                    "malformed_server_response",
                    "OpenCode message has conflicting structured output",
                )
            if message_results:
                assistant_results.append((index, message, message_results[0]))
        terminal_assistant = False
        if len(user_messages) == 1:
            user_index, user_id = user_messages[0]
            terminal_candidates: list[tuple[int, dict[str, Any]]] = []
            for assistant_index, assistant in assistant_messages:
                raw_assistant_info = assistant.get("info")
                assistant_info: dict[str, Any] = (
                    raw_assistant_info if isinstance(raw_assistant_info, dict) else {}
                )
                parent = assistant.get(
                    "parentID",
                    assistant.get(
                        "parent_id",
                        assistant_info.get("parentID", assistant_info.get("parent_id")),
                    ),
                )
                if assistant_index > user_index and parent == user_id:
                    terminal_candidates.append((assistant_index, assistant))
            if len(terminal_candidates) == 1:
                terminal_assistant = True
                assistant_index, assistant = terminal_candidates[0]
                raw_assistant_info = assistant.get("info")
                assistant_info = raw_assistant_info if isinstance(raw_assistant_info, dict) else {}
                terminal_assistant_error = (
                    assistant_info.get("error", assistant.get("error")) is not None
                )
                terminal_results = [
                    value
                    for index, _message, value in assistant_results
                    if index == assistant_index
                ]
                if len(terminal_results) == 1:
                    result = terminal_results[0]
        if result is None and assistant_results:
            result = assistant_results[-1][2]
        return InvocationObservation(
            status,
            messages,
            result,
            structured_error,
            unexpected_tool,
            terminal_assistant,
            terminal_assistant_error,
        )

    def abort(self, session: CreatedSession) -> AbortObservation:
        """Issue one documented abort request; its response is acknowledgement only."""
        response = self._request("POST", f"/session/{quote(session.session_id, safe='')}/abort", {})
        if not isinstance(response, bool):
            raise ControllerError(
                "malformed_server_response",
                "OpenCode abort response is not a boolean acknowledgement",
            )
        return AbortObservation(response)

    def transcript(
        self, session: CreatedSession, *, deadline: float | None = None
    ) -> TranscriptCapture:
        """Return the raw message array for bounded controller-side persistence."""
        return TranscriptCapture(self._messages(session, deadline=deadline))
