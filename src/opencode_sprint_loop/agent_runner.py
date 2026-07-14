"""OpenCode-independent contracts used by the Sprint 2 execution probe."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ServerValidationRequest:
    """Inputs for a read-only server capability check."""

    sprint_root: Path
    agents: dict[str, str]
    models: dict[str, str]


@dataclass(frozen=True, slots=True)
class ValidatedServer:
    """Credential-free identity returned after server validation."""

    url: str
    version: str


@dataclass(frozen=True, slots=True)
class InvocationRequest:
    """Controller-owned immutable inputs for one fresh execution probe."""

    invocation_id: str
    sequence: int
    role: str
    model: str
    title: str
    prompt: str
    sprint_root: Path


@dataclass(frozen=True, slots=True)
class CreatedSession:
    """Validated identity of one newly created top-level OpenCode session."""

    session_id: str
    title: str
    directory: Path
    permissions: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class InvocationObservation:
    """One synchronous terminal response and its normalized message evidence."""

    status: str | None
    messages: list[dict[str, Any]]
    structured_result: Any | None
    structured_error: bool
    unexpected_tool: bool
    terminal_assistant: bool = False
    terminal_assistant_error: bool = False


@dataclass(frozen=True, slots=True)
class AbortObservation:
    """Result of a single best-effort session abort request."""

    acknowledged: bool


class AgentRunner(Protocol):
    """Synchronous runner boundary; methods never expose transport objects."""

    def validate_server(self, request: ServerValidationRequest) -> ValidatedServer:
        """Validate the configured server without creating a session."""

    def existing_session_ids(self) -> set[str]:
        """Return a bounded snapshot of default-workspace session identifiers."""

    def create_session(self, request: InvocationRequest) -> CreatedSession:
        """Create exactly one fresh, non-mutating session without submitting work."""

    def execute_prompt(
        self, session: CreatedSession, request: InvocationRequest, *, deadline: float | None = None
    ) -> InvocationObservation:
        """Synchronously submit one prompt and return its terminal assistant evidence."""

    def observe_status(
        self, session: CreatedSession, *, deadline: float | None = None
    ) -> str | None:
        """Return one bounded status observation solely for abort confirmation."""

    def abort(self, session: CreatedSession) -> AbortObservation:
        """Request one best-effort cooperative abort for an active session."""


class FakeAgentRunner:
    """Deterministic scripted ``AgentRunner`` for offline controller tests."""

    def __init__(
        self,
        validated: ValidatedServer,
        *,
        session_ids: list[str] | None = None,
        observations: list[InvocationObservation | Exception] | None = None,
        transcript_messages: list[dict[str, Any]] | None = None,
        abort_acknowledged: bool = True,
        validation_error: Exception | None = None,
        session_snapshot_error: Exception | None = None,
        create_error: Exception | None = None,
        submit_error: Exception | None = None,
        abort_error: Exception | None = None,
    ) -> None:
        """Configure deterministic values or failures for every runner operation."""
        self.validated = validated
        self.session_ids = list(session_ids or ["ses_fake_0001"])
        self.observations = list(observations or [])
        self.transcript_messages = list(transcript_messages or [])
        self.abort_acknowledged = abort_acknowledged
        self.validation_error = validation_error
        self.session_snapshot_error = session_snapshot_error
        self.create_error = create_error
        self.submit_error = submit_error
        self.abort_error = abort_error
        self.created: list[CreatedSession] = []
        self.submitted: list[str] = []
        self.aborted: list[str] = []
        self.observation_deadlines: list[float | None] = []
        self.preexisting: set[str] = set()

    def validate_server(self, request: ServerValidationRequest) -> ValidatedServer:
        """Return the scripted validation result or a scripted controller error."""
        del request
        if self.validation_error is not None:
            raise self.validation_error
        return self.validated

    def existing_session_ids(self) -> set[str]:
        """Return the scripted pre-creation session snapshot."""
        if self.session_snapshot_error is not None:
            raise self.session_snapshot_error
        return set(self.preexisting)

    def create_session(self, request: InvocationRequest) -> CreatedSession:
        """Create the next scripted session and retain its semantic request fields."""
        if self.create_error is not None:
            raise self.create_error
        if not self.session_ids:
            from .errors import ControllerError

            raise ControllerError(
                "session_creation_failed", "Fake session creation was not scripted"
            )
        session = CreatedSession(
            self.session_ids.pop(0),
            request.title,
            request.sprint_root,
            ({"permission": "*", "pattern": "*", "action": "deny"},),
        )
        self.created.append(session)
        return session

    def execute_prompt(
        self, session: CreatedSession, request: InvocationRequest, *, deadline: float | None = None
    ) -> InvocationObservation:
        """Return the next terminal scripted response without transport I/O."""
        self.submitted.append(session.session_id)
        del request, deadline
        if self.submit_error is not None:
            raise self.submit_error
        if self.observations:
            observation = self.observations.pop(0)
            if isinstance(observation, Exception):
                raise observation
            if self.transcript_messages:
                messages = deepcopy(self.transcript_messages)
                if observation.structured_result is not None:
                    for message in messages:
                        info = message.get("info")
                        if isinstance(info, dict):
                            for key in ("structured", "structured_output"):
                                if key in info:
                                    info[key] = observation.structured_result
                        for part in message.get("parts", []):
                            if isinstance(part, dict) and part.get("type") in {
                                "structured_output",
                                "json_schema",
                            }:
                                part["value"] = observation.structured_result
                return replace(observation, messages=messages)
            return observation
        from .errors import ControllerError

        raise ControllerError("invocation_timed_out", "Fake synchronous response was not scripted")

    def observe_status(
        self, session: CreatedSession, *, deadline: float | None = None
    ) -> str | None:
        """Return scripted status only for bounded abort confirmation."""
        del session
        self.observation_deadlines.append(deadline)
        if self.observations:
            observation = self.observations.pop(0)
            if isinstance(observation, Exception):
                raise observation
            return observation.status
        return "idle"

    def abort(self, session: CreatedSession) -> AbortObservation:
        """Record a scripted abort acknowledgement."""
        self.aborted.append(session.session_id)
        if self.abort_error is not None:
            raise self.abort_error
        return AbortObservation(self.abort_acknowledged)
