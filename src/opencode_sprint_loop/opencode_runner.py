"""Standard-library HTTP adapter for the documented Sprint 2 OpenCode API."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
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

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10
_VERSION = re.compile(r"^(1)\.(17)\.(\d+)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PERMISSIONS = ({"permission": "*", "pattern": "*", "action": "deny"},)


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
        port = parsed.port
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
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
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
            directory = Path(location["directory"]).resolve()
            worktree = Path(location["worktree"]).resolve()
        except (KeyError, TypeError, OSError, RuntimeError) as error:
            raise ControllerError(
                "malformed_server_response", "OpenCode path response is invalid"
            ) from error
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
            if (
                not isinstance(name, str)
                or not name
                or agent.get("mode", "all") == "disabled"
                or agent.get("disable") is True
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
        configured = self._request("GET", "/config/providers")
        providers_response = self._request("GET", "/provider")
        providers = (
            providers_response.get("providers")
            if isinstance(providers_response, dict)
            else providers_response
        )
        configured_names = (
            configured.get("providers", configured) if isinstance(configured, dict) else configured
        )
        if not isinstance(providers, list) or not isinstance(configured_names, (list, dict)):
            raise ControllerError(
                "malformed_server_response", "OpenCode provider response is invalid"
            )
        available: dict[str, Any] = {}
        for provider in providers:
            if not isinstance(provider, dict) or not isinstance(provider.get("id"), str):
                raise ControllerError(
                    "malformed_server_response", "OpenCode provider record is invalid"
                )
            available[provider["id"]] = provider
        configured_ids = set(
            configured_names if isinstance(configured_names, list) else configured_names
        )
        for value in models.values():
            provider_id, _, model_id = value.partition("/")
            provider = available.get(provider_id)
            advertised = provider.get("models") if isinstance(provider, dict) else None
            has_model = (
                model_id in advertised
                if isinstance(advertised, dict)
                else model_id in advertised
                if isinstance(advertised, list)
                else False
            )
            if (
                provider_id not in configured_ids
                or provider is None
                or provider.get("connected") is False
                or not has_model
            ):
                raise ControllerError(
                    "configured_model_unavailable", "A configured OpenCode model is unavailable"
                )

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
            if not isinstance(identifier, str) or not identifier or len(identifier.encode()) > 1024:
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
        parent = response.get("parentID", response.get("parent_id"))
        permissions = response.get("permission", response.get("permissions"))
        try:
            directory = Path(response["directory"]).resolve()
        except (KeyError, TypeError, OSError, RuntimeError) as error:
            raise ControllerError(
                "malformed_server_response", "Created session directory is invalid"
            ) from error
        if (
            not isinstance(identifier, str)
            or not identifier
            or len(identifier.encode()) > 1024
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

    def submit_prompt(self, session: CreatedSession, request: InvocationRequest) -> None:
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
                    "retryCount": 2,
                },
                http_error_code="prompt_submission_failed",
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

    def _messages(self, session: CreatedSession) -> list[dict[str, Any]]:
        """Fetch the documented message collection without interpreting prose as output."""
        response = self._request("GET", f"/session/{quote(session.session_id, safe='')}/message")
        messages = response.get("messages") if isinstance(response, dict) else response
        if not isinstance(messages, list) or not all(
            isinstance(message, dict) for message in messages
        ):
            raise ControllerError(
                "malformed_server_response", "OpenCode message response is invalid"
            )
        return messages

    def observe(self, session: CreatedSession) -> InvocationObservation:
        """Read one status map and message collection, detecting structured and tool evidence."""
        status_response = self._request("GET", "/session/status")
        statuses = (
            status_response.get("statuses", status_response)
            if isinstance(status_response, dict)
            else None
        )
        if not isinstance(statuses, dict):
            raise ControllerError(
                "malformed_server_response", "OpenCode status response is invalid"
            )
        value = statuses.get(session.session_id)
        status = value.get("status") if isinstance(value, dict) else value
        if status is not None and not isinstance(status, str):
            raise ControllerError("malformed_server_response", "OpenCode session status is invalid")
        messages = self._messages(session)
        result: Any | None = None
        structured_error = False
        unexpected_tool = False
        terminal_assistant_error = False
        user_messages: list[tuple[int, str]] = []
        assistant_results: list[tuple[int, dict[str, Any], Any]] = []
        for index, message in enumerate(messages):
            raw_info = message.get("info")
            info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
            role = message.get("role", info.get("role"))
            identifier = message.get("id", info.get("id"))
            if role == "user" and isinstance(identifier, str) and identifier:
                user_messages.append((index, identifier))
            message_result: Any | None = None
            for part in (
                message.get("parts", []) if isinstance(message.get("parts", []), list) else []
            ):
                if not isinstance(part, dict):
                    raise ControllerError(
                        "malformed_server_response", "OpenCode message part is invalid"
                    )
                kind = part.get("type")
                if kind in {"structured_output", "json_schema"}:
                    message_result = part.get("value", part.get("output"))
                if kind == "tool":
                    tool = part.get("tool", part.get("name"))
                    if tool == "StructuredOutputError":
                        structured_error = True
                    elif tool != "StructuredOutput":
                        unexpected_tool = True
                if kind == "permission":
                    unexpected_tool = True
            if message.get("structured_output") is not None:
                message_result = message["structured_output"]
            if message.get("error") == "StructuredOutputError":
                structured_error = True
            if message_result is not None:
                assistant_results.append((index, message, message_result))
        terminal_assistant = False
        if len(user_messages) == 1 and len(assistant_results) == 1:
            user_index, user_id = user_messages[0]
            assistant_index, assistant, result = assistant_results[0]
            raw_assistant_info = assistant.get("info")
            assistant_info: dict[str, Any] = (
                raw_assistant_info if isinstance(raw_assistant_info, dict) else {}
            )
            role = assistant.get("role", assistant_info.get("role"))
            parent = assistant.get(
                "parentID",
                assistant.get(
                    "parent_id", assistant_info.get("parentID", assistant_info.get("parent_id"))
                ),
            )
            terminal_assistant = (
                role == "assistant" and assistant_index > user_index and parent == user_id
            )
            terminal_assistant_error = terminal_assistant and assistant.get("error") is not None
        elif assistant_results:
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
        return AbortObservation(
            response is True
            or response is None
            or response == {}
            or (isinstance(response, dict) and response.get("acknowledged", True) is True)
        )

    def transcript(self, session: CreatedSession) -> TranscriptCapture:
        """Return the raw message array for bounded controller-side persistence."""
        return TranscriptCapture(self._messages(session))
