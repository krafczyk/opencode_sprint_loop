"""Offline Sprint 2 URL, runner, artifact, and probe-flow tests."""

from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
import os
import signal
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from opencode_sprint_loop.agent_runner import (
    CreatedSession,
    FakeAgentRunner,
    InvocationObservation,
    ServerValidationRequest,
    ValidatedServer,
)
from opencode_sprint_loop.errors import ControllerError
from opencode_sprint_loop.events import validate_event_history
from opencode_sprint_loop.invocations import (
    allocate_paths,
    new_metadata,
    probe_prompt,
    transcript_wrapper,
    validate_transcript_messages,
    validate_invocation_records,
    validate_metadata,
    validate_prompt,
    validate_result,
    write_metadata,
    write_prompt,
    write_result,
    write_transcript,
)
from opencode_sprint_loop.opencode_runner import (
    HTTPAuthentication,
    OpenCodeServerRunner,
    parse_server_url,
)
from opencode_sprint_loop.security import contains_credential, redact_diagnostic

_PROBE_PERMISSIONS = [
    {"permission": "*", "pattern": "*", "action": "deny"},
    {"permission": "StructuredOutput", "pattern": "*", "action": "allow"},
]


def _last_matching_permission_action(rules: list[dict[str, str]], permission: str) -> str | None:
    """Model OpenCode 1.17.18's ordered last-match permission selection."""
    selected: str | None = None
    for rule in rules:
        if rule["pattern"] == "*" and rule["permission"] in {"*", permission}:
            selected = rule["action"]
    return selected


class _Handler(BaseHTTPRequestHandler):
    """Minimal local documented-API fake for exercising the real HTTP adapter."""

    root = "/tmp"
    mode = "complete"
    abort_requests = 0
    last_session: dict[str, object] | None = None
    last_prompt: dict[str, object] | None = None
    request_paths: list[str] = []
    preflight_started = threading.Event()

    def log_message(self, format: str, *args: object) -> None:
        """Keep default tests silent."""
        del format, args

    def _json(self, value: object) -> None:
        encoded = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        """Serve the bounded endpoints used during preflight and observation."""
        type(self).request_paths.append(self.path)
        if self.path == "/global/health":
            if self.mode == "slow_preflight":
                type(self).preflight_started.set()
                time.sleep(0.5)
            self._json({"healthy": True, "version": "1.17.18"})
        elif self.path == "/path":
            self._json({"directory": self.root, "worktree": self.root})
        elif self.path == "/agent":
            self._json([{"name": "builder"}, {"name": "auditor"}, {"name": "ci-fixer"}])
        elif self.path == "/config/providers":
            self._json({"providers": [{"id": "test", "models": {"medium": {}, "strong": {}}}]})
        elif self.path == "/provider":
            self._json(
                {
                    "all": [{"id": "test", "models": {"medium": {}, "strong": {}}}],
                    "connected": ["test"],
                }
            )
        elif self.path == "/session":
            self._json([{"id": "\ud800"}] if self.mode == "surrogate_session_list" else [])
        elif self.path == "/session/status":
            status = "busy" if self.mode == "busy" else "retry" if self.mode == "retry" else "idle"
            if self.mode == "malformed_status_scalar":
                self._json({"ses_local": status})
            elif self.mode == "malformed_status_missing_type":
                self._json({"ses_local": {"status": status}})
            else:
                self._json({"ses_local": {"type": status}})
        elif self.path == "/session/ses_local/message":
            if self.mode == "busy":
                self._json([])
            else:
                result = {
                    "schema_version": 1,
                    "status": "completed",
                    "summary": "ok",
                    "checks": [],
                    "blocking_reason": None,
                }
                self._json(
                    [
                        {
                            "info": {"id": "msg_user", "role": "user"},
                            "parts": [
                                {
                                    "type": "text",
                                    "text": (
                                        self.last_prompt["parts"][0]["text"]
                                        if self.last_prompt is not None
                                        else "probe\n"
                                    ),
                                }
                            ],
                        },
                        {
                            "info": {
                                "id": "msg_assistant",
                                "role": "assistant",
                                "sessionID": "ses_local",
                                "parentID": "msg_user",
                                "agent": (
                                    self.last_prompt["agent"]
                                    if self.last_prompt is not None
                                    else "auditor"
                                ),
                                "providerID": (
                                    self.last_prompt["model"]["providerID"]
                                    if self.last_prompt is not None
                                    else "test"
                                ),
                                "modelID": (
                                    self.last_prompt["model"]["modelID"]
                                    if self.last_prompt is not None
                                    else "strong"
                                ),
                                **(
                                    {"error": {"name": "StructuredOutputError"}}
                                    if self.mode == "structured_output_error"
                                    else {}
                                ),
                                **(
                                    {}
                                    if self.mode == "structured_output_error"
                                    else {"structured": result}
                                ),
                            },
                            "parts": [
                                *(
                                    [
                                        {
                                            "type": "tool",
                                            "tool": "shell",
                                            "sessionID": "ses_local",
                                            "messageID": "msg_assistant",
                                        }
                                    ]
                                    if self.mode == "unexpected_tool"
                                    else []
                                )
                            ],
                        },
                    ]
                )
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        """Serve fresh-session, prompt, and abort calls."""
        type(self).request_paths.append(self.path)
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/session":
            if self.mode == "create_reject":
                self.send_error(500)
                return
            request = json.loads(raw.decode())
            if request.get("permission") != _PROBE_PERMISSIONS:
                self.send_error(422)
                return
            type(self).last_session = request
            self._json(
                {
                    "id": "\ud800" if self.mode == "surrogate_create" else "ses_local",
                    "title": request["title"],
                    "directory": self.root,
                    "parentID": None,
                    "permission": request["permission"],
                }
            )
        elif self.path.endswith("/abort"):
            type(self).abort_requests += 1
            type(self).mode = "complete"
            self._json(True)
        elif self.path.endswith("/message"):
            type(self).last_prompt = json.loads(raw.decode())
            while self.mode == "busy":
                time.sleep(0.01)
            result = {
                "schema_version": 1,
                "status": "completed",
                "summary": "ok",
                "checks": [],
                "blocking_reason": None,
            }
            self._json(
                {
                    "info": {
                        "id": "msg_assistant",
                        "role": "assistant",
                        "sessionID": "ses_local",
                        "parentID": "msg_user",
                        "agent": self.last_prompt["agent"],
                        "providerID": self.last_prompt["model"]["providerID"],
                        "modelID": self.last_prompt["model"]["modelID"],
                        **(
                            {"error": {"name": "StructuredOutputError"}}
                            if self.mode == "structured_output_error"
                            else {}
                        ),
                        **(
                            {}
                            if self.mode == "structured_output_error"
                            else {"structured_output": result}
                        ),
                    },
                    "parts": [
                        *(
                            [
                                {
                                    "type": "tool",
                                    "tool": "shell",
                                    "sessionID": "ses_local",
                                    "messageID": "msg_assistant",
                                }
                            ]
                            if self.mode == "unexpected_tool"
                            else []
                        )
                    ],
                }
            )
        else:
            self.send_error(404)


class _HTTPResponse:
    """Small opener response fake for transport and response-bound tests."""

    def __init__(self, url: str, payload: bytes) -> None:
        self.url = url
        self.payload = payload

    def __enter__(self) -> "_HTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def geturl(self) -> str:
        return self.url

    def read(self, amount: int) -> bytes:
        return self.payload[:amount]


class OpenCodeExecutionTests(unittest.TestCase):
    """Exercise the public Sprint 2 execution boundary without external services."""

    def test_url_rules_and_in_memory_authentication(self) -> None:
        """Origins normalize while unsafe URL/auth forms fail without disclosure."""
        self.assertEqual(parse_server_url("HTTP://Example.invalid:80/"), "http://example.invalid")
        self.assertEqual(
            parse_server_url("https://example.invalid:444"), "https://example.invalid:444"
        )
        self.assertEqual(parse_server_url("http://[::1]:80"), "http://[::1]")
        for value in (
            "",
            "relative",
            "ssh://host",
            "http:///missing-host",
            "http://user:pass@host",
            "http://host/a",
            "http://host/%zz",
            "http://host?q=x",
            "http://host#x",
            "http://host\n",
            "http://host:%zz",
            "http://host:",
            "http://host:65536",
            "http://[::1]:",
            "http://bad_host",
            "http://host:0",
            "http://host..invalid",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ControllerError) as context:
                    parse_server_url(value)
                self.assertEqual(context.exception.code, "invalid_server_url")
        with patch.dict("os.environ", {"OPENCODE_SERVER_USERNAME": "name"}, clear=True):
            with self.assertRaises(ControllerError) as context:
                HTTPAuthentication.from_environment()
            self.assertEqual(context.exception.code, "invalid_server_authentication")
        with patch.dict("os.environ", {"OPENCODE_SERVER_PASSWORD": "synthetic-pass"}, clear=True):
            authentication = HTTPAuthentication.from_environment()
            self.assertEqual(authentication.username, "opencode")
            self.assertTrue(authentication.header().startswith("Basic "))  # type: ignore[union-attr]
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(HTTPAuthentication.from_environment().header())

    def test_metadata_schema_and_transcript_bounds_fail_closed(self) -> None:
        """Nested metadata invariants are exact and transcript truncation stays in bounds."""
        metadata = new_metadata(
            "00000000-0000-4000-8000-000000000000",
            "0001-auditor",
            1,
            "test/strong",
            "1.17.18",
            "managed",
        )
        validate_metadata(metadata)
        invalid_records = []
        invalid = deepcopy(metadata)
        invalid["result"]["available"] = True
        invalid_records.append(invalid)
        invalid = deepcopy(metadata)
        invalid["transcript"]["extra"] = False
        invalid_records.append(invalid)
        invalid = deepcopy(metadata)
        invalid["invocation_id"] = "0002-auditor"
        invalid_records.append(invalid)
        invalid = deepcopy(metadata)
        invalid["created_at"] = "2026-02-30T00:00:00Z"
        invalid_records.append(invalid)
        for invalid in invalid_records:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ControllerError) as context:
                    validate_metadata(invalid)
                self.assertEqual(context.exception.code, "invocation_record_failed")

        with patch("opencode_sprint_loop.invocations.MAX_TRANSCRIPT_BYTES", 1024):
            wrapper = transcript_wrapper(
                "ses_large",
                [
                    {
                        "id": f"message-{index}",
                        "role": "user",
                        "parts": [{"type": "text", "text": "x" * 500}],
                    }
                    for index in range(3)
                ],
            )
            self.assertTrue(wrapper["truncated"])
            self.assertTrue(wrapper["content"].endswith("\n[TRUNCATED]"))
            with TemporaryDirectory() as temporary:
                paths = allocate_paths(Path(temporary), "test", 1, 1, "auditor")
                write_transcript(paths, wrapper)
                self.assertLessEqual(paths.transcript.stat().st_size, 1024)
                with self.assertRaises(ControllerError):
                    write_transcript(paths, wrapper)

    def test_real_adapter_uses_local_fake_server(self) -> None:
        """Capture the complete documented direct-adapter session and message requests."""
        with TemporaryDirectory() as temporary:
            _Handler.root = str(Path(temporary).resolve())
            _Handler.mode = "complete"
            _Handler.abort_requests = 0
            _Handler.last_session = None
            _Handler.last_prompt = None
            _Handler.request_paths = []
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                runner = OpenCodeServerRunner(f"http://127.0.0.1:{server.server_port}")
                from opencode_sprint_loop.agent_runner import (
                    InvocationRequest,
                    ServerValidationRequest,
                )

                root = Path(temporary).resolve()
                validated = runner.validate_server(
                    ServerValidationRequest(
                        root,
                        {"builder": "builder", "auditor": "auditor", "ci_fixer": "ci-fixer"},
                        {
                            "builder": "test/medium",
                            "auditor": "test/strong",
                            "ci_fixer": "test/medium",
                        },
                    )
                )
                self.assertEqual(validated.version, "1.17.18")
                request = InvocationRequest(
                    "0001-auditor",
                    1,
                    "auditor",
                    "test/strong",
                    "[test/1] auditor 0001 execution probe",
                    "probe\n",
                    root,
                )
                with patch(
                    "opencode_sprint_loop.opencode_runner._PERMISSIONS",
                    (dict(_PROBE_PERMISSIONS[0]),),
                ):
                    with self.assertRaises(ControllerError) as context:
                        runner.create_session(request)
                self.assertEqual(context.exception.code, "session_creation_failed")
                _Handler.request_paths = []
                self.assertEqual(runner.existing_session_ids(), set())
                session = runner.create_session(request)
                observation = runner.execute_prompt(session, request)
                schema = {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "schema_version",
                        "status",
                        "summary",
                        "checks",
                        "blocking_reason",
                    ],
                    "properties": {
                        "schema_version": {"type": "integer", "const": 1},
                        "status": {"enum": ["completed", "blocked", "failed"]},
                        "summary": {"type": "string"},
                        "checks": {"type": "array"},
                        "blocking_reason": {"type": ["string", "null"]},
                    },
                }
                self.assertEqual(
                    _Handler.last_session,
                    {"title": request.title, "permission": _PROBE_PERMISSIONS},
                )
                self.assertEqual(
                    _Handler.last_prompt,
                    {
                        "agent": "auditor",
                        "model": {"providerID": "test", "modelID": "strong"},
                        "parts": [{"type": "text", "text": "probe\n"}],
                        "format": {"type": "json_schema", "schema": schema},
                    },
                )
                self.assertEqual(
                    _Handler.request_paths,
                    [
                        "/session",
                        "/session",
                        "/session/ses_local/message",
                    ],
                )
                self.assertNotIn("retryCount", _Handler.last_prompt)  # type: ignore[operator]
                self.assertNotIn("retryCount", _Handler.last_prompt["format"])  # type: ignore[index]
                self.assertEqual(observation.structured_result["status"], "completed")
                self.assertTrue(observation.terminal_assistant)
                self.assertFalse(observation.terminal_assistant_error)
                self.assertEqual(
                    transcript_wrapper(
                        session.session_id,
                        observation.messages,
                        expected_result=observation.structured_result,
                    )["session_id"],
                    "ses_local",
                )
                validate_transcript_messages(
                    observation.messages,
                    expected_result=observation.structured_result,
                    expected_prompt=request.prompt,
                    expected_role=request.role,
                    expected_model=request.model,
                    expected_session_id=session.session_id,
                )
                for mode in ("malformed_status_scalar", "malformed_status_missing_type"):
                    _Handler.mode = mode
                    with self.subTest(mode=mode), self.assertRaises(ControllerError) as context:
                        runner.observe_status(session)
                    self.assertEqual(context.exception.code, "malformed_server_response")
                _Handler.mode = "structured_output_error"
                errored_observation = runner.execute_prompt(session, request)
                self.assertTrue(errored_observation.structured_error)
                self.assertTrue(errored_observation.terminal_assistant)
                self.assertTrue(errored_observation.terminal_assistant_error)
                _Handler.mode = "unexpected_tool"
                self.assertTrue(runner.execute_prompt(session, request).unexpected_tool)
                self.assertTrue(runner.abort(session).acknowledged)
                self.assertEqual(_Handler.abort_requests, 1)
                _Handler.mode = "surrogate_session_list"
                with self.assertRaises(ControllerError) as context:
                    runner.existing_session_ids()
                self.assertEqual(context.exception.code, "malformed_server_response")
                _Handler.mode = "surrogate_create"
                with self.assertRaises(ControllerError) as context:
                    runner.create_session(request)
                self.assertEqual(context.exception.code, "session_creation_failed")
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    @unittest.skipUnless(
        os.environ.get("SPRINT_LOOP_REAL_SERVER_URL")
        and os.environ.get("SPRINT_LOOP_REAL_SPRINT_ROOT"),
        "real OpenCode preflight is opt-in",
    )
    def test_opt_in_real_server_preflight(self) -> None:
        """Validate an explicitly supplied real server without creating a session."""
        from opencode_sprint_loop.config import load_config

        root_value = os.environ["SPRINT_LOOP_REAL_SPRINT_ROOT"]
        root = Path(root_value).resolve(strict=True)
        config = load_config(root)
        runner = OpenCodeServerRunner(os.environ["SPRINT_LOOP_REAL_SERVER_URL"])
        validated = runner.validate_server(
            ServerValidationRequest(root, dict(config.agents), dict(config.models))
        )
        self.assertEqual(validated.url, parse_server_url(os.environ["SPRINT_LOOP_REAL_SERVER_URL"]))
        self.assertRegex(validated.version, r"^1\.17\.\d+$")

    def test_server_preflight_capability_failures_are_specific(self) -> None:
        """Health, workspace, agent, and model validation fail in their owned categories."""
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            request = ServerValidationRequest(
                root,
                {"builder": "builder", "auditor": "auditor", "ci_fixer": "ci-fixer"},
                {
                    "builder": "test/medium",
                    "auditor": "test/strong",
                    "ci_fixer": "test/medium",
                },
            )
            valid = {
                "/global/health": {"healthy": True, "version": "1.17.18"},
                "/path": {"directory": str(root), "worktree": str(root)},
                "/agent": [
                    {"name": "builder"},
                    {"name": "auditor"},
                    {"name": "ci-fixer"},
                ],
                "/config/providers": {
                    "providers": [{"id": "test", "models": {"medium": {}, "strong": {}}}]
                },
                "/provider": {
                    "all": [{"id": "test", "models": {"medium": {}, "strong": {}}}],
                    "connected": ["test"],
                },
            }
            cases = (
                ("/global/health", {"healthy": False, "version": "1.17.18"}, "server_unhealthy"),
                (
                    "/global/health",
                    {"healthy": "yes", "version": "1.17.18"},
                    "malformed_server_response",
                ),
                (
                    "/global/health",
                    {"healthy": True, "version": "1.18.0"},
                    "unsupported_server_version",
                ),
                (
                    "/global/health",
                    {"healthy": True, "version": "1.16.99"},
                    "unsupported_server_version",
                ),
                (
                    "/global/health",
                    {"healthy": True, "version": "1.17.18-beta.1"},
                    "unsupported_server_version",
                ),
                (
                    "/global/health",
                    {"healthy": True, "version": "1.17.018"},
                    "unsupported_server_version",
                ),
                (
                    "/global/health",
                    {"healthy": True, "version": "not-a-version"},
                    "unsupported_server_version",
                ),
                (
                    "/path",
                    {"directory": str(root.parent), "worktree": str(root)},
                    "wrong_server_workspace",
                ),
                ("/agent", [{"name": "auditor"}], "configured_agent_unavailable"),
                (
                    "/agent",
                    [{"name": "builder"}, {"name": "auditor"}, {"name": "auditor"}],
                    "configured_agent_unavailable",
                ),
                ("/agent", [{"name": 1}], "malformed_server_response"),
                ("/agent", [{"name": "builder", "mode": []}], "malformed_server_response"),
                ("/agent", [{"name": "builder", "disable": 1}], "malformed_server_response"),
                (
                    "/config/providers",
                    {
                        "providers": [
                            {"id": "test", "models": {"strong": {}}},
                            {"id": "test", "models": {"medium": {}}},
                        ]
                    },
                    "malformed_server_response",
                ),
                (
                    "/config/providers",
                    {"providers": [1]},
                    "malformed_server_response",
                ),
                ("/provider", {"all": [], "connected": []}, "configured_model_unavailable"),
                (
                    "/provider",
                    {"all": [{"id": "test", "models": {"medium": {}, "strong": {}}}]},
                    "malformed_server_response",
                ),
                (
                    "/provider",
                    {
                        "all": [{"id": "test", "models": {"medium": {}, "strong": {}}}],
                        "connected": "test",
                    },
                    "malformed_server_response",
                ),
                (
                    "/provider",
                    {
                        "all": [{"id": "test", "models": ["medium", "strong"]}],
                        "connected": ["test"],
                    },
                    "malformed_server_response",
                ),
                (
                    "/provider",
                    {"all": [{"id": "test", "models": {"strong": None}}], "connected": ["test"]},
                    "malformed_server_response",
                ),
                (
                    "/provider",
                    {
                        "all": [
                            {"id": "test", "models": {"strong": {}}},
                            {"id": "test", "models": {"medium": {}}},
                        ],
                        "connected": ["test"],
                    },
                    "malformed_server_response",
                ),
                (
                    "/provider",
                    {
                        "all": [{"id": "test", "models": {"medium": {}, "strong": {}}}],
                        "connected": [],
                    },
                    "configured_model_unavailable",
                ),
            )
            for endpoint, response, code in cases:
                with self.subTest(endpoint=endpoint, code=code):
                    responses = {**valid, endpoint: response}
                    runner = OpenCodeServerRunner("http://127.0.0.1:4096")
                    with patch.object(
                        runner,
                        "_request",
                        side_effect=lambda _method, path, *args, **kwargs: responses[path],
                    ):
                        with self.assertRaises(ControllerError) as context:
                            runner.validate_server(request)
                    self.assertEqual(context.exception.code, code)

    def test_server_preflight_failure_has_no_runtime_or_session_mutation(self) -> None:
        """A fake validation failure occurs before ownership, records, or session creation."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            validation_error=ControllerError("server_unhealthy", "Synthetic unhealthy server"),
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "server_unhealthy")
        self.assertEqual(fake.created, [])
        self.assertFalse((root / "info").exists())
        self.assertFalse((root / "invocations").exists())

    def test_result_and_artifact_sanitization(self) -> None:
        """Only exact safe results persist and transcript credentials redact first."""
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        self.assertEqual(validate_result(completed), completed)
        for invalid in (
            {**completed, "unknown": True},
            {**completed, "checks": [{"command": "x"}]},
            {**completed, "summary": "token=synthetic-secret"},
            {**completed, "status": []},
            {**completed, "status": {}},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ControllerError):
                    validate_result(invalid)
        wrapper = transcript_wrapper(
            "ses_test",
            [
                {
                    "id": "message-1",
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "text": "Authorization: Bearer synthetic-secret",
                        }
                    ],
                    "token": "synthetic-secret",
                }
            ],
        )
        self.assertIn("[REDACTED]", wrapper["content"])
        token_key = "ghp_" + "A" * 36
        uri_key = "https://example.invalid/path?opaque=synthetic-secret"
        for key in (token_key, uri_key):
            with self.subTest(key=key.split(":", 1)[0]):
                wrapper = transcript_wrapper("ses_test", [{key: "safe"}])
                self.assertNotIn(key, wrapper["content"])
                self.assertIn("[REDACTED]", wrapper["content"])
        with self.assertRaises(ControllerError) as context:
            transcript_wrapper(
                "ses_test",
                [{token_key: "safe", "[REDACTED]": "safe"}],
            )
        self.assertEqual(context.exception.code, "transcript_capture_failed")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = allocate_paths(root, "test", 1, 1, "auditor")
            metadata = new_metadata(
                "00000000-0000-4000-8000-000000000000",
                "0001-auditor",
                1,
                "test/strong",
                "1.17.18",
                "managed",
            )
            write_metadata(paths, metadata)
            write_prompt(paths, probe_prompt("test", 1, "0001-auditor"))
            self.assertEqual((paths.prompt.stat().st_mode & 0o777), 0o600)

    def test_current_credential_forms_are_rejected_or_redacted(self) -> None:
        """Synthetic provider tokens and URI values cannot enter durable records."""
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        values = (
            "sk-proj-" + "A" * 24,
            "sk-svcacct-" + "B" * 24,
            "sk-ant-api03-" + "C" * 24,
            "sk-ant-oat01-" + "D" * 24,
            "sk-or-v1-" + "E" * 24,
            "AIza" + "F" * 30,
            *(
                prefix + "G" * 24
                for prefix in (
                    "glpat-",
                    "glcbt-",
                    "glptt-",
                    "glrt-",
                    "glimt-",
                    "glsoat-",
                    "gldt-",
                    "glrtr-",
                    "glft-",
                    "glagent-",
                    "glwt-",
                    "glffct-",
                    "gloas-",
                )
            ),
            "hf_" + "H" * 24,
            "xapp-" + "I" * 24,
            "gho_" + "J" * 36,
            "ghs_12345_" + "J" * 40 + "." + "K" * 40 + "." + "L" * 40,
            "ASIA" + "K" * 16,
            "postgresql://user:synthetic-secret@example.invalid/database",
            "ssh://user:synthetic-secret@example.invalid/repository",
            "https://example.invalid/path?opaque=synthetic-secret",
            "https://example.invalid/path#opaque-fragment",
        )
        for value in values:
            with self.subTest(value=value.split(":", 1)[0]):
                self.assertTrue(contains_credential(value))
                with self.assertRaises(ControllerError) as context:
                    validate_result({**completed, "summary": value})
                self.assertEqual(context.exception.code, "invalid_agent_result")
                wrapper = transcript_wrapper(
                    "ses_test",
                    [
                        {
                            "id": "message-1",
                            "role": "user",
                            "parts": [{"type": "text", "text": value}],
                        }
                    ],
                )
                self.assertNotIn(value, wrapper["content"])
                self.assertIn("[REDACTED]", wrapper["content"])
                self.assertNotIn(value, redact_diagnostic(f"external diagnostic: {value}"))

    def test_credential_scan_handles_bounded_non_uri_text(self) -> None:
        """A bounded artifact-sized benign string cannot trigger URI-pattern backtracking."""
        self.assertFalse(contains_credential("x" * (1024 * 1024)))

    def test_adapter_distinguishes_definitive_create_rejection(self) -> None:
        """An HTTP rejection is definitive, unlike an unavailable create transport."""
        from opencode_sprint_loop.agent_runner import InvocationRequest

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _Handler.root = str(root)
            _Handler.mode = "create_reject"
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                runner = OpenCodeServerRunner(f"http://127.0.0.1:{server.server_port}")
                request = InvocationRequest(
                    "0001-auditor", 1, "auditor", "test/strong", "title", "probe\n", root
                )
                with self.assertRaises(ControllerError) as context:
                    runner.create_session(request)
                self.assertEqual(context.exception.code, "session_creation_failed")
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
                _Handler.mode = "complete"

    def test_ordered_permissions_allow_only_structured_output_under_last_match_semantics(
        self,
    ) -> None:
        """The 1.17.18 exception follows deny so only StructuredOutput can match allow."""
        self.assertEqual(
            _last_matching_permission_action(
                [{"permission": "*", "pattern": "*", "action": "deny"}], "StructuredOutput"
            ),
            "deny",
        )
        self.assertEqual(
            _PROBE_PERMISSIONS,
            [
                {"permission": "*", "pattern": "*", "action": "deny"},
                {"permission": "StructuredOutput", "pattern": "*", "action": "allow"},
            ],
        )
        self.assertEqual(
            _last_matching_permission_action(_PROBE_PERMISSIONS, "StructuredOutput"), "allow"
        )
        for forbidden in ("shell", "repository", "web", "task", "mcp", "external"):
            with self.subTest(permission=forbidden):
                self.assertEqual(
                    _last_matching_permission_action(_PROBE_PERMISSIONS, forbidden), "deny"
                )

    def test_direct_and_full_probe_use_the_same_complete_request_contract(self) -> None:
        """The adapter and controller send identical captured Sprint 2 request bodies/routes."""
        from opencode_sprint_loop.agent_runner import InvocationRequest
        from opencode_sprint_loop.cli import _run
        from opencode_sprint_loop.invocations import probe_title
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        _Handler.root = str(root)
        _Handler.mode = "complete"
        _Handler.last_session = None
        _Handler.last_prompt = None
        _Handler.request_paths = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            request = InvocationRequest(
                "0001-auditor",
                1,
                "auditor",
                "test/strong",
                probe_title("foundation", 1, 1),
                probe_prompt("foundation", 1, "0001-auditor"),
                root,
            )
            direct = OpenCodeServerRunner(origin)
            direct.validate_server(
                ServerValidationRequest(
                    root,
                    {"builder": "builder", "auditor": "auditor", "ci_fixer": "ci-fixer"},
                    {
                        "builder": "test/medium",
                        "auditor": "test/strong",
                        "ci_fixer": "test/medium",
                    },
                )
            )
            direct.existing_session_ids()
            session = direct.create_session(request)
            direct.execute_prompt(session, request)
            direct_capture = (
                deepcopy(_Handler.last_session),
                deepcopy(_Handler.last_prompt),
                list(_Handler.request_paths),
            )
            _Handler.last_session = None
            _Handler.last_prompt = None
            _Handler.request_paths = []

            self.assertEqual(_run(str(root), origin), 4)
            full_capture = (
                deepcopy(_Handler.last_session),
                deepcopy(_Handler.last_prompt),
                list(_Handler.request_paths),
            )
            self.assertEqual(full_capture, direct_capture)
            session_body, message_body, paths = full_capture
            self.assertEqual(
                session_body, {"title": request.title, "permission": _PROBE_PERMISSIONS}
            )
            self.assertIsInstance(message_body, dict)
            self.assertEqual(message_body["agent"], "auditor")  # type: ignore[index]
            self.assertEqual(  # type: ignore[index]
                message_body["model"], {"providerID": "test", "modelID": "strong"}
            )
            self.assertEqual(message_body["parts"], [{"type": "text", "text": request.prompt}])  # type: ignore[index]
            self.assertEqual(paths[-1], "/session/ses_local/message")
            self.assertNotIn("retryCount", message_body)  # type: ignore[arg-type]
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_create_session_rejects_unsafe_or_conflicting_response_identity(self) -> None:
        """Malformed session aliases and unsafe IDs cannot reach durable probe state."""
        from opencode_sprint_loop.agent_runner import InvocationRequest

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            request = InvocationRequest(
                "0001-auditor", 1, "auditor", "test/strong", "title", "probe\n", root
            )
            base = {
                "id": "ses_valid",
                "title": "title",
                "directory": str(root),
                "parentID": None,
                "permission": _PROBE_PERMISSIONS,
            }
            cases = (
                ({**base, "id": "ses\nunsafe"}, "unsafe ID"),
                ({**base, "parent_id": "unexpected-parent"}, "parent alias"),
                (
                    {
                        **base,
                        "permissions": [{"permission": "shell", "pattern": "*", "action": "allow"}],
                    },
                    "permission alias",
                ),
            )
            for response, label in cases:
                with self.subTest(label=label):
                    runner = OpenCodeServerRunner("http://127.0.0.1:4096")
                    with patch.object(runner, "_request", return_value=response):
                        with self.assertRaises(ControllerError) as context:
                            runner.create_session(request)
                    self.assertEqual(context.exception.code, "session_creation_failed")

    def test_fake_runner_is_deterministic_and_fresh(self) -> None:
        """The controller-facing fake records a fresh session and scripted terminal output."""
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:1", "1.17.18"),
            session_ids=["ses_one"],
            observations=[
                InvocationObservation(
                    "idle",
                    self._terminal_messages(),
                    {
                        "schema_version": 1,
                        "status": "completed",
                        "summary": "ok",
                        "checks": [],
                        "blocking_reason": None,
                    },
                    False,
                    False,
                    True,
                )
            ],
        )
        self.assertEqual(fake.existing_session_ids(), set())
        self.assertEqual(fake.validated.version, "1.17.18")

    def test_controller_probe_flow_persists_artifacts_before_placeholder(self) -> None:
        """A fake-driven probe durably records server/session/result evidence in order."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run
        from opencode_sprint_loop.config import load_config
        from opencode_sprint_loop.paths import runtime_paths

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        sprint_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
        managed = root / "repositories/managed"
        managed_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=managed, check=True, capture_output=True, text=True
        ).stdout.strip()
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", self._terminal_messages(completed), completed, False, False, True
                )
            ],
            transcript_messages=self._terminal_messages(completed),
        )
        self.assertEqual(_run(str(root), "http://127.0.0.1:4096", runner=fake), 4)
        config = load_config(root)
        paths = runtime_paths(root, config.multisprint, config.sprint)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        events = [
            json.loads(line) for line in paths.events.read_text(encoding="utf-8").splitlines()
        ]
        metadata_path = root / "invocations" / "foundation" / "1" / "0001-auditor" / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(state["reason"]["code"], "execution_not_implemented")
        self.assertEqual(
            [event["type"] for event in events],
            [
                "run.started",
                "state.entered",
                "server.validated",
                "agent.started",
                "agent.completed",
                "run.blocked",
            ],
        )
        self.assertEqual(metadata["session_id"], "ses_fake_0001")
        self.assertTrue((metadata_path.parent / "result.json").is_file())
        self.assertTrue((metadata_path.parent / "transcript.json").is_file())
        sprint_status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertEqual(
            set(sprint_status),
            {
                "?? info/foundation/1/events.jsonl",
                "?? info/foundation/1/lock.json",
                "?? info/foundation/1/state.json",
                "?? invocations/foundation/1/0001-auditor/metadata.json",
                "?? invocations/foundation/1/0001-auditor/prompt.md",
                "?? invocations/foundation/1/0001-auditor/result.json",
                "?? invocations/foundation/1/0001-auditor/transcript.json",
            },
        )
        self.assertEqual(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=managed,
                check=True,
                capture_output=True,
                text=True,
            ).stdout,
            "",
        )
        self.assertEqual(
            subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip(),
            sprint_head,
        )
        self.assertEqual(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=managed,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            managed_head,
        )

        without_server = [event for event in events if event["type"] != "server.validated"]
        with self.assertRaises(ControllerError):
            validate_event_history(without_server)
        duplicated_server = [*events[:3], deepcopy(events[2]), *events[3:]]
        with self.assertRaises(ControllerError):
            validate_event_history(duplicated_server)
        invalid_interruption = deepcopy(events[:5])
        invalid_interruption[-1]["type"] = "agent.interrupted"
        invalid_interruption[-1]["payload"] = {
            "previous_state": "validating",
            "invocation_id": "0001-auditor",
            "role": "auditor",
            "session_id": "ses_fake_0001",
            "interruption": {
                "code": "invocation_interrupted",
                "message": "synthetic interruption",
                "details": {},
            },
            "abort_acknowledged": 1,
        }
        with self.assertRaises(ControllerError):
            validate_event_history(invalid_interruption)

        mismatched_terminal = deepcopy(events)
        mismatched_terminal[4]["payload"]["session_id"] = "ses_other"
        with self.assertRaises(ControllerError):
            validate_event_history(mismatched_terminal)
        repeated_terminal = [*events[:5], deepcopy(events[4]), *events[5:]]
        with self.assertRaises(ControllerError):
            validate_event_history(repeated_terminal)

    @staticmethod
    def _terminal_messages(result: object | None = None) -> list[dict[str, object]]:
        """Return minimal sole-prompt/associated-assistant evidence for a fake probe."""
        prompt = probe_prompt("foundation", 1, "0001-auditor")
        return [
            {
                "id": "prompt-1",
                "role": "user",
                "sessionID": "ses_fake_0001",
                "parts": [
                    {
                        "type": "text",
                        "text": prompt,
                        "sessionID": "ses_fake_0001",
                        "messageID": "prompt-1",
                    }
                ],
            },
            {
                "id": "answer-1",
                "role": "assistant",
                "parentID": "prompt-1",
                "info": {
                    "agent": "auditor",
                    "providerID": "test",
                    "modelID": "strong",
                    "sessionID": "ses_fake_0001",
                },
                "parts": [
                    {
                        "type": "structured_output",
                        "value": {} if result is None else result,
                        "sessionID": "ses_fake_0001",
                        "messageID": "answer-1",
                    }
                ],
            },
        ]

    def test_valid_blocked_and_failed_results_complete_without_interruption(self) -> None:
        """Valid terminal agent outcomes retain their result event despite a blocked run."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run
        from opencode_sprint_loop.paths import runtime_paths
        from opencode_sprint_loop.config import load_config

        for status in ("blocked", "failed"):
            with self.subTest(status=status):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                result = {
                    "schema_version": 1,
                    "status": status,
                    "summary": "probe cannot advance",
                    "checks": [],
                    "blocking_reason": "synthetic block",
                }
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    observations=[
                        InvocationObservation(
                            "idle", self._terminal_messages(), result, False, False, True
                        )
                    ],
                    transcript_messages=self._terminal_messages(result),
                )
                with self.assertRaises(ControllerError) as context:
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, "invocation_failed")
                paths = runtime_paths(root, "foundation", 1)
                events = [json.loads(line) for line in paths.events.read_text().splitlines()]
                metadata = json.loads(
                    (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
                )
                self.assertEqual(
                    [event["type"] for event in events][-2:], ["agent.completed", "run.blocked"]
                )
                self.assertNotIn("agent.interrupted", [event["type"] for event in events])
                self.assertEqual(metadata["status"], status)
                self.assertEqual(metadata["result"], {"available": True, "status": status})
                self.assertEqual(load_config(root).sprint, 1)

    def test_persistence_reader_cross_validates_invocation_artifacts(self) -> None:
        """Missing and contradictory terminal artifacts fail as invocation-record corruption."""
        from opencode_sprint_loop.cli import _run
        from opencode_sprint_loop.config import load_config
        from opencode_sprint_loop.paths import runtime_paths
        from opencode_sprint_loop.status import validate_persistence
        from tests.integration.test_foundation import SprintRepositoryFixture

        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        for corruption in ("missing_transcript", "result_status", "transcript_session"):
            with self.subTest(corruption=corruption):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    observations=[
                        InvocationObservation(
                            "idle", self._terminal_messages(), completed, False, False, True
                        )
                    ],
                    transcript_messages=self._terminal_messages(completed),
                )
                self.assertEqual(_run(str(root), "http://127.0.0.1:4096", runner=fake), 4)
                directory = root / "invocations/foundation/1/0001-auditor"
                if corruption == "missing_transcript":
                    (directory / "transcript.json").unlink()
                elif corruption == "result_status":
                    result = json.loads((directory / "result.json").read_text())
                    result["status"] = "blocked"
                    result["blocking_reason"] = "synthetic contradiction"
                    (directory / "result.json").write_text(json.dumps(result) + "\n")
                else:
                    transcript = json.loads((directory / "transcript.json").read_text())
                    transcript["session_id"] = "ses_other"
                    (directory / "transcript.json").write_text(json.dumps(transcript) + "\n")
                config = load_config(root)
                with self.assertRaises(ControllerError) as context:
                    validate_persistence(
                        runtime_paths(root, config.multisprint, config.sprint), config
                    )
                self.assertEqual(context.exception.code, "inconsistent_invocation_record")

    def test_unexpected_probe_repository_change_is_preserved_and_blocks(self) -> None:
        """Post-probe Git verification reports but never repairs an accidental agent edit."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", self._terminal_messages(completed), completed, False, False, True
                )
            ],
            transcript_messages=self._terminal_messages(completed),
        )
        original_submit = fake.execute_prompt

        def mutate_after_submit(
            session: object, request: object, *, deadline: float | None = None
        ) -> InvocationObservation:
            response = original_submit(session, request, deadline=deadline)  # type: ignore[arg-type]
            (root / "repositories/managed/unexpected.txt").write_text("preserve me\n")
            return response

        with patch.object(fake, "execute_prompt", side_effect=mutate_after_submit):
            with self.assertRaises(ControllerError) as context:
                _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "unexpected_agent_repository_change")
        self.assertEqual(
            (root / "repositories/managed/unexpected.txt").read_text(), "preserve me\n"
        )
        state = json.loads((root / "info/foundation/1/state.json").read_text())
        self.assertEqual(state["reason"]["code"], "unexpected_agent_repository_change")

    def test_terminal_result_without_associated_assistant_evidence_is_interrupted(self) -> None:
        """Idle structured output without sole-prompt assistant evidence cannot pass."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation("idle", [], completed, False, False, False),
                InvocationObservation("idle", [], None, False, False, False),
            ],
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "transcript_capture_failed")
        self.assertEqual(fake.aborted, ["ses_fake_0001"])
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        self.assertIn("agent.interrupted", [event["type"] for event in events])

    def test_synchronous_response_needs_no_normal_observation_polling(self) -> None:
        """A terminal synchronous response completes without status or message polling."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", self._terminal_messages(completed), completed, False, False, True
                )
            ],
        )
        self.assertEqual(_run(str(root), "http://127.0.0.1:4096", runner=fake), 4)
        self.assertEqual(fake.observation_deadlines, [])

    def test_blocked_synchronous_worker_cancels_without_polling_or_waiting_for_shutdown(
        self,
    ) -> None:
        """Cancellation aborts one daemon-held POST while the main thread blocks on a queue."""
        from opencode_sprint_loop import cli
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        fake = FakeAgentRunner(ValidatedServer("http://127.0.0.1:4096", "1.17.18"))
        entered = threading.Event()
        release = threading.Event()

        def blocked_response(*_arguments: object, **_keywords: object) -> InvocationObservation:
            entered.set()
            release.wait(timeout=10)
            return InvocationObservation("idle", [], None, False, False)

        cancellation = cli._Cancellation()
        original_abort = fake.abort

        def abort_and_release(session: object) -> object:
            release.set()
            return original_abort(session)  # type: ignore[arg-type]

        def request_cancel() -> None:
            self.assertTrue(entered.wait(timeout=2))
            cancellation.signal_number = signal.SIGINT

        canceller = threading.Thread(target=request_cancel)
        canceller.start()
        with (
            patch.object(fake, "execute_prompt", side_effect=blocked_response),
            patch.object(fake, "abort", side_effect=abort_and_release),
        ):
            with self.assertRaises(cli._CancellationRequested):
                cli._run(str(root), "http://127.0.0.1:4096", runner=fake, cancellation=cancellation)
        canceller.join(timeout=2)
        self.assertEqual(fake.aborted, ["ses_fake_0001"])
        self.assertEqual(len(fake.observation_deadlines), 1)

    def test_synchronous_worker_timing_arbitrates_deadline_and_signal_order(self) -> None:
        """Delayed workers cannot outrun a deadline/cancellation after dequeue."""
        from opencode_sprint_loop import cli
        from opencode_sprint_loop.agent_runner import InvocationRequest

        session = CreatedSession("ses_fake_0001", "title", Path("/tmp"), tuple(_PROBE_PERMISSIONS))
        request = InvocationRequest(
            "0001-auditor", 1, "auditor", "test/strong", "title", "probe\n", Path("/tmp")
        )
        response = InvocationObservation("idle", [], None, False, False)

        # The arithmetic boundary is deterministic: a completion strictly
        # before a later cancellation is retained, while equality fails closed.
        later = cli._Cancellation()
        later.signal_number = signal.SIGINT
        later.requested_at = 20.0
        cli._arbitrate_synchronous_completion(19.0, 30.0, later)
        for completion, deadline, requested_at, expected in (
            (20.0, 30.0, 20.0, cli._CancellationRequested),
            (30.0, 30.0, None, ControllerError),
        ):
            with self.subTest(completion=completion, deadline=deadline):
                cancellation = cli._Cancellation()
                if requested_at is not None:
                    cancellation.signal_number = signal.SIGTERM
                    cancellation.requested_at = requested_at
                with self.assertRaises(expected):
                    cli._arbitrate_synchronous_completion(completion, deadline, cancellation)

        def wait_with(release_after: float, cancellation: cli._Cancellation) -> None:
            fake = FakeAgentRunner(ValidatedServer("http://127.0.0.1:1", "1.17.18"))
            entered = threading.Event()
            release = threading.Event()

            def delayed(*_arguments: object, **_keywords: object) -> InvocationObservation:
                entered.set()
                self.assertTrue(release.wait(timeout=2))
                return response

            releaser = threading.Thread(
                target=lambda: (entered.wait(timeout=2), time.sleep(release_after), release.set())
            )
            releaser.start()
            try:
                with patch.object(fake, "execute_prompt", side_effect=delayed):
                    cli._wait_for_synchronous_response(
                        fake, session, request, time.monotonic() + 0.02, cancellation
                    )
            finally:
                release.set()
                releaser.join(timeout=2)

        # The worker completes only after the deadline and is rejected despite
        # producing a response; it cannot write persistence through this path.
        with self.assertRaises(ControllerError) as context:
            wait_with(0.05, cli._Cancellation())
        self.assertEqual(context.exception.code, "invocation_timed_out")

        # A signal recorded before releasing the delayed worker wins even if
        # the response is already queued when the controller wakes.
        cancellation = cli._Cancellation()
        cancellation.record(signal.SIGINT, None)
        with self.assertRaises(cli._CancellationRequested):
            wait_with(0, cancellation)

    def test_abort_non_acknowledgement_is_persisted(self) -> None:
        """A false abort acknowledgement remains explicit interruption evidence."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation("idle", self._terminal_messages(), None, False, False, True),
                InvocationObservation("idle", [], None, False, False),
            ],
            abort_acknowledged=False,
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "invalid_agent_result")
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-2]["type"], "agent.interrupted")
        self.assertIs(events[-2]["payload"]["abort_acknowledged"], False)
        self.assertEqual(events[-2]["payload"]["abort_confirmation"], "idle")

    def test_abort_acknowledgement_response_is_strict(self) -> None:
        """Only a documented JSON boolean abort acknowledgement is accepted verbatim."""
        from opencode_sprint_loop.agent_runner import CreatedSession

        runner = OpenCodeServerRunner("http://127.0.0.1:4096")
        session = CreatedSession(
            "ses_test",
            "title",
            Path("/tmp"),
            tuple(_PROBE_PERMISSIONS),
        )
        for response in (
            None,
            {},
            {"acknowledged": True},
            {"acknowledged": "yes"},
            1,
            "true",
        ):
            with (
                self.subTest(response=response),
                patch.object(runner, "_request", return_value=response),
            ):
                with self.assertRaises(ControllerError) as context:
                    runner.abort(session)
                self.assertEqual(context.exception.code, "malformed_server_response")
        for response in (True, False):
            with (
                self.subTest(response=response),
                patch.object(runner, "_request", return_value=response),
            ):
                self.assertIs(runner.abort(session).acknowledged, response)

    def test_abort_confirmation_records_idle_or_unconfirmed(self) -> None:
        """Post-abort evidence is explicitly classified without treating absence as idle."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        scenarios: tuple[tuple[str, list[InvocationObservation | Exception], str | None], ...] = (
            (
                "idle",
                [
                    InvocationObservation("unexpected", [], None, False, False),
                    InvocationObservation("idle", [], None, False, False),
                ],
                "idle",
            ),
            (
                "status_idle",
                [
                    InvocationObservation("unexpected", [], None, False, False),
                    InvocationObservation(
                        "idle", self._terminal_messages(completed), completed, False, False, True
                    ),
                ],
                "idle",
            ),
            (
                "unconfirmed",
                [
                    InvocationObservation("unexpected", [], None, False, False),
                    ControllerError("server_unavailable", "Synthetic confirmation loss"),
                ],
                None,
            ),
        )
        for name, observations, expected_confirmation in scenarios:
            with self.subTest(confirmation=name):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"), observations=observations
                )
                diagnostics = StringIO()
                with redirect_stderr(diagnostics), self.assertRaises(ControllerError) as context:
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, "invalid_agent_result")
                if expected_confirmation is None:
                    self.assertIn(
                        "OpenCode cancellation could not be confirmed; the session may remain active.",
                        diagnostics.getvalue(),
                    )
                else:
                    self.assertNotIn("cancellation could not be confirmed", diagnostics.getvalue())
                events = [
                    json.loads(line)
                    for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
                ]
                self.assertEqual(events[-2]["payload"]["abort_confirmation"], expected_confirmation)

    def test_abort_confirmation_uses_one_deadline_and_records_idle_evidence(self) -> None:
        """Abort observation requests share one bounded deadline and persist their confirmation."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation("unexpected", [], None, False, False),
                InvocationObservation("busy", [], None, False, False),
                InvocationObservation("busy", [], None, False, False),
                InvocationObservation("idle", [], None, False, False),
            ],
        )
        with patch("opencode_sprint_loop.cli.time.sleep", return_value=None):
            with self.assertRaises(ControllerError) as context:
                _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "invalid_agent_result")
        self.assertEqual(len(fake.observation_deadlines), 3)
        self.assertTrue(all(deadline is not None for deadline in fake.observation_deadlines))
        self.assertEqual(len(set(fake.observation_deadlines[1:])), 1)
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-2]["payload"]["abort_confirmation"], "idle")

    def test_malformed_returned_evidence_blocks_with_sanitized_evidence(self) -> None:
        """Safe malformed synchronous evidence is retained even though it cannot complete the probe."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", self._terminal_messages(), completed, False, False, True
                )
            ],
            transcript_messages=[{"malformed": True}],
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "transcript_capture_failed")
        directory = root / "invocations/foundation/1/0001-auditor"
        self.assertFalse((directory / "result.json").exists())
        metadata = json.loads((directory / "metadata.json").read_text())
        self.assertEqual(metadata["transcript"]["status"], "complete")
        self.assertTrue((directory / "transcript.json").is_file())

    def test_unhashable_result_status_uses_durable_invalid_result_path(self) -> None:
        """List and object statuses cannot escape result failure persistence as TypeError."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        for status in ([], {}):
            with self.subTest(status=status):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                malformed = {
                    "schema_version": 1,
                    "status": status,
                    "summary": "ok",
                    "checks": [],
                    "blocking_reason": None,
                }
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    observations=[
                        InvocationObservation(
                            "idle", self._terminal_messages(), malformed, False, False, True
                        ),
                        InvocationObservation("idle", [], None, False, False),
                    ],
                    transcript_messages=self._terminal_messages(),
                )
                with self.assertRaises(ControllerError) as context:
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, "invalid_agent_result")
                metadata = json.loads(
                    (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
                )
                events = [
                    json.loads(line)
                    for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
                ]
                state = json.loads((root / "info/foundation/1/state.json").read_text())
                self.assertEqual(metadata["status"], "interrupted")
                self.assertFalse(
                    (root / "invocations/foundation/1/0001-auditor/result.json").exists()
                )
                self.assertEqual(events[-2]["type"], "agent.interrupted")
                self.assertEqual(events[-1]["type"], "run.blocked")
                self.assertEqual(state["reason"]["code"], "invalid_agent_result")

    def test_returned_transcript_tool_evidence_prevents_probe_success(self) -> None:
        """A forbidden tool in returned terminal evidence fails closed."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        transcript = self._terminal_messages()
        transcript[1]["parts"].append(  # type: ignore[union-attr]
            {
                "type": "tool",
                "tool": "shell",
                "sessionID": "ses_fake_0001",
                "messageID": "answer-1",
            }
        )
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", self._terminal_messages(), completed, False, False, True
                ),
                InvocationObservation("idle", [], None, False, False),
            ],
            transcript_messages=transcript,
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "unexpected_probe_tool")
        directory = root / "invocations/foundation/1/0001-auditor"
        metadata = json.loads((directory / "metadata.json").read_text())
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        state = json.loads((root / "info/foundation/1/state.json").read_text())
        self.assertFalse((directory / "result.json").exists())
        self.assertEqual(metadata["status"], "interrupted")
        self.assertEqual(metadata["transcript"]["status"], "complete")
        self.assertTrue((directory / "transcript.json").is_file())
        self.assertNotIn("agent.completed", [event["type"] for event in events])
        self.assertEqual(events[-2]["type"], "agent.interrupted")
        self.assertEqual(state["reason"]["code"], "unexpected_probe_tool")
        self.assertIn("shell", json.loads((directory / "transcript.json").read_text())["content"])

    def test_structured_output_error_precedes_generic_terminal_error(self) -> None:
        """Documented info.error.name maps through orchestration to invalid output."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        messages = self._terminal_messages()
        messages[1]["info"] = {"error": {"name": "StructuredOutputError"}}  # type: ignore[index]
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation("idle", messages, None, True, False, True, True),
                InvocationObservation("idle", [], None, False, False),
            ],
            transcript_messages=messages,
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "invalid_agent_result")
        directory = root / "invocations/foundation/1/0001-auditor"
        metadata = json.loads((directory / "metadata.json").read_text())
        self.assertEqual(metadata["transcript"]["status"], "complete")
        self.assertTrue((directory / "transcript.json").is_file())

    def test_surrogate_results_and_transcripts_follow_durable_failure_paths(self) -> None:
        """Escaped surrogate external strings cannot bypass bounded interruption persistence."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        scenarios = (
            ("summary", {**completed, "summary": "\ud800"}, None, "invalid_agent_result"),
            (
                "blocking_reason",
                {
                    **completed,
                    "status": "blocked",
                    "blocking_reason": "\ud800",
                },
                None,
                "invalid_agent_result",
            ),
            ("message_id", completed, ("id", "\ud800"), "transcript_capture_failed"),
            (
                "transcript_text",
                completed,
                ("text", "\ud800"),
                "transcript_capture_failed",
            ),
        )
        for label, result, transcript_mutation, code in scenarios:
            with self.subTest(label=label):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                transcript = self._terminal_messages(result)
                if transcript_mutation is not None:
                    field, value = transcript_mutation
                    if field == "id":
                        transcript[0][field] = value
                    else:
                        transcript[0]["parts"][0][field] = value  # type: ignore[index]
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    observations=[
                        InvocationObservation(
                            "idle", self._terminal_messages(), result, False, False, True
                        ),
                        InvocationObservation("idle", [], None, False, False),
                    ],
                    transcript_messages=transcript,
                )
                with self.assertRaises(ControllerError) as context:
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, code)
                metadata = json.loads(
                    (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
                )
                state = json.loads((root / "info/foundation/1/state.json").read_text())
                self.assertEqual(metadata["status"], "interrupted")
                self.assertEqual(state["reason"]["code"], code)

    def test_safe_evidence_precedes_permission_tool_and_structured_failures(self) -> None:
        """Every semantic probe violation retains its sanitized transcript for diagnosis."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        for label, part, structured_error, expected_code in (
            (
                "permission",
                {"type": "permission", "sessionID": "ses_fake_0001", "messageID": "answer-1"},
                False,
                "unexpected_probe_tool",
            ),
            (
                "tool",
                {
                    "type": "tool",
                    "tool": "shell",
                    "sessionID": "ses_fake_0001",
                    "messageID": "answer-1",
                },
                False,
                "unexpected_probe_tool",
            ),
            (
                "structured",
                {
                    "type": "tool",
                    "tool": "StructuredOutputError",
                    "sessionID": "ses_fake_0001",
                    "messageID": "answer-1",
                },
                True,
                "invalid_agent_result",
            ),
        ):
            with self.subTest(label=label):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                transcript = self._terminal_messages(completed)
                transcript[1]["parts"].append(part)  # type: ignore[union-attr]
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    observations=[
                        InvocationObservation(
                            "idle",
                            self._terminal_messages(),
                            completed,
                            structured_error,
                            False,
                            True,
                            structured_error,
                        ),
                        InvocationObservation("idle", [], None, False, False),
                    ],
                    transcript_messages=transcript,
                )
                with self.assertRaises(ControllerError) as context:
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, expected_code)
                directory = root / "invocations/foundation/1/0001-auditor"
                metadata = json.loads((directory / "metadata.json").read_text())
                self.assertEqual(metadata["transcript"]["status"], "complete")
                self.assertTrue((directory / "transcript.json").is_file())

    def test_missing_structured_output_fails_immediately_without_corrective_retry(self) -> None:
        """A terminal free-form answer is invalid output and takes the one-abort path."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation("idle", self._terminal_messages(), None, False, False, True),
                InvocationObservation("idle", [], None, False, False),
            ],
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "invalid_agent_result")
        self.assertEqual(fake.aborted, ["ses_fake_0001"])
        self.assertEqual(len(fake.created), 1)
        self.assertEqual(fake.submitted, ["ses_fake_0001"])

    def test_prompt_and_status_confirmation_transport_failures_abort_once(self) -> None:
        """Uncertain post-create transport outcomes preserve identity through bounded abort."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        scenarios = {
            "submit": {
                "submit_error": ControllerError(
                    "prompt_submission_failed", "Synthetic prompt outcome is unknown"
                ),
                "observations": [InvocationObservation("idle", [], None, False, False)],
            },
            "observe": {
                "observations": [
                    ControllerError("server_unavailable", "Synthetic observation failure"),
                    InvocationObservation("idle", [], None, False, False),
                ]
            },
        }
        for operation, scripted in scenarios.items():
            with self.subTest(operation=operation):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"), **scripted
                )
                with self.assertRaises(ControllerError):
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(fake.aborted, ["ses_fake_0001"])
                metadata = json.loads(
                    (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
                )
                self.assertEqual(metadata["session_id"], "ses_fake_0001")
                self.assertEqual(metadata["status"], "interrupted")
                events = [
                    json.loads(line)
                    for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
                ]
                self.assertEqual(events[-2]["type"], "agent.interrupted")
                self.assertEqual(events[-1]["type"], "run.blocked")

    def test_terminal_assistant_error_rejects_structured_result_and_preserves_evidence(
        self,
    ) -> None:
        """Any terminal assistant error blocks the probe before accepting its result."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        terminal_messages = self._terminal_messages()
        terminal_messages[1]["error"] = "synthetic terminal failure"
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", terminal_messages, completed, False, False, True, True
                )
            ],
            transcript_messages=terminal_messages,
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "invocation_failed")
        directory = root / "invocations/foundation/1/0001-auditor"
        metadata = json.loads((directory / "metadata.json").read_text())
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(fake.aborted, ["ses_fake_0001"])
        self.assertFalse((directory / "result.json").exists())
        self.assertTrue((directory / "transcript.json").is_file())
        self.assertEqual(metadata["result"], {"available": False, "status": None})
        self.assertEqual(events[-2]["type"], "agent.interrupted")
        self.assertEqual(events[-1]["type"], "run.blocked")
        self.assertNotEqual(events[-1]["payload"]["reason"]["code"], "execution_not_implemented")

    def test_terminal_transcript_must_bind_prompt_agent_provider_and_model(self) -> None:
        """Stale or misrouted terminal evidence cannot complete the execution probe."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        for field, value in (
            ("prompt", "different submitted prompt\n"),
            ("agent", "builder"),
            ("providerID", "other-provider"),
            ("modelID", "other-model"),
        ):
            with self.subTest(field=field):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                transcript = self._terminal_messages(completed)
                if field == "prompt":
                    transcript[0]["parts"][0]["text"] = value  # type: ignore[index]
                else:
                    transcript[1]["info"][field] = value  # type: ignore[index]
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    observations=[
                        InvocationObservation(
                            "idle", self._terminal_messages(), completed, False, False, True
                        ),
                        InvocationObservation("idle", [], None, False, False),
                    ],
                    transcript_messages=transcript,
                )
                with self.assertRaises(ControllerError) as context:
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, "transcript_capture_failed")
                directory = root / "invocations/foundation/1/0001-auditor"
                self.assertFalse((directory / "result.json").exists())
                self.assertTrue((directory / "transcript.json").is_file())
                state = json.loads((root / "info/foundation/1/state.json").read_text())
                self.assertEqual(state["reason"]["code"], "transcript_capture_failed")

    def test_session_id_persistence_failures_abort_once_without_submission(self) -> None:
        """Known sessions survive either session-ID persistence failure in terminal metadata."""
        import opencode_sprint_loop.cli as cli

        from tests.integration.test_foundation import SprintRepositoryFixture

        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        for failure in ("agent_started", "metadata"):
            with self.subTest(failure=failure):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    observations=[
                        InvocationObservation(
                            "idle", self._terminal_messages(), completed, False, False, True
                        )
                    ],
                )
                original_observe = cli.persist_observation
                original_metadata = cli.write_metadata

                def fail_started(*args: object, **kwargs: object) -> dict[str, object]:
                    if args[4] == "agent.started":
                        raise ControllerError(
                            "invocation_record_failed",
                            "Synthetic agent.started persistence failure",
                        )
                    return original_observe(*args, **kwargs)  # type: ignore[arg-type]

                def fail_running_metadata(paths: object, metadata: object) -> None:
                    if isinstance(metadata, dict) and metadata["status"] == "running":
                        raise ControllerError(
                            "invocation_record_failed", "Synthetic session metadata failure"
                        )
                    original_metadata(paths, metadata)  # type: ignore[arg-type]

                target = (
                    "opencode_sprint_loop.cli.persist_observation"
                    if failure == "agent_started"
                    else "opencode_sprint_loop.cli.write_metadata"
                )
                replacement = fail_started if failure == "agent_started" else fail_running_metadata
                with patch(target, side_effect=replacement):
                    with self.assertRaises(ControllerError) as context:
                        cli._run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, "invocation_record_failed")
                self.assertEqual(len(fake.created), 1)
                self.assertEqual(fake.submitted, [])
                self.assertEqual(fake.aborted, ["ses_fake_0001"])
                metadata = json.loads(
                    (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
                )
                self.assertEqual(
                    metadata["status"], "failed" if failure == "agent_started" else "interrupted"
                )
                self.assertEqual(metadata["session_id"], "ses_fake_0001")
                self.assertIsNotNone(metadata["started_at"])

    def test_terminal_metadata_failure_preserves_result_transcript_write_ahead_prefix(self) -> None:
        """A terminal metadata fault leaves truthful immutable artifacts without terminal events."""
        import opencode_sprint_loop.cli as cli

        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", self._terminal_messages(), completed, False, False, True
                )
            ],
            transcript_messages=self._terminal_messages(completed),
        )
        original_metadata = cli.write_metadata

        def fail_terminal_metadata(paths: object, metadata: object) -> None:
            if (
                isinstance(metadata, dict)
                and metadata["status"] == "completed"
                and metadata["result"] == {"available": True, "status": "completed"}
            ):
                raise ControllerError(
                    "invocation_record_failed", "Synthetic terminal metadata failure"
                )
            original_metadata(paths, metadata)  # type: ignore[arg-type]

        with patch("opencode_sprint_loop.cli.write_metadata", side_effect=fail_terminal_metadata):
            with self.assertRaises(ControllerError) as context:
                cli._run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "invocation_record_failed")
        directory = root / "invocations/foundation/1/0001-auditor"
        metadata = json.loads((directory / "metadata.json").read_text())
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(fake.aborted, [])
        self.assertEqual(metadata["status"], "running")
        self.assertTrue((directory / "result.json").is_file())
        self.assertTrue((directory / "transcript.json").is_file())
        self.assertEqual([event["type"] for event in events][-1], "agent.started")
        self.assertNotIn("agent.completed", [event["type"] for event in events])
        self.assertNotIn("agent.interrupted", [event["type"] for event in events])
        self.assertNotIn("run.blocked", [event["type"] for event in events])
        from opencode_sprint_loop.config import load_config
        from opencode_sprint_loop.paths import runtime_paths
        from opencode_sprint_loop.status import validate_persistence

        config = load_config(root)
        state, _ = validate_persistence(runtime_paths(root, "foundation", 1), config)
        self.assertEqual(state["state"], "validating")
        self.assertIsNotNone(state["active_invocation"])

    def test_each_terminal_write_boundary_preserves_only_coherent_prefixes(self) -> None:
        """Result, transcript, agent-event, and run-block faults never imply false success."""
        import opencode_sprint_loop.cli as cli

        from tests.integration.test_foundation import SprintRepositoryFixture

        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        for boundary in ("result", "transcript", "agent_event", "run_block"):
            with self.subTest(boundary=boundary):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    observations=[
                        InvocationObservation(
                            "idle", self._terminal_messages(), completed, False, False, True
                        )
                    ],
                    transcript_messages=self._terminal_messages(completed),
                )
                original_observe = cli.persist_observation
                original_transition = cli.transition

                def fail_agent_event(*args: object, **kwargs: object) -> dict[str, object]:
                    if args[4] == "agent.completed":
                        raise ControllerError(
                            "invocation_record_failed", "Synthetic agent event failure"
                        )
                    return original_observe(*args, **kwargs)  # type: ignore[arg-type]

                def fail_run_block(*args: object, **kwargs: object) -> dict[str, object]:
                    if args[4] == "blocked":
                        raise ControllerError(
                            "invocation_record_failed", "Synthetic run block failure"
                        )
                    return original_transition(*args, **kwargs)  # type: ignore[arg-type]

                target = {
                    "result": "opencode_sprint_loop.cli.write_result",
                    "transcript": "opencode_sprint_loop.cli.write_transcript",
                    "agent_event": "opencode_sprint_loop.cli.persist_observation",
                    "run_block": "opencode_sprint_loop.cli.transition",
                }[boundary]
                replacement = {
                    "result": ControllerError(
                        "invocation_record_failed", "Synthetic result write failure"
                    ),
                    "transcript": ControllerError(
                        "invocation_record_failed", "Synthetic transcript write failure"
                    ),
                    "agent_event": fail_agent_event,
                    "run_block": fail_run_block,
                }[boundary]
                with patch(target, side_effect=replacement):
                    with self.assertRaises(ControllerError):
                        cli._run(str(root), "http://127.0.0.1:4096", runner=fake)
                directory = root / "invocations/foundation/1/0001-auditor"
                metadata = json.loads((directory / "metadata.json").read_text())
                events = [
                    json.loads(line)
                    for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
                ]
                event_types = [event["type"] for event in events]
                if boundary == "result":
                    self.assertFalse((directory / "result.json").exists())
                    self.assertEqual(metadata["status"], "interrupted")
                    self.assertEqual(event_types[-2:], ["agent.interrupted", "run.blocked"])
                elif boundary == "transcript":
                    self.assertTrue((directory / "result.json").is_file())
                    self.assertFalse((directory / "transcript.json").exists())
                    self.assertEqual(metadata["status"], "running")
                    self.assertEqual(event_types[-1], "agent.started")
                elif boundary == "agent_event":
                    self.assertTrue((directory / "result.json").is_file())
                    self.assertTrue((directory / "transcript.json").is_file())
                    self.assertEqual(metadata["status"], "completed")
                    self.assertEqual(event_types[-1], "agent.started")
                else:
                    self.assertEqual(metadata["status"], "completed")
                    self.assertEqual(event_types[-1], "agent.completed")
                    self.assertNotIn("run.blocked", event_types)

    def test_timeout_aborts_once_before_terminal_interruption_persistence(self) -> None:
        """A monotonic timeout runs the one-abort cleanup path exactly once."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[InvocationObservation("busy", [], None, False, False)],
        )
        with patch(
            "opencode_sprint_loop.cli.time.monotonic", side_effect=[0, 100, 100, 100, 111, 111]
        ):
            with self.assertRaises(ControllerError) as context:
                _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "invocation_timed_out")
        self.assertEqual(fake.aborted, ["ses_fake_0001"])
        metadata = json.loads(
            (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
        )
        self.assertEqual(metadata["status"], "timed_out")
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-2]["type"], "agent.interrupted")
        self.assertEqual(events[-1]["type"], "run.blocked")

    def test_session_creation_failures_terminalize_planned_metadata(self) -> None:
        """Definitive and ambiguous create failures preserve a null session identity."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        for code in ("session_creation_failed", "session_creation_ambiguous"):
            with self.subTest(code=code):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    create_error=ControllerError(code, "Synthetic creation outcome"),
                )
                with self.assertRaises(ControllerError) as context:
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, code)
                metadata = json.loads(
                    (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
                )
                self.assertEqual(metadata["status"], "failed")
                self.assertIsNone(metadata["session_id"])
                self.assertIsNone(metadata["started_at"])
                self.assertIsNotNone(metadata["completed_at"])
                self.assertEqual(metadata["error"]["code"], code)

    def test_reused_session_id_is_aborted_before_prompt_submission(self) -> None:
        """A create response matching the bounded snapshot cannot become an invocation."""
        from tests.integration.test_foundation import SprintRepositoryFixture
        from opencode_sprint_loop.cli import _run

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        fake = FakeAgentRunner(ValidatedServer("http://127.0.0.1:4096", "1.17.18"))
        fake.preexisting = {"ses_fake_0001"}
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "non_fresh_session")
        self.assertEqual(fake.submitted, [])
        self.assertEqual(fake.aborted, ["ses_fake_0001"])
        metadata = json.loads(
            (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
        )
        self.assertIsNone(metadata["session_id"])
        self.assertEqual(metadata["status"], "failed")

    def test_http_failure_classes_and_strict_json_are_normalized(self) -> None:
        """Transport, HTTP, redirect, malformed, and oversized responses fail distinctly."""
        runner = OpenCodeServerRunner("http://127.0.0.1:4096")
        url = "http://127.0.0.1:4096/global/health"
        failures = (
            (ConnectionRefusedError("synthetic refusal"), "server_unavailable"),
            (TimeoutError("synthetic timeout"), "server_unavailable"),
            (URLError(ssl.SSLError("synthetic TLS failure")), "server_unavailable"),
            (HTTPError(url, 401, "synthetic", {}, None), "server_authentication_failed"),
            (HTTPError(url, 403, "synthetic", {}, None), "server_unavailable"),
            (HTTPError(url, 404, "synthetic", {}, None), "server_unavailable"),
            (HTTPError(url, 500, "synthetic", {}, None), "server_unavailable"),
            (
                HTTPError(url, 302, "synthetic", {"Location": "/other"}, None),
                "server_api_incompatible",
            ),
        )
        for failure, code in failures:
            with self.subTest(failure=type(failure).__name__, code=code):
                with patch.object(runner._opener, "open", side_effect=failure):
                    with self.assertRaises(ControllerError) as context:
                        runner._request("GET", "/global/health")
                self.assertEqual(context.exception.code, code)

        malformed = (
            b"{",
            b'{"healthy":true,"healthy":false}',
            b'{"healthy":NaN}',
            b"\xff",
        )
        for payload in malformed:
            with self.subTest(payload=payload):
                response = _HTTPResponse(url, payload)
                with patch.object(runner._opener, "open", return_value=response):
                    with self.assertRaises(ControllerError) as context:
                        runner._request("GET", "/global/health")
                self.assertEqual(context.exception.code, "malformed_server_response")

        redirected = _HTTPResponse("http://127.0.0.1:4096/other", b"{}")
        with patch.object(runner._opener, "open", return_value=redirected):
            with self.assertRaises(ControllerError) as context:
                runner._request("GET", "/global/health")
        self.assertEqual(context.exception.code, "server_api_incompatible")
        with patch("opencode_sprint_loop.opencode_runner.MAX_RESPONSE_BYTES", 8):
            response = _HTTPResponse(url, b"123456789")
            with patch.object(runner._opener, "open", return_value=response):
                with self.assertRaises(ControllerError) as context:
                    runner._request("GET", "/global/health")
        self.assertEqual(context.exception.code, "server_response_too_large")

    def test_runner_caps_slow_transport_at_invocation_deadline(self) -> None:
        """A slow request receives only the remaining monotonic invocation budget."""
        runner = OpenCodeServerRunner("http://127.0.0.1:4096")
        timeouts: list[float] = []

        def slow_open(_request: object, *, timeout: float) -> _HTTPResponse:
            timeouts.append(timeout)
            time.sleep(timeout)
            raise socket.timeout("synthetic slow transport")

        deadline = time.monotonic() + 0.05
        started = time.monotonic()
        with patch.object(runner._opener, "open", side_effect=slow_open):
            with self.assertRaises(ControllerError) as context:
                runner._request("GET", "/session/status", deadline=deadline)
        elapsed = time.monotonic() - started
        self.assertEqual(context.exception.code, "invocation_timed_out")
        self.assertEqual(len(timeouts), 1)
        self.assertLessEqual(timeouts[0], 0.06)
        self.assertLess(elapsed, 0.2)

    def test_synchronous_message_post_uses_full_remaining_invocation_budget(self) -> None:
        """The one long non-idempotent POST is not capped by the short request timeout."""
        from opencode_sprint_loop.agent_runner import CreatedSession, InvocationRequest

        runner = OpenCodeServerRunner("http://127.0.0.1:4096")
        root = Path("/tmp").resolve()
        request = InvocationRequest(
            "0001-auditor", 1, "auditor", "test/strong", "title", "probe\n", root
        )
        session = CreatedSession(
            "ses_test",
            "title",
            root,
            tuple(_PROBE_PERMISSIONS),
        )
        response = {
            "info": {
                "id": "msg_assistant",
                "role": "assistant",
                "sessionID": "ses_test",
                "parentID": "msg_user",
                "agent": "auditor",
                "providerID": "test",
                "modelID": "strong",
                "structured": {
                    "schema_version": 1,
                    "status": "completed",
                    "summary": "ok",
                    "checks": [],
                    "blocking_reason": None,
                },
            },
            "parts": [],
        }
        timeouts: list[float] = []

        def capture_timeout(_request: object, *, timeout: float) -> _HTTPResponse:
            timeouts.append(timeout)
            return _HTTPResponse(
                "http://127.0.0.1:4096/session/ses_test/message", json.dumps(response).encode()
            )

        with (
            patch("opencode_sprint_loop.opencode_runner.time.monotonic", return_value=100.0),
            patch.object(runner._opener, "open", side_effect=capture_timeout),
        ):
            runner.execute_prompt(session, request, deadline=125.0)
        self.assertEqual(timeouts, [25.0])

    def test_adapter_rejects_conflicting_terminal_message_aliases(self) -> None:
        """Top-level and info aliases cannot contradict one another in a terminal POST response."""
        from opencode_sprint_loop.agent_runner import InvocationRequest

        runner = OpenCodeServerRunner("http://127.0.0.1:4096")
        request = InvocationRequest(
            "0001-auditor", 1, "auditor", "test/strong", "title", "probe\n", Path("/tmp")
        )
        result = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        base = {
            "id": "msg_assistant",
            "role": "assistant",
            "sessionID": "ses_test",
            "parentID": "msg_user",
            "agent": "auditor",
            "providerID": "test",
            "modelID": "strong",
            "structured_output": result,
            "error": None,
            "info": {
                "id": "msg_assistant",
                "role": "assistant",
                "session_id": "ses_test",
                "parent_id": "msg_user",
                "agent": "auditor",
                "provider_id": "test",
                "model_id": "strong",
                "structured": result,
                "error": None,
            },
            "parts": [
                {
                    "type": "structured_output",
                    "value": result,
                    "output": result,
                    "sessionID": "ses_test",
                    "messageID": "msg_assistant",
                }
            ],
        }
        session = CreatedSession("ses_test", "title", Path("/tmp"), tuple(_PROBE_PERMISSIONS))
        self.assertEqual(
            runner._synchronous_response(deepcopy(base), request, session).structured_result, result
        )
        cases = (
            ("role", lambda message: message["info"].__setitem__("role", "user")),
            ("ID", lambda message: message["info"].__setitem__("id", "other")),
            ("error", lambda message: message["info"].__setitem__("error", "failed")),
            (
                "structured output",
                lambda message: message["info"].__setitem__("structured", {"other": True}),
            ),
            ("parent", lambda message: message["info"].__setitem__("parent_id", "other-parent")),
            ("route", lambda message: message["info"].__setitem__("agent", "builder")),
            (
                "part output",
                lambda message: message["parts"][0].__setitem__("output", {"other": True}),
            ),
        )
        for label, mutate in cases:
            with self.subTest(alias=label):
                message = deepcopy(base)
                mutate(message)
                with self.assertRaises(ControllerError) as context:
                    runner._synchronous_response(message, request, session)
                self.assertEqual(context.exception.code, "malformed_server_response")

    def test_terminal_session_and_part_associations_fail_closed_live_and_persisted(self) -> None:
        """Assistant and every retained part bind exactly to the created session/message."""
        from opencode_sprint_loop.agent_runner import InvocationRequest

        runner = OpenCodeServerRunner("http://127.0.0.1:4096")
        request = InvocationRequest(
            "0001-auditor", 1, "auditor", "test/strong", "title", "probe\n", Path("/tmp")
        )
        session = CreatedSession("ses_test", "title", Path("/tmp"), tuple(_PROBE_PERMISSIONS))
        result = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        messages = [
            {
                "id": "msg_user",
                "role": "user",
                "sessionID": "ses_test",
                "parts": [
                    {
                        "type": "text",
                        "text": "probe\n",
                        "sessionID": "ses_test",
                        "messageID": "msg_user",
                    }
                ],
            },
            {
                "id": "msg_assistant",
                "role": "assistant",
                "sessionID": "ses_test",
                "parentID": "msg_user",
                "info": {
                    "session_id": "ses_test",
                    "agent": "auditor",
                    "providerID": "test",
                    "modelID": "strong",
                    "structured": result,
                },
                "parts": [
                    {
                        "type": "structured_output",
                        "value": result,
                        "sessionID": "ses_test",
                        "messageID": "msg_assistant",
                    }
                ],
            },
        ]
        self.assertEqual(
            runner._synchronous_response(deepcopy(messages[1]), request, session).structured_result,
            result,
        )
        validate_transcript_messages(
            deepcopy(messages),
            expected_result=result,
            expected_prompt="probe\n",
            expected_role="auditor",
            expected_model="test/strong",
            expected_session_id="ses_test",
        )
        mutations = (
            (
                "missing assistant session",
                lambda value: (value[1].pop("sessionID"), value[1]["info"].pop("session_id")),
            ),
            (
                "conflicting assistant session",
                lambda value: value[1]["info"].__setitem__("session_id", "other"),
            ),
            ("wrong assistant session", lambda value: value[1].__setitem__("sessionID", "other")),
            ("missing part session", lambda value: value[1]["parts"][0].pop("sessionID")),
            (
                "wrong part session",
                lambda value: value[1]["parts"][0].__setitem__("sessionID", "other"),
            ),
            ("missing part message", lambda value: value[1]["parts"][0].pop("messageID")),
            (
                "wrong part message",
                lambda value: value[1]["parts"][0].__setitem__("messageID", "other"),
            ),
            ("wrong reconstructed parent", lambda value: value[1].__setitem__("parentID", "other")),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                mutated = deepcopy(messages)
                mutate(mutated)
                with self.assertRaises(ControllerError):
                    validate_transcript_messages(
                        mutated,
                        expected_result=result,
                        expected_prompt="probe\n",
                        expected_role="auditor",
                        expected_model="test/strong",
                        expected_session_id="ses_test",
                    )
                if label != "wrong reconstructed parent":
                    with self.assertRaises(ControllerError):
                        runner._synchronous_response(mutated[1], request, session)

    def test_tool_evidence_requires_documented_exact_identity(self) -> None:
        """``name`` is only a consistency alias; every tool still needs exact ``tool``."""
        from opencode_sprint_loop.agent_runner import InvocationRequest

        runner = OpenCodeServerRunner("http://127.0.0.1:4096")
        request = InvocationRequest(
            "0001-auditor", 1, "auditor", "test/strong", "title", "probe\n", Path("/tmp")
        )
        session = CreatedSession("ses_test", "title", Path("/tmp"), tuple(_PROBE_PERMISSIONS))
        result = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        base = {
            "id": "msg_assistant",
            "role": "assistant",
            "sessionID": "ses_test",
            "parentID": "msg_user",
            "info": {
                "agent": "auditor",
                "providerID": "test",
                "modelID": "strong",
                "structured": result,
            },
            "parts": [
                {
                    "type": "tool",
                    "tool": "StructuredOutput",
                    "name": "StructuredOutput",
                    "sessionID": "ses_test",
                    "messageID": "msg_assistant",
                }
            ],
        }
        self.assertFalse(
            runner._synchronous_response(deepcopy(base), request, session).unexpected_tool
        )
        validate_transcript_messages(
            [
                {
                    "id": "msg_user",
                    "role": "user",
                    "sessionID": "ses_test",
                    "parts": [
                        {
                            "type": "text",
                            "text": "probe\n",
                            "sessionID": "ses_test",
                            "messageID": "msg_user",
                        }
                    ],
                },
                deepcopy(base),
            ],
            expected_result=result,
            expected_prompt="probe\n",
            expected_role="auditor",
            expected_model="test/strong",
            expected_session_id="ses_test",
        )
        cases = (
            ("missing tool", lambda part: part.pop("tool"), ControllerError),
            (
                "malformed tool",
                lambda part: part.__setitem__("tool", ["StructuredOutput"]),
                ControllerError,
            ),
            ("conflicting name", lambda part: part.__setitem__("name", "shell"), ControllerError),
            (
                "name is not substitute",
                lambda part: (part.pop("tool"), part.__setitem__("name", "StructuredOutput")),
                ControllerError,
            ),
            ("shell", lambda part: (part.__setitem__("tool", "shell"), part.pop("name")), None),
            (
                "shell-like",
                lambda part: (part.__setitem__("tool", "StructuredOutput "), part.pop("name")),
                None,
            ),
        )
        for label, mutate, expected_error in cases:
            with self.subTest(case=label):
                message = deepcopy(base)
                mutate(message["parts"][0])
                if expected_error is not None:
                    with self.assertRaises(expected_error):
                        runner._synchronous_response(message, request, session)
                else:
                    self.assertTrue(
                        runner._synchronous_response(message, request, session).unexpected_tool
                    )
                transcript = [
                    {
                        "id": "msg_user",
                        "role": "user",
                        "sessionID": "ses_test",
                        "parts": [
                            {
                                "type": "text",
                                "text": "probe\n",
                                "sessionID": "ses_test",
                                "messageID": "msg_user",
                            }
                        ],
                    },
                    message,
                ]
                with self.assertRaises(ControllerError):
                    validate_transcript_messages(
                        transcript,
                        expected_result=result,
                        expected_prompt="probe\n",
                        expected_role="auditor",
                        expected_model="test/strong",
                        expected_session_id="ses_test",
                    )

    def test_transcript_validation_rejects_conflicting_message_aliases(self) -> None:
        """Persisted transcript validation applies the same fail-closed alias reconciliation."""
        result = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        base = self._terminal_messages(result)
        assistant = base[1]
        assistant["info"].update(  # type: ignore[index]
            {
                "id": "answer-1",
                "role": "assistant",
                "parent_id": "prompt-1",
                "error": None,
                "structured_output": result,
            }
        )
        self.assertEqual(
            validate_transcript_messages(
                deepcopy(base),
                expected_result=result,
                expected_prompt=probe_prompt("foundation", 1, "0001-auditor"),
                expected_role="auditor",
                expected_model="test/strong",
                expected_session_id="ses_fake_0001",
            ),
            base,
        )
        cases = (
            ("role", lambda message: message[1]["info"].__setitem__("role", "user")),
            ("ID", lambda message: message[1]["info"].__setitem__("id", "other")),
            ("error", lambda message: message[1]["info"].__setitem__("error", "failed")),
            (
                "structured output",
                lambda message: message[1]["info"].__setitem__(
                    "structured_output", {"other": True}
                ),
            ),
            (
                "parent",
                lambda message: message[1]["info"].__setitem__("parent_id", "other-parent"),
            ),
            ("route", lambda message: message[1]["info"].__setitem__("agent", "builder")),
            (
                "part output",
                lambda message: message[1]["parts"][0].update({"output": {"other": True}}),
            ),
        )
        for label, mutate in cases:
            with self.subTest(alias=label):
                messages = deepcopy(base)
                mutate(messages)
                with self.assertRaises(ControllerError) as context:
                    validate_transcript_messages(
                        messages,
                        expected_result=result,
                        expected_prompt=probe_prompt("foundation", 1, "0001-auditor"),
                        expected_role="auditor",
                        expected_model="test/strong",
                        expected_session_id="ses_fake_0001",
                    )
                self.assertEqual(context.exception.code, "transcript_capture_failed")

    def test_authentication_header_is_in_memory_and_origin_bound(self) -> None:
        """Inherited Basic authentication reaches only the validated request origin."""
        with patch.dict(
            "os.environ",
            {
                "OPENCODE_SERVER_USERNAME": "synthetic-user",
                "OPENCODE_SERVER_PASSWORD": "synthetic-password",
            },
            clear=True,
        ):
            runner = OpenCodeServerRunner("https://example.invalid")
        observed: dict[str, str | None] = {}

        def open_request(request: object, *, timeout: int) -> _HTTPResponse:
            observed["url"] = request.full_url  # type: ignore[attr-defined]
            observed["authorization"] = request.get_header("Authorization")  # type: ignore[attr-defined]
            observed["timeout"] = str(timeout)
            return _HTTPResponse("https://example.invalid/global/health", b"{}")

        with patch.object(runner._opener, "open", side_effect=open_request):
            self.assertEqual(runner._request("GET", "/global/health"), {})
        self.assertEqual(observed["url"], "https://example.invalid/global/health")
        self.assertTrue(observed["authorization"].startswith("Basic "))  # type: ignore[union-attr]
        self.assertNotIn("synthetic-password", str(observed))

        with patch.dict(
            "os.environ",
            {"OPENCODE_SERVER_USERNAME": "ignored", "OPENCODE_SERVER_PASSWORD": ""},
            clear=True,
        ):
            with self.assertRaises(ControllerError) as context:
                HTTPAuthentication.from_environment()
        self.assertEqual(context.exception.code, "invalid_server_authentication")

    def test_workspace_aliases_and_malformed_path_fields(self) -> None:
        """Canonical aliases pass while wrong and malformed default workspaces fail closed."""
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            alias = root / "alias"
            alias.symlink_to(root, target_is_directory=True)
            request = ServerValidationRequest(
                root,
                {"builder": "builder", "auditor": "auditor", "ci_fixer": "ci-fixer"},
                {"builder": "test/medium", "auditor": "test/strong", "ci_fixer": "test/medium"},
            )
            valid = {
                "/global/health": {"healthy": True, "version": "1.17.18"},
                "/agent": [{"name": "builder"}, {"name": "auditor"}, {"name": "ci-fixer"}],
                "/config/providers": {
                    "providers": [{"id": "test", "models": {"medium": {}, "strong": {}}}]
                },
                "/provider": {
                    "all": [{"id": "test", "models": {"medium": {}, "strong": {}}}],
                    "connected": ["test"],
                },
            }
            runner = OpenCodeServerRunner("http://127.0.0.1:4096")
            responses = {
                **valid,
                "/path": {"directory": str(alias), "worktree": str(root / ".")},
            }
            with patch.object(
                runner,
                "_request",
                side_effect=lambda _method, path, *args, **kwargs: responses[path],
            ):
                self.assertEqual(runner.validate_server(request).version, "1.17.18")
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                for path_response, code in (
                    (
                        {"directory": str(root.parent), "worktree": str(root)},
                        "wrong_server_workspace",
                    ),
                    (
                        {"directory": str(root), "worktree": str(root.parent)},
                        "wrong_server_workspace",
                    ),
                    ({"directory": None, "worktree": str(root)}, "malformed_server_response"),
                    ({"directory": str(root)}, "malformed_server_response"),
                    ({"directory": "", "worktree": str(root)}, "malformed_server_response"),
                    ({"directory": ".", "worktree": str(root)}, "malformed_server_response"),
                    ({"directory": str(root), "worktree": "relative"}, "malformed_server_response"),
                ):
                    with self.subTest(response=path_response):
                        responses = {**valid, "/path": path_response}
                        with patch.object(
                            runner,
                            "_request",
                            side_effect=lambda _method, path, *args, **kwargs: responses[path],
                        ):
                            with self.assertRaises(ControllerError) as context:
                                runner.validate_server(request)
                        self.assertEqual(context.exception.code, code)
            finally:
                os.chdir(previous_cwd)

    def test_created_session_requires_an_absolute_directory(self) -> None:
        """Empty and relative create responses cannot inherit the controller CWD as identity."""
        from opencode_sprint_loop.agent_runner import InvocationRequest

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            request = InvocationRequest(
                "0001-auditor", 1, "auditor", "test/strong", "title", "probe\n", root
            )
            response = {
                "id": "ses_valid",
                "title": "title",
                "parentID": None,
                "permission": _PROBE_PERMISSIONS,
            }
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                for directory in ("", ".", "relative"):
                    with self.subTest(directory=directory):
                        runner = OpenCodeServerRunner("http://127.0.0.1:4096")
                        with patch.object(
                            runner, "_request", return_value={**response, "directory": directory}
                        ):
                            with self.assertRaises(ControllerError) as context:
                                runner.create_session(request)
                        self.assertEqual(context.exception.code, "malformed_server_response")
            finally:
                os.chdir(previous_cwd)

    def test_all_preflight_failure_categories_are_mutation_free_with_fake(self) -> None:
        """Every scripted server-validation category precedes records, sessions, and Git changes."""
        from opencode_sprint_loop.cli import _run
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        managed = root / "repositories/managed"
        initial = {
            "sprint_head": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
            ).stdout,
            "managed_head": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=managed,
                check=True,
                capture_output=True,
                text=True,
            ).stdout,
        }
        codes = (
            "server_unavailable",
            "server_authentication_failed",
            "server_unhealthy",
            "unsupported_server_version",
            "server_api_incompatible",
            "wrong_server_workspace",
            "configured_agent_unavailable",
            "configured_model_unavailable",
            "malformed_server_response",
            "server_response_too_large",
        )
        for code in codes:
            with self.subTest(code=code):
                fake = FakeAgentRunner(
                    ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
                    validation_error=ControllerError(code, f"Synthetic {code}"),
                )
                with self.assertRaises(ControllerError) as context:
                    _run(str(root), "http://127.0.0.1:4096", runner=fake)
                self.assertEqual(context.exception.code, code)
                self.assertEqual(fake.created, [])
                self.assertFalse((root / "info").exists())
                self.assertFalse((root / "invocations").exists())
                self.assertEqual(
                    subprocess.run(
                        ["git", "status", "--porcelain", "--untracked-files=all"],
                        cwd=root,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout,
                    "",
                )
                self.assertEqual(
                    subprocess.run(
                        ["git", "status", "--porcelain", "--untracked-files=all"],
                        cwd=managed,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout,
                    "",
                )
        self.assertEqual(
            subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
            ).stdout,
            initial["sprint_head"],
        )
        self.assertEqual(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=managed,
                check=True,
                capture_output=True,
                text=True,
            ).stdout,
            initial["managed_head"],
        )

    def test_post_lock_local_revalidation_retains_complete_no_run_status(self) -> None:
        """Until bounded post-lock checks finish, the durable status remains the no-run envelope."""
        from opencode_sprint_loop import cli
        from opencode_sprint_loop.config import load_config
        from opencode_sprint_loop.git import validate_root
        from opencode_sprint_loop.paths import runtime_paths
        from opencode_sprint_loop.status import project_status
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", self._terminal_messages(), completed, False, False, True
                )
            ],
            transcript_messages=self._terminal_messages(completed),
        )
        checking = threading.Event()
        release = threading.Event()
        original = cli.validate_preflight
        calls = 0

        def pause_second_check(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if calls == 2:
                checking.set()
                if not release.wait(timeout=10):
                    raise RuntimeError("synthetic post-lock revalidation wait timed out")
            return original(*args, **kwargs)  # type: ignore[arg-type]

        outcome: list[int | BaseException] = []

        def run_probe() -> None:
            try:
                outcome.append(cli._run(str(root), "http://127.0.0.1:4096", runner=fake))
            except BaseException as error:  # pragma: no cover - asserted below.
                outcome.append(error)

        with patch("opencode_sprint_loop.cli.validate_preflight", side_effect=pause_second_check):
            thread = threading.Thread(target=run_probe)
            thread.start()
            try:
                self.assertTrue(checking.wait(timeout=5))
                config = load_config(root)
                paths = runtime_paths(root, config.multisprint, config.sprint)
                repository = validate_root(root)
                run_lock, _ = cli._lock_paths(repository.git_dir)
                status = project_status(root, config, paths, run_lock)
                self.assertFalse(status["run_exists"])
                self.assertFalse(status["process_running"])
                self.assertIsNone(status["state"])
                self.assertFalse((root / "info").exists())
                self.assertFalse((root / "invocations").exists())
            finally:
                release.set()
                thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, [4])

    def test_structured_result_validation_matrix_and_bounds(self) -> None:
        """Completed/blocked/failed results pass and every schema/boundary class fails."""
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        for status in ("completed", "blocked", "failed"):
            value = {
                **completed,
                "status": status,
                "blocking_reason": None if status == "completed" else "synthetic reason",
            }
            self.assertEqual(validate_result(value), value)
        invalid = [
            None,
            {key: value for key, value in completed.items() if key != "summary"},
            {**completed, "unknown": None},
            {**completed, "schema_version": True},
            {**completed, "schema_version": 2},
            {**completed, "status": "unknown"},
            {**completed, "status": []},
            {**completed, "summary": ""},
            {**completed, "summary": 1},
            {**completed, "summary": "x" * 4097},
            {**completed, "checks": {}},
            {
                **completed,
                "checks": [{"command": "x", "result": "passed", "details": None}],
            },
            {**completed, "checks": [None] * 101},
            {**completed, "blocking_reason": "not-null"},
            {**completed, "status": "blocked", "blocking_reason": None},
            {**completed, "status": "failed", "blocking_reason": ""},
            {**completed, "status": "failed", "blocking_reason": "x" * 4097},
            {**completed, "summary": float("nan")},
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ControllerError) as context:
                    validate_result(value)
                self.assertEqual(context.exception.code, "invalid_agent_result")
        with patch("opencode_sprint_loop.invocations.MAX_RESULT_BYTES", 32):
            with self.assertRaises(ControllerError) as context:
                validate_result(completed)
        self.assertEqual(context.exception.code, "invalid_agent_result")

    def test_fake_runner_scripts_every_lifecycle_operation_and_evidence_class(self) -> None:
        """The deterministic fake exposes success and failure controls for every runner method."""
        from opencode_sprint_loop.agent_runner import InvocationRequest

        root = Path("/tmp").resolve()
        request = InvocationRequest(
            "0001-auditor", 1, "auditor", "test/strong", "title", "prompt\n", root
        )
        categories = (
            "server_unavailable",
            "server_authentication_failed",
            "server_unhealthy",
            "unsupported_server_version",
            "server_api_incompatible",
            "wrong_server_workspace",
            "configured_agent_unavailable",
            "configured_model_unavailable",
            "malformed_server_response",
            "server_response_too_large",
        )
        for code in categories:
            fake = FakeAgentRunner(
                ValidatedServer("http://127.0.0.1:1", "1.17.18"),
                validation_error=ControllerError(code, "synthetic"),
            )
            with self.assertRaises(ControllerError) as context:
                fake.validate_server(ServerValidationRequest(root, {}, {}))
            self.assertEqual(context.exception.code, code)

        observations = [
            InvocationObservation("idle", [], None, False, False),
            InvocationObservation("busy", [], None, False, False),
            InvocationObservation("idle", [], None, False, False),
        ]
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:1", "1.17.18"),
            session_ids=["ses_unique", "ses_duplicate"],
            observations=observations,
            transcript_messages=[{"id": "m", "role": "user", "parts": []}],
            abort_acknowledged=False,
        )
        fake.preexisting = {"ses_duplicate"}
        self.assertEqual(fake.existing_session_ids(), {"ses_duplicate"})
        session = fake.create_session(request)
        self.assertEqual(session.session_id, "ses_unique")
        fake.execute_prompt(session, request)
        self.assertEqual(
            [fake.observe_status(session) for _ in range(2)],
            [
                "busy",
                "idle",
            ],
        )
        self.assertFalse(fake.abort(session).acknowledged)

        sensitive_messages = [
            {
                "id": "message-1",
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "Authorization: Bearer synthetic-secret",
                    },
                    {"type": "text", "text": "x" * 128},
                ],
            }
        ]
        with patch("opencode_sprint_loop.invocations.MAX_STRING_BYTES", 64):
            wrapper = transcript_wrapper(session.session_id, sensitive_messages)
        self.assertIn("[REDACTED]", wrapper["content"])
        self.assertIn("[TRUNCATED]", wrapper["content"])
        self.assertTrue(wrapper["truncated"])

        operation_errors = {
            "session_snapshot_error": "server_unavailable",
            "create_error": "session_creation_ambiguous",
            "submit_error": "prompt_submission_failed",
            "abort_error": "server_unavailable",
        }
        for argument, code in operation_errors.items():
            scripted = FakeAgentRunner(
                ValidatedServer("http://127.0.0.1:1", "1.17.18"),
                **{argument: ControllerError(code, "synthetic")},
            )
            with self.subTest(operation=argument):
                with self.assertRaises(ControllerError) as context:
                    if argument == "session_snapshot_error":
                        scripted.existing_session_ids()
                    elif argument == "create_error":
                        scripted.create_session(request)
                    elif argument == "submit_error":
                        scripted.execute_prompt(session, request)
                    elif argument == "abort_error":
                        scripted.abort(session)
                self.assertEqual(context.exception.code, code)

    def test_cancellation_after_create_aborts_known_session_before_submission(self) -> None:
        """Cancellation recorded by create still durably identifies and aborts its session."""
        from opencode_sprint_loop import cli
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        cancellation = cli._Cancellation()
        fake = FakeAgentRunner(ValidatedServer("http://127.0.0.1:4096", "1.17.18"))
        original_create = fake.create_session

        def create_then_cancel(request: object) -> object:
            session = original_create(request)  # type: ignore[arg-type]
            cancellation.record(signal.SIGTERM, None)
            return session

        with patch.object(fake, "create_session", side_effect=create_then_cancel):
            with self.assertRaises(cli._CancellationRequested) as context:
                cli._run(
                    str(root),
                    "http://127.0.0.1:4096",
                    runner=fake,
                    cancellation=cancellation,
                )
        self.assertEqual(context.exception.exit_status, 143)
        self.assertEqual(fake.submitted, [])
        self.assertEqual(fake.aborted, ["ses_fake_0001"])
        metadata = json.loads(
            (root / "invocations/foundation/1/0001-auditor/metadata.json").read_text()
        )
        self.assertEqual(metadata["session_id"], "ses_fake_0001")
        self.assertEqual(metadata["status"], "interrupted")

    def test_atomic_artifact_faults_leave_absent_prior_or_next_complete_json(self) -> None:
        """Short-write, permission, install, replacement, and sync faults never truncate JSON."""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = allocate_paths(root, "test", 1, 1, "auditor")
            result = {
                "schema_version": 1,
                "status": "completed",
                "summary": "ok",
                "checks": [],
                "blocking_reason": None,
            }
            import opencode_sprint_loop.invocations as invocations

            real_write = invocations.os.write

            def short_write(descriptor: int, payload: bytes) -> int:
                return real_write(descriptor, payload[: max(1, len(payload) // 2)])

            with patch("opencode_sprint_loop.invocations.os.write", side_effect=short_write):
                with self.assertRaises(ControllerError):
                    write_result(paths, result)
            self.assertFalse(paths.result.exists())

            real_open = invocations.os.open

            def deny_create(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if flags & os.O_CREAT:
                    raise PermissionError("synthetic permission failure")
                return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

            with patch("opencode_sprint_loop.invocations.os.open", side_effect=deny_create):
                with self.assertRaises(ControllerError):
                    write_result(paths, result)
            self.assertFalse(paths.result.exists())

            with patch(
                "opencode_sprint_loop.invocations.os.link",
                side_effect=OSError("synthetic pre-install failure"),
            ):
                with self.assertRaises(ControllerError):
                    write_result(paths, result)
            self.assertFalse(paths.result.exists())
            write_result(paths, result)
            self.assertEqual(json.loads(paths.result.read_text()), result)

            metadata = new_metadata(
                "00000000-0000-4000-8000-000000000000",
                "0001-auditor",
                1,
                "test/strong",
                "1.17.18",
                "managed",
            )
            write_metadata(paths, metadata)
            prior = paths.metadata.read_bytes()
            replacement = deepcopy(metadata)
            replacement["status"] = "session_created"
            replacement["session_id"] = "ses_test"
            replacement["started_at"] = replacement["created_at"]
            with patch(
                "opencode_sprint_loop.invocations.os.replace",
                side_effect=OSError("synthetic pre-replace failure"),
            ):
                with self.assertRaises(ControllerError):
                    write_metadata(paths, replacement)
            self.assertEqual(paths.metadata.read_bytes(), prior)
            self.assertEqual(json.loads(prior), metadata)

            real_fsync = invocations.os.fsync

            def fail_directory_sync(descriptor: int) -> None:
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError("synthetic post-replace directory sync failure")
                real_fsync(descriptor)

            with patch(
                "opencode_sprint_loop.invocations.os.fsync", side_effect=fail_directory_sync
            ):
                with self.assertRaises(ControllerError):
                    write_metadata(paths, replacement)
            self.assertEqual(json.loads(paths.metadata.read_text()), replacement)

    def test_prompt_metadata_transcript_string_and_identifier_bounds(self) -> None:
        """Every non-HTTP Sprint 2 hard bound rejects or visibly truncates as specified."""
        with patch("opencode_sprint_loop.invocations.MAX_PROMPT_BYTES", 8):
            with self.assertRaises(ControllerError):
                validate_prompt("123456789")
        metadata = new_metadata(
            "00000000-0000-4000-8000-000000000000",
            "0001-auditor",
            1,
            "test/strong",
            "1.17.18",
            "managed",
        )
        with TemporaryDirectory() as temporary:
            paths = allocate_paths(Path(temporary), "test", 1, 1, "auditor")
            with patch("opencode_sprint_loop.invocations.MAX_METADATA_BYTES", 8):
                with self.assertRaises(ControllerError):
                    write_metadata(paths, metadata)
        messages = [
            {
                "id": "message-1",
                "role": "user",
                "parts": [{"type": "text", "text": "abcdefghijk"}],
            }
        ]
        with patch("opencode_sprint_loop.invocations.MAX_STRING_BYTES", 8):
            wrapper = transcript_wrapper("ses_test", messages)
        self.assertTrue(wrapper["truncated"])
        self.assertIn("[TRUNCATED]", wrapper["content"])
        with self.assertRaises(ControllerError):
            transcript_wrapper("s" * 1025, messages)
        malformed = deepcopy(messages)
        malformed[0]["id"] = "m" * 1025
        with self.assertRaises(ControllerError):
            validate_transcript_messages(malformed)

    def test_cross_record_mismatch_matrix_fails_closed(self) -> None:
        """Every durable identity, status, availability, truncation, and path mismatch is rejected."""
        from opencode_sprint_loop.cli import _run
        from opencode_sprint_loop.config import load_config
        from opencode_sprint_loop.paths import runtime_paths
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        completed = {
            "schema_version": 1,
            "status": "completed",
            "summary": "ok",
            "checks": [],
            "blocking_reason": None,
        }
        fake = FakeAgentRunner(
            ValidatedServer("http://127.0.0.1:4096", "1.17.18"),
            observations=[
                InvocationObservation(
                    "idle", self._terminal_messages(), completed, False, False, True
                )
            ],
            transcript_messages=self._terminal_messages(completed),
        )
        self.assertEqual(_run(str(root), "http://127.0.0.1:4096", runner=fake), 4)
        config = load_config(root)
        runtime = runtime_paths(root, config.multisprint, config.sprint)
        state = json.loads(runtime.state.read_text())
        events = [json.loads(line) for line in runtime.events.read_text().splitlines()]
        directory = root / "invocations/foundation/1/0001-auditor"
        paths = {
            name: directory / name
            for name in ("metadata.json", "prompt.md", "result.json", "transcript.json")
        }
        originals = {name: path.read_bytes() for name, path in paths.items()}

        def mutate_metadata(field: str, value: object) -> None:
            metadata = json.loads(originals["metadata.json"])
            metadata[field] = value
            paths["metadata.json"].write_text(json.dumps(metadata) + "\n")

        cases = {
            "run": lambda: mutate_metadata("run_id", "11111111-1111-4111-8111-111111111111"),
            "invocation": lambda: mutate_metadata("invocation_id", "0002-auditor"),
            "sequence": lambda: mutate_metadata("sequence", 2),
            "role": lambda: mutate_metadata("role", "builder"),
            "model": lambda: mutate_metadata("model", "test/other"),
            "session": lambda: mutate_metadata("session_id", "ses_other"),
            "server": lambda: mutate_metadata("server_version", "1.17.17"),
            "input_commits": lambda: mutate_metadata("input_commits", {"other": None}),
            "result_availability": lambda: mutate_metadata(
                "result", {"available": False, "status": None}
            ),
            "transcript_availability": lambda: mutate_metadata(
                "transcript", {"status": "unavailable", "truncated": False}
            ),
            "transcript_truncation": lambda: mutate_metadata(
                "transcript", {"status": "truncated", "truncated": True}
            ),
            "result_status": lambda: paths["result.json"].write_text(
                json.dumps({**completed, "status": "blocked", "blocking_reason": "synthetic"})
                + "\n"
            ),
            "transcript_session": lambda: paths["transcript.json"].write_text(
                json.dumps(
                    {
                        **json.loads(originals["transcript.json"]),
                        "session_id": "ses_other",
                    }
                )
                + "\n"
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(mismatch=name):
                for artifact, payload in originals.items():
                    paths[artifact].write_bytes(payload)
                mutate()
                with self.assertRaises(ControllerError) as context:
                    validate_invocation_records(root, config, state, events)
                self.assertEqual(context.exception.code, "inconsistent_invocation_record")
        for artifact, payload in originals.items():
            paths[artifact].write_bytes(payload)
        moved = directory.with_name("0002-auditor")
        directory.rename(moved)
        try:
            with self.assertRaises(ControllerError) as context:
                validate_invocation_records(root, config, state, events)
            self.assertEqual(context.exception.code, "inconsistent_invocation_record")
        finally:
            moved.rename(directory)

    def test_process_signals_abort_once_and_return_conventional_status(self) -> None:
        """Real SIGINT/SIGTERM delivery records orderly interruption evidence."""
        from tests.integration.test_foundation import SprintRepositoryFixture

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signal_number):
                fixture = SprintRepositoryFixture()
                root = fixture.create()
                self.addCleanup(fixture.close)
                _Handler.root = str(root.resolve())
                _Handler.mode = "busy"
                _Handler.abort_requests = 0
                server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                environment = dict(os.environ)
                source = str(Path(__file__).resolve().parents[2] / "src")
                environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "opencode_sprint_loop.cli",
                        "run",
                        "--root",
                        str(root),
                        "--server-url",
                        f"http://127.0.0.1:{server.server_port}",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                )
                try:
                    state_path = root / "info/foundation/1/state.json"
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if state_path.exists() and json.loads(state_path.read_text()).get(
                            "active_invocation"
                        ):
                            break
                        time.sleep(0.05)
                    else:
                        self.fail("child did not persist its active invocation")
                    status_process = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "opencode_sprint_loop.cli",
                            "status",
                            "--root",
                            str(root),
                            "--json",
                        ],
                        capture_output=True,
                        text=True,
                        env=environment,
                        timeout=5,
                        check=False,
                    )
                    self.assertEqual(status_process.returncode, 0, status_process.stderr)
                    active_status = json.loads(status_process.stdout)
                    self.assertTrue(active_status["process_running"])
                    self.assertEqual(
                        active_status["active"],
                        {
                            "role": "auditor",
                            "invocation_id": "0001-auditor",
                            "session_id": "ses_local",
                        },
                    )
                    process.send_signal(signal_number)
                    _, stderr = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 128 + signal_number, stderr)
                    self.assertEqual(_Handler.abort_requests, 1)
                    events = [
                        json.loads(line)
                        for line in (root / "info/foundation/1/events.jsonl")
                        .read_text()
                        .splitlines()
                    ]
                    self.assertEqual(events[-2]["type"], "agent.interrupted")
                    self.assertEqual(events[-1]["type"], "run.blocked")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)
                    server.shutdown()
                    thread.join()
                    server.server_close()

    def test_process_signal_before_durable_state_leaves_no_runtime_artifacts(self) -> None:
        """A recorded preflight signal exits conventionally without creating records."""
        from tests.integration.test_foundation import SprintRepositoryFixture

        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        _Handler.root = str(root.resolve())
        _Handler.mode = "slow_preflight"
        _Handler.preflight_started.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        environment = dict(os.environ)
        source = str(Path(__file__).resolve().parents[2] / "src")
        environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "opencode_sprint_loop.cli",
                "run",
                "--root",
                str(root),
                "--server-url",
                f"http://127.0.0.1:{server.server_port}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        try:
            self.assertTrue(_Handler.preflight_started.wait(timeout=5))
            process.send_signal(signal.SIGINT)
            _, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130, stderr)
            self.assertFalse((root / "info").exists())
            self.assertFalse((root / "invocations").exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            server.shutdown()
            thread.join()
            server.server_close()
            _Handler.mode = "complete"


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
