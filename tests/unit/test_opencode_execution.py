"""Offline Sprint 2 URL, runner, artifact, and probe-flow tests."""

from __future__ import annotations

import json
import os
import signal
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

from opencode_sprint_loop.agent_runner import (
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
    validate_metadata,
    validate_result,
    write_metadata,
    write_prompt,
    write_transcript,
)
from opencode_sprint_loop.opencode_runner import (
    HTTPAuthentication,
    OpenCodeServerRunner,
    parse_server_url,
)


class _Handler(BaseHTTPRequestHandler):
    """Minimal local documented-API fake for exercising the real HTTP adapter."""

    root = "/tmp"
    mode = "complete"
    abort_requests = 0
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
            self._json({"providers": ["test"]})
        elif self.path == "/provider":
            self._json([{"id": "test", "connected": True, "models": {"medium": {}, "strong": {}}}])
        elif self.path == "/session":
            self._json([])
        elif self.path == "/session/status":
            self._json({"ses_local": "busy" if self.mode == "busy" else "idle"})
        elif self.path == "/session/ses_local/message":
            if self.mode == "busy":
                self._json([])
            else:
                self._json(
                    [
                        {
                            "id": "msg_user",
                            "role": "user",
                            "parts": [{"type": "text", "text": "probe"}],
                        },
                        {
                            "id": "msg_assistant",
                            "role": "assistant",
                            "parentID": "msg_user",
                            **(
                                {"error": "synthetic terminal failure"}
                                if self.mode == "terminal_message_error"
                                else {}
                            ),
                            "parts": [
                                {
                                    "type": "structured_output",
                                    "value": {
                                        "schema_version": 1,
                                        "status": "completed",
                                        "summary": "ok",
                                        "checks": [],
                                        "blocking_reason": None,
                                    },
                                },
                                *(
                                    [{"type": "tool", "tool": "shell"}]
                                    if self.mode == "unexpected_tool"
                                    else []
                                ),
                            ],
                        },
                    ]
                )
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        """Serve fresh-session, prompt, and abort calls."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/session":
            if self.mode == "create_reject":
                self.send_error(500)
                return
            request = json.loads(raw.decode())
            self._json(
                {
                    "id": "ses_local",
                    "title": request["title"],
                    "directory": self.root,
                    "parentID": None,
                    "permission": [{"permission": "*", "pattern": "*", "action": "deny"}],
                }
            )
        elif self.path.endswith("/abort"):
            type(self).abort_requests += 1
            type(self).mode = "complete"
            self._json({"acknowledged": True})
        elif self.path.endswith("/prompt_async"):
            self._json({"accepted": True})
        else:
            self.send_error(404)


class OpenCodeExecutionTests(unittest.TestCase):
    """Exercise the public Sprint 2 execution boundary without external services."""

    def test_url_rules_and_in_memory_authentication(self) -> None:
        """Origins normalize while unsafe URL/auth forms fail without disclosure."""
        self.assertEqual(parse_server_url("HTTP://Example.invalid:80/"), "http://example.invalid")
        self.assertEqual(
            parse_server_url("https://example.invalid:444"), "https://example.invalid:444"
        )
        for value in (
            "",
            "ssh://host",
            "http://user:pass@host",
            "http://host/a",
            "http://host?q=x",
            "http://host#x",
            "http://host:%zz",
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
            self.assertTrue(authentication.header().startswith("Basic "))  # type: ignore[union-attr]

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
            wrapper = transcript_wrapper("ses_large", [{"body": "x" * 500} for _ in range(3)])
            self.assertTrue(wrapper["truncated"])
            self.assertTrue(wrapper["content"].endswith("\n[TRUNCATED]"))
            with TemporaryDirectory() as temporary:
                paths = allocate_paths(Path(temporary), "test", 1, 1, "auditor")
                write_transcript(paths, wrapper)
                self.assertLessEqual(paths.transcript.stat().st_size, 1024)
                with self.assertRaises(ControllerError):
                    write_transcript(paths, wrapper)

    def test_real_adapter_uses_local_fake_server(self) -> None:
        """Preflight and a complete fresh lifecycle use only local HTTP endpoints."""
        with TemporaryDirectory() as temporary:
            _Handler.root = str(Path(temporary).resolve())
            _Handler.mode = "complete"
            _Handler.abort_requests = 0
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
                self.assertEqual(runner.existing_session_ids(), set())
                session = runner.create_session(request)
                runner.submit_prompt(session, request)
                observation = runner.observe(session)
                self.assertEqual(observation.structured_result["status"], "completed")
                _Handler.mode = "terminal_message_error"
                errored_observation = runner.observe(session)
                self.assertTrue(errored_observation.terminal_assistant)
                self.assertTrue(errored_observation.terminal_assistant_error)
                _Handler.mode = "unexpected_tool"
                self.assertTrue(runner.observe(session).unexpected_tool)
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
                "/config/providers": {"providers": ["test"]},
                "/provider": [
                    {
                        "id": "test",
                        "connected": True,
                        "models": {"medium": {}, "strong": {}},
                    }
                ],
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
                ("/provider", [], "configured_model_unavailable"),
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
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ControllerError):
                    validate_result(invalid)
        wrapper = transcript_wrapper(
            "ses_test",
            [{"token": "synthetic-secret", "body": "Authorization: Bearer synthetic-secret"}],
        )
        self.assertIn("[REDACTED]", wrapper["content"])
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
            transcript_messages=self._terminal_messages(),
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

    @staticmethod
    def _terminal_messages() -> list[dict[str, object]]:
        """Return minimal sole-prompt/associated-assistant evidence for a fake probe."""
        return [
            {"id": "prompt-1", "role": "user", "parts": [{"type": "text", "text": "probe"}]},
            {
                "id": "answer-1",
                "role": "assistant",
                "parentID": "prompt-1",
                "parts": [{"type": "structured_output", "value": {}}],
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
                    transcript_messages=self._terminal_messages(),
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
                    "idle", self._terminal_messages(), completed, False, False, True
                )
            ],
            transcript_messages=self._terminal_messages(),
        )
        original_submit = fake.submit_prompt

        def mutate_after_submit(session: object, request: object) -> None:
            original_submit(session, request)  # type: ignore[arg-type]
            (root / "repositories/managed/unexpected.txt").write_text("preserve me\n")

        with patch.object(fake, "submit_prompt", side_effect=mutate_after_submit):
            with self.assertRaises(ControllerError) as context:
                _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "unexpected_agent_repository_change")
        self.assertEqual(
            (root / "repositories/managed/unexpected.txt").read_text(), "preserve me\n"
        )
        state = json.loads((root / "info/foundation/1/state.json").read_text())
        self.assertEqual(state["reason"]["code"], "unexpected_agent_repository_change")

    def test_transcript_failure_after_result_keeps_completed_metadata(self) -> None:
        """A result artifact is never contradicted when later transcript capture fails."""
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
                    "idle", self._terminal_messages(), completed, False, False, True
                )
            ],
            transcript_error=ControllerError(
                "transcript_capture_failed", "Synthetic transcript failure"
            ),
        )
        with self.assertRaises(ControllerError) as context:
            _run(str(root), "http://127.0.0.1:4096", runner=fake)
        self.assertEqual(context.exception.code, "transcript_capture_failed")
        directory = root / "invocations/foundation/1/0001-auditor"
        metadata = json.loads((directory / "metadata.json").read_text())
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(metadata["result"], {"available": True, "status": "completed"})
        self.assertEqual(metadata["transcript"]["status"], "unavailable")
        self.assertTrue((directory / "result.json").is_file())
        self.assertNotIn("agent.interrupted", [event["type"] for event in events])

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
        self.assertEqual(context.exception.code, "invocation_failed")
        self.assertEqual(fake.aborted, ["ses_fake_0001"])
        events = [
            json.loads(line)
            for line in (root / "info/foundation/1/events.jsonl").read_text().splitlines()
        ]
        self.assertIn("agent.interrupted", [event["type"] for event in events])

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

    def test_prompt_and_observation_transport_failures_abort_once(self) -> None:
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
            transcript_messages=self._terminal_messages(),
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
