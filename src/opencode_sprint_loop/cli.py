"""Command-line interface for the Sprint Loop Controller foundation."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import secrets
import signal
import socket
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any, NoReturn, Sequence

from . import __version__
from .config import SprintConfig, load_config
from .errors import ControllerError
from .agent_runner import AgentRunner, CreatedSession, InvocationRequest, ServerValidationRequest
from .git import (
    capture_probe_snapshot,
    is_tracked_path,
    validate_preflight,
    validate_root,
    verify_probe_snapshot,
)
from .invocations import (
    InvocationPaths,
    allocate_paths,
    new_metadata,
    probe_prompt,
    probe_title,
    transcript_wrapper,
    validate_transcript_messages,
    validate_result,
    write_metadata,
    write_prompt,
    write_result,
    write_transcript,
)
from .locking import ownership_lock, persistence_lock as persistence_advisory_lock
from .paths import RuntimePaths, canonical_root, ensure_runtime_paths_safe, runtime_paths
from .safeio import open_directory
from .security import redact_diagnostic, validate_external_utf8
from .state import _rename_exchange, new_state, process_start_identity, utc_now
from .status import format_status, project_status, validate_persistence
from .transitions import observe as persist_observation
from .transitions import persist_initial, transition
from .opencode_runner import OpenCodeServerRunner, parse_server_url


class _ArgumentParser(argparse.ArgumentParser):
    """Translate parser failures into the controller's stable error contract."""

    def error(self, message: str) -> NoReturn:
        raise ControllerError("invalid_arguments", message)


class _CancellationRequested(Exception):
    """A recorded cooperative SIGINT or SIGTERM request."""

    def __init__(self, signal_number: int) -> None:
        super().__init__(signal_number)
        self.signal_number = signal_number

    @property
    def exit_status(self) -> int:
        """Return the conventional shell status for the recorded signal."""
        return 128 + self.signal_number


class _Cancellation:
    """Minimal signal-safe cancellation recorder used by orchestration."""

    def __init__(self) -> None:
        self.signal_number: int | None = None

    def record(self, signal_number: int, _frame: object) -> None:
        """Record the first request; handlers never perform I/O or persistence."""
        if self.signal_number is None:
            self.signal_number = signal_number

    def check(self) -> None:
        """Raise in orchestration after any current atomic action finishes."""
        if self.signal_number is not None:
            raise _CancellationRequested(self.signal_number)


@contextlib.contextmanager
def _cooperative_signal_handlers() -> Any:
    """Install temporary request-recording handlers only in the main thread."""
    cancellation = _Cancellation()
    if threading.current_thread() is not threading.main_thread():
        yield cancellation
        return
    previous = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }
    signal.signal(signal.SIGINT, cancellation.record)
    signal.signal(signal.SIGTERM, cancellation.record)
    try:
        yield cancellation
    finally:
        for signal_number, handler in previous.items():
            signal.signal(signal_number, handler)


def _parser() -> argparse.ArgumentParser:
    """Build the stable V1 command parser."""
    parser = _ArgumentParser(prog="sprint-loop")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--root", required=True)
    run.add_argument("--server-url", required=True)
    status = commands.add_parser("status")
    status.add_argument("--root", required=True)
    status.add_argument("--json", action="store_true")
    pause = commands.add_parser("pause")
    pause.add_argument("--root", required=True)
    resume = commands.add_parser("resume")
    resume.add_argument("--root", required=True)
    resume.add_argument("--server-url", required=True)
    stop = commands.add_parser("stop")
    stop.add_argument("--root", required=True)
    return parser


def _lock_paths(git_dir: Path) -> tuple[Path, Path]:
    """Return dedicated Git-metadata directories used as advisory lock anchors."""
    base = git_dir / "opencode-sprint-loop"
    # Git never rewrites controller-owned metadata directories during ordinary
    # branch, index, remote, or configuration operations.
    return base / "run", base / "persistence"


def _load_root_config(root_value: str) -> tuple[Path, SprintConfig, RuntimePaths, Path, Path]:
    """Validate root identity and load configuration needed by all commands."""
    root = canonical_root(root_value)
    repository = validate_root(root)
    config = load_config(root)
    paths = runtime_paths(root, config.multisprint, config.sprint)
    ensure_runtime_paths_safe(root, paths)
    run_lock, persistence_lock = _lock_paths(repository.git_dir)
    return root, config, paths, run_lock, persistence_lock


def _existing_run(paths: RuntimePaths, config: SprintConfig) -> None:
    """Reject any existing Sprint 1 state or event artifacts before preflight."""
    state_exists = paths.state.exists()
    events_exists = paths.events.exists()
    if state_exists and events_exists:
        state, _ = validate_persistence(paths, config)
        if state is None:
            raise ControllerError(
                "inconsistent_persistence", "State and event log changed during validation"
            )
        raise ControllerError(
            "run_already_exists",
            "A persisted Sprint 1 run already exists; resume policy is not implemented",
        )
    if state_exists or events_exists:
        raise ControllerError(
            "inconsistent_persistence",
            "Incomplete existing state or event artifacts require manual inspection",
        )


def _existing_run_before_preflight(
    paths: RuntimePaths, config: SprintConfig, persistence_lock: Path
) -> None:
    """Read existing artifacts under an existing persistence lock when available."""
    while True:
        try:
            with persistence_advisory_lock(persistence_lock, exclusive=False, create=False):
                _existing_run(paths, config)
            return
        except FileNotFoundError:
            _existing_run(paths, config)
            if not persistence_lock.exists():
                return


def _reject_tracked_lock_metadata(root: Path, paths: RuntimePaths) -> None:
    """Refuse to replace lock metadata that belongs to the sprint repository history."""
    if paths.lock_metadata.exists() and is_tracked_path(root, paths.lock_metadata):
        raise ControllerError(
            "inconsistent_persistence",
            f"Tracked lock metadata cannot be replaced: {paths.lock_metadata}. Remove it from repository history before running",
        )


def _write_lock_metadata(root: Path, path: Path, state: dict[str, object]) -> None:
    """Install current descriptive metadata without overwriting a racing replacement."""
    del root  # Tracking is validated immediately before this ownership-only operation.
    metadata = {
        "schema_version": 1,
        "run_id": state["run_id"],
        "pid": os.getpid(),
        "process_start": process_start_identity(os.getpid()),
        "hostname": socket.gethostname(),
        "started_at": state["created_at"],
    }
    temporary_name: str | None = None
    directory: int | None = None
    exchange_pending = False
    try:
        directory = open_directory(path.parent, create=True)
        temporary_name = f".lock-{secrets.token_hex(16)}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            payload = (json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8")
            if os.write(descriptor, payload) != len(payload):
                raise OSError("Short write while persisting lock metadata")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            existing = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            try:
                os.link(temporary_name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            except FileExistsError as error:
                raise ControllerError(
                    "persistence_failed", f"Lock metadata changed during installation: {path}"
                ) from error
        else:
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise ControllerError(
                    "persistence_failed", f"Lock metadata is unsafe to replace: {path}"
                )
            expected_identity = (existing.st_dev, existing.st_ino)
            _rename_exchange(directory, temporary_name, path.name)
            exchange_pending = True
            displaced = os.stat(temporary_name, dir_fd=directory, follow_symlinks=False)
            if (displaced.st_dev, displaced.st_ino) != expected_identity:
                _rename_exchange(directory, temporary_name, path.name)
                exchange_pending = False
                raise ControllerError(
                    "persistence_failed", f"Lock metadata changed during installation: {path}"
                )
        os.fsync(directory)
        os.unlink(temporary_name, dir_fd=directory)
        exchange_pending = False
        temporary_name = None
        os.fsync(directory)
    except OSError as error:
        raise ControllerError(
            "persistence_failed", f"Could not persist lock metadata: {path}"
        ) from error
    finally:
        if exchange_pending and temporary_name is not None and directory is not None:
            try:
                _rename_exchange(directory, temporary_name, path.name)
            except (OSError, ControllerError):
                temporary_name = None
        if temporary_name is not None and directory is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
        if directory is not None:
            os.close(directory)


def _persist_best_effort_failure(
    paths: RuntimePaths, config: SprintConfig, persistence_lock: Path
) -> None:
    """Record a safe failed transition only when the current durable pair is consistent."""
    if not paths.state.exists() or not paths.events.exists():
        return
    try:
        state, _ = validate_persistence(paths, config)
        if state is None:
            return
        if state["state"] not in {"initializing", "validating"}:
            return
        transition(
            state,
            paths.events,
            paths.state,
            persistence_lock,
            "failed",
            reason={
                "code": "internal_error",
                "message": "Controller failed after durable state became available.",
                "details": {},
            },
        )
    except (ControllerError, OSError):
        # Preserve the original failure and never overwrite inconsistent evidence.
        return


def _runtime_artifacts(root: Path, paths: RuntimePaths, invocation: InvocationPaths) -> set[str]:
    """Enumerate only the Sprint 2 runtime files permitted by post-probe Git checks."""
    candidates = (
        paths.lock_metadata,
        paths.state,
        paths.events,
        invocation.metadata,
        invocation.prompt,
        invocation.result,
        invocation.transcript,
    )
    return {
        candidate.relative_to(root).as_posix() for candidate in candidates if candidate.exists()
    }


def _terminal_metadata(
    metadata: dict[str, Any],
    *,
    status: str,
    error: ControllerError,
    transcript_status: str = "unavailable",
    transcript_truncated: bool = False,
) -> None:
    """Set a coherent terminal infrastructure outcome without fabricating a result."""
    metadata.update(
        {
            "status": status,
            "completed_at": utc_now(),
            "result": {"available": False, "status": None},
            "transcript": {"status": transcript_status, "truncated": transcript_truncated},
            "error": {"code": error.code, "message": error.message},
        }
    )


def _capture_transcript(
    session: CreatedSession, messages: list[dict[str, Any]], paths: InvocationPaths
) -> tuple[str, bool, ControllerError | None]:
    """Persist already-returned response evidence without message-list retrieval."""
    try:
        wrapper = transcript_wrapper(session.session_id, messages)
        write_transcript(paths, wrapper)
        return (
            "truncated" if wrapper["truncated"] else "complete",
            bool(wrapper["truncated"]),
            None,
        )
    except ControllerError as error:
        return "unavailable", False, error


def _abort_empty_session(runner: AgentRunner, session: CreatedSession) -> None:
    """Attempt exactly one abort for a known session before prompt submission."""
    try:
        runner.abort(session)
    except ControllerError:
        # The attempted abort is all the controller can safely do for a session
        # whose invocation state was not made durable.
        pass


def _interrupt_active_invocation(
    state: dict[str, Any],
    paths: RuntimePaths,
    persistence_lock: Path,
    runner: AgentRunner,
    session: CreatedSession,
    invocation_paths: InvocationPaths,
    metadata: dict[str, Any],
    error: ControllerError,
    transcript_evidence: tuple[str, bool] | None = None,
) -> dict[str, Any]:
    """Abort once, wait briefly for evidence, then persist one interruption event."""
    abort_acknowledged: bool | None = None
    try:
        abort_acknowledged = runner.abort(session).acknowledged
    except ControllerError:
        # A failed abort request is evidence of an unconfirmed abort, not a
        # reason to retry a non-idempotent cancellation operation.
        abort_acknowledged = None
    confirmation: str | None = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            status = runner.observe_status(session, deadline=deadline)
        except ControllerError:
            break
        if status == "idle":
            confirmation = "idle"
            break
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    if transcript_evidence is None:
        transcript_status, transcript_truncated = "unavailable", False
    else:
        transcript_status, transcript_truncated = transcript_evidence
    if confirmation is None:
        # This fixed diagnostic contains no service response data.  Preserve the
        # event's null confirmation contract while making the orphan-session
        # risk visible to the operator.
        sys.stderr.write(
            "cancellation_unconfirmed: OpenCode cancellation could not be confirmed; "
            "the session may remain active.\n"
        )
    _terminal_metadata(
        metadata,
        status="timed_out" if error.code == "invocation_timed_out" else "interrupted",
        error=error,
        transcript_status=transcript_status,
        transcript_truncated=transcript_truncated,
    )
    write_metadata(invocation_paths, metadata)
    return persist_observation(
        state,
        paths.events,
        paths.state,
        persistence_lock,
        "agent.interrupted",
        {
            "previous_state": "validating",
            "invocation_id": metadata["invocation_id"],
            "role": "auditor",
            "session_id": session.session_id,
            "interruption": {
                "code": error.code,
                "message": error.message,
                "details": {},
            },
            "abort_acknowledged": abort_acknowledged,
            "abort_confirmation": confirmation,
        },
        {"active_invocation": None},
    )


def _wait_for_synchronous_response(
    runner: AgentRunner,
    session: CreatedSession,
    request: InvocationRequest,
    deadline: float,
    cancellation: _Cancellation,
) -> Any:
    """Wait without polling while a daemon worker owns one non-idempotent POST.

    Socket I/O blocks in the worker and ``Queue.get`` blocks the controller
    thread, so idle waiting consumes negligible CPU while wall-clock timeout is
    still the configured monotonic invocation deadline.  The worker never
    receives persistence objects and a late result is intentionally ignored.
    """
    completed: queue.Queue[tuple[Any | None, BaseException | None]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            completed.put((runner.execute_prompt(session, request, deadline=deadline), None))
        except BaseException as error:  # pragma: no cover - normalized by caller
            completed.put((None, error))

    worker = threading.Thread(target=invoke, name="opencode-sync-prompt", daemon=True)
    worker.start()
    while True:
        cancellation.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ControllerError(
                "invocation_timed_out", "OpenCode invocation exceeded its configured timeout"
            )
        try:
            observation, error = completed.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue
        if error is not None:
            if isinstance(error, ControllerError):
                raise error
            raise ControllerError(
                "prompt_submission_failed", "OpenCode synchronous prompt failed"
            ) from error
        return observation


def _run(
    root_value: str,
    server_url: str,
    *,
    runner: AgentRunner | None = None,
    cancellation: _Cancellation | None = None,
) -> int:
    """Run one non-mutating, fresh Auditor execution probe then block intentionally."""
    cancellation = cancellation or _Cancellation()
    if not server_url:
        raise ControllerError("invalid_server_url", "--server-url must be non-empty")
    root, config, paths, run_lock, persistence_lock = _load_root_config(root_value)
    # Initial validation must not create controller metadata. A concurrent run
    # is rechecked under both locks after this read-only pass.
    _existing_run_before_preflight(paths, config, persistence_lock)
    _reject_tracked_lock_metadata(root, paths)
    allowed = (
        {paths.lock_metadata.relative_to(root).as_posix()}
        if paths.lock_metadata.exists()
        else set()
    )
    validate_preflight(root, config, require_clean=True, allowed_root_untracked=allowed)
    # This entire preflight is intentionally before ownership/runtime creation.
    effective_runner: AgentRunner = runner or OpenCodeServerRunner(server_url)
    validated_server = effective_runner.validate_server(
        ServerValidationRequest(root, dict(config.agents), dict(config.models))
    )
    cancellation.check()
    with ownership_lock(run_lock, blocking=False) as ownership:
        state: dict[str, Any] | None = None
        invocation_paths: InvocationPaths | None = None
        metadata: dict[str, Any] | None = None
        session: CreatedSession | None = None
        agent_started = False
        agent_completed = False
        session_creation_in_flight = False
        terminal_metadata_pending = False
        transcript_evidence: tuple[str, bool] | None = None
        try:
            # Reload after ownership so a concurrent clean configuration change
            # cannot direct this run to stale runtime paths or repository data.
            root, config, paths, post_lock_run_lock, post_lock_persistence_lock = _load_root_config(
                root_value
            )
            if post_lock_run_lock != run_lock or post_lock_persistence_lock != persistence_lock:
                raise ControllerError(
                    "internal_error", "Controller lock location changed during preflight"
                )
            ownership.ensure_current()
            # The first transition includes the post-lock revalidation and
            # metadata creation under one exclusive persistence lock so status
            # sees either no run or a complete initialized run.
            with persistence_advisory_lock(persistence_lock, exclusive=True) as persistence:
                ownership.ensure_current()
                persistence.ensure_current()
                _existing_run(paths, config)
                _reject_tracked_lock_metadata(root, paths)
                allowed = (
                    {paths.lock_metadata.relative_to(root).as_posix()}
                    if paths.lock_metadata.exists()
                    else set()
                )
                validate_preflight(root, config, require_clean=True, allowed_root_untracked=allowed)
                ownership.ensure_current()
                persistence.ensure_current()
                state = new_state(config)
                _write_lock_metadata(root, paths.lock_metadata, state)
                state = persist_initial(
                    state, paths.events, paths.state, persistence_lock, lock_held=True
                )
                persistence.ensure_current()
            ownership.ensure_current()
            state = transition(state, paths.events, paths.state, persistence_lock, "validating")
            ownership.ensure_current()
            state = persist_observation(
                state,
                paths.events,
                paths.state,
                persistence_lock,
                "server.validated",
                {"previous_state": "validating", "server_version": validated_server.version},
                {"server": {"url": validated_server.url, "version": validated_server.version}},
            )
            invocation_id = "0001-auditor"
            invocation_paths = allocate_paths(root, config.multisprint, config.sprint, 1, "auditor")
            prompt = probe_prompt(config.multisprint, config.sprint, invocation_id)
            metadata = new_metadata(
                state["run_id"],
                invocation_id,
                1,
                validated_server.version and config.models["auditor"],
                validated_server.version,
                config.repositories[0].name,
            )
            write_metadata(invocation_paths, metadata)
            write_prompt(invocation_paths, prompt)
            snapshot = capture_probe_snapshot(root, config)
            known_sessions = effective_runner.existing_session_ids()
            cancellation.check()
            request = InvocationRequest(
                invocation_id,
                1,
                config.agents["auditor"],
                config.models["auditor"],
                probe_title(config.multisprint, config.sprint, 1),
                prompt,
                root,
            )
            session_creation_in_flight = True
            session = effective_runner.create_session(request)
            session_creation_in_flight = False
            if session.session_id in known_sessions:
                raise ControllerError(
                    "non_fresh_session", "OpenCode reused an existing session identifier"
                )
            started_at = utc_now()
            # Retain this in memory before the state/event write.  If that
            # durable pair fails, the terminal metadata fallback must still
            # identify the externally created session it aborts.
            metadata.update(
                {
                    "session_id": session.session_id,
                    "status": "session_created",
                    "started_at": started_at,
                }
            )
            active = {
                "invocation_id": invocation_id,
                "sequence": 1,
                "role": "auditor",
                "model": config.models["auditor"],
                "session_id": session.session_id,
                "status": "running",
                "started_at": started_at,
            }
            state = persist_observation(
                state,
                paths.events,
                paths.state,
                persistence_lock,
                "agent.started",
                {
                    "previous_state": "validating",
                    "invocation_id": invocation_id,
                    "role": "auditor",
                    "session_id": session.session_id,
                },
                {"active_invocation": active},
            )
            agent_started = True
            metadata["status"] = "running"
            write_metadata(invocation_paths, metadata)
            cancellation.check()
            deadline = time.monotonic() + config.limits["invocation_timeout_seconds"]
            observation = _wait_for_synchronous_response(
                effective_runner, session, request, deadline, cancellation
            )
            if observation.structured_result is not None:
                validate_external_utf8(
                    observation.structured_result,
                    code="invalid_agent_result",
                    label="OpenCode structured result",
                )
            # Preserve the complete returned assistant object before reporting
            # semantic violations. Its paired user record is reconstructed from
            # the exact prompt plus the returned assistant parent identifier.
            wrapper = transcript_wrapper(session.session_id, observation.messages)
            try:
                if observation.unexpected_tool:
                    raise ControllerError(
                        "unexpected_probe_tool", "Execution probe attempted a forbidden tool"
                    )
                if observation.structured_error:
                    raise ControllerError(
                        "invalid_agent_result", "OpenCode reported structured output failure"
                    )
                if observation.terminal_assistant_error:
                    raise ControllerError(
                        "invocation_failed", "OpenCode terminal assistant message reported an error"
                    )
                if observation.structured_result is None:
                    raise ControllerError(
                        "invalid_agent_result",
                        "OpenCode terminal assistant message omitted structured output",
                    )
                validate_transcript_messages(
                    observation.messages,
                    expected_result=observation.structured_result,
                    expected_prompt=prompt,
                    expected_role=request.role,
                    expected_model=request.model,
                )
                result = validate_result(observation.structured_result)
            except ControllerError as transcript_semantic_error:
                # Safe opaque evidence is retained before the semantic probe
                # violation is reported through the interruption lifecycle.
                write_transcript(invocation_paths, wrapper)
                transcript_evidence = (
                    "truncated" if wrapper["truncated"] else "complete",
                    bool(wrapper["truncated"]),
                )
                raise transcript_semantic_error
            write_result(invocation_paths, result)
            # Once the immutable result exists, every later terminal write is
            # a documented write-ahead prefix. Preserve it rather than
            # manufacturing interruption metadata that denies the result.
            terminal_metadata_pending = True
            write_transcript(invocation_paths, wrapper)
            transcript_status = "truncated" if wrapper["truncated"] else "complete"
            transcript_truncated = bool(wrapper["truncated"])
            metadata.update(
                {
                    "status": result["status"],
                    "completed_at": utc_now(),
                    "result": {"available": True, "status": result["status"]},
                    "transcript": {
                        "status": transcript_status,
                        "truncated": transcript_truncated,
                    },
                    "error": None,
                }
            )
            # A raised write leaves immutable result/transcript artifacts as
            # the documented write-ahead prefix.  Do not later replace
            # metadata or append an agent terminal event that denies them.
            write_metadata(invocation_paths, metadata)
            state = persist_observation(
                state,
                paths.events,
                paths.state,
                persistence_lock,
                "agent.completed",
                {
                    "previous_state": "validating",
                    "invocation_id": invocation_id,
                    "role": "auditor",
                    "session_id": session.session_id,
                    "result_status": result["status"],
                },
                {"active_invocation": None},
            )
            terminal_metadata_pending = False
            agent_completed = True
            if result["status"] != "completed":
                raise ControllerError(
                    "invocation_failed", "Execution probe reported it could not complete"
                )
            verify_probe_snapshot(
                root, config, snapshot, _runtime_artifacts(root, paths, invocation_paths)
            )
            state = transition(
                state,
                paths.events,
                paths.state,
                persistence_lock,
                "blocked",
                reason={
                    "code": "execution_not_implemented",
                    "message": "OpenCode execution probe completed; Builder workflow begins in Sprint 4.",
                    "details": {},
                },
            )
        except _CancellationRequested as cancellation_error:
            error = ControllerError(
                "invocation_interrupted", "OpenCode invocation was interrupted by a signal"
            )
            if state is None:
                raise
            if terminal_metadata_pending:
                # Preserve the result/transcript-before-metadata prefix.
                pass
            elif (
                session is not None
                and agent_started
                and not agent_completed
                and invocation_paths is not None
                and metadata is not None
            ):
                try:
                    state = _interrupt_active_invocation(
                        state,
                        paths,
                        persistence_lock,
                        effective_runner,
                        session,
                        invocation_paths,
                        metadata,
                        error,
                        transcript_evidence,
                    )
                except ControllerError:
                    pass
            elif (
                session is not None
                and not agent_completed
                and invocation_paths is not None
                and metadata is not None
            ):
                _abort_empty_session(effective_runner, session)
                try:
                    _terminal_metadata(metadata, status="interrupted", error=error)
                    write_metadata(invocation_paths, metadata)
                except ControllerError:
                    pass
            elif invocation_paths is not None and metadata is not None:
                if session_creation_in_flight:
                    error = ControllerError(
                        "session_creation_ambiguous", "Session creation outcome is unknown"
                    )
                    terminal_status = "failed"
                else:
                    terminal_status = "interrupted"
                try:
                    _terminal_metadata(metadata, status=terminal_status, error=error)
                    write_metadata(invocation_paths, metadata)
                except ControllerError:
                    pass
            if not terminal_metadata_pending:
                try:
                    current, _ = validate_persistence(paths, config)
                    if current is not None and current["state"] == "validating":
                        transition(
                            current,
                            paths.events,
                            paths.state,
                            persistence_lock,
                            "blocked",
                            reason={"code": error.code, "message": error.message, "details": {}},
                        )
                except ControllerError:
                    pass
            raise cancellation_error
        except ControllerError as error:
            # A completed valid result has already emitted agent.completed;
            # never overwrite its metadata or append agent.interrupted.
            if terminal_metadata_pending:
                # Preserve the documented result/transcript write-ahead prefix
                # rather than manufacturing contradictory interruption records.
                pass
            elif (
                state is not None
                and session is not None
                and agent_started
                and not agent_completed
                and invocation_paths is not None
                and metadata is not None
            ):
                try:
                    state = _interrupt_active_invocation(
                        state,
                        paths,
                        persistence_lock,
                        effective_runner,
                        session,
                        invocation_paths,
                        metadata,
                        error,
                        transcript_evidence,
                    )
                except ControllerError:
                    pass
            elif (
                session is not None
                and not agent_completed
                and invocation_paths is not None
                and metadata is not None
            ):
                _abort_empty_session(effective_runner, session)
                try:
                    _terminal_metadata(metadata, status="failed", error=error)
                    write_metadata(invocation_paths, metadata)
                except ControllerError:
                    pass
            elif invocation_paths is not None and metadata is not None and not agent_completed:
                # A definitive rejection and a transport-ambiguous create both
                # terminalize the planned record without inventing a session ID.
                try:
                    _terminal_metadata(metadata, status="failed", error=error)
                    write_metadata(invocation_paths, metadata)
                except ControllerError:
                    pass
            if state is not None and not terminal_metadata_pending:
                try:
                    current, _ = validate_persistence(paths, config)
                    if current is not None and current["state"] == "validating":
                        transition(
                            current,
                            paths.events,
                            paths.state,
                            persistence_lock,
                            "blocked",
                            reason={"code": error.code, "message": error.message, "details": {}},
                        )
                except ControllerError:
                    pass
            raise
        except BaseException:
            _persist_best_effort_failure(paths, config, persistence_lock)
            raise
    sys.stderr.write(
        "execution_not_implemented: OpenCode execution probe completed; Builder workflow begins in Sprint 4.\n"
    )
    return 4


def _status(root_value: str, as_json: bool) -> int:
    """Print current durable status without requiring a clean worktree."""
    root, config, paths, run_lock, persistence_lock = _load_root_config(root_value)
    with persistence_advisory_lock(persistence_lock, exclusive=False) as lock:
        status = project_status(root, config, paths, run_lock)
        lock.ensure_current()
    if as_json:
        sys.stdout.write(json.dumps(status, sort_keys=True) + "\n")
    else:
        sys.stdout.write(format_status(status))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and convert expected controller failures to safe diagnostics."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
        if arguments.command == "run":
            with _cooperative_signal_handlers() as cancellation:
                try:
                    return _run(arguments.root, arguments.server_url, cancellation=cancellation)
                except _CancellationRequested as error:
                    return error.exit_status
        if arguments.command == "status":
            return _status(arguments.root, arguments.json)
        if arguments.command == "resume":
            parse_server_url(arguments.server_url)
        # Reserved controls still validate their root and configuration before
        # reporting that their Sprint 1 coordination semantics are unavailable.
        _load_root_config(arguments.root)
        raise ControllerError(
            "feature_not_implemented", f"{arguments.command} is not implemented in Sprint 2"
        )
    except ControllerError as error:
        sys.stderr.write(f"{error.code}: {redact_diagnostic(error.message)}\n")
        return 2
    except SystemExit:
        raise
    except Exception:
        sys.stderr.write(
            "internal_error: Unexpected controller failure; inspect local controller logs and retry\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
