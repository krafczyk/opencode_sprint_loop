"""Offline integration tests for the Sprint 1 controller foundation."""

from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from opencode_sprint_loop.cli import main
from opencode_sprint_loop.config import load_config
from opencode_sprint_loop.errors import ControllerError
from opencode_sprint_loop.events import load_events
from opencode_sprint_loop.locking import advisory_lock
from opencode_sprint_loop.paths import runtime_paths
from opencode_sprint_loop.state import load_state, new_state, write_state_atomic


def hold_lock(path: str, ready: multiprocessing.synchronize.Event) -> None:
    """Hold an ownership lock in a separate process for contention tests."""
    with advisory_lock(Path(path), exclusive=True):
        ready.set()
        __import__("time").sleep(1)


def git(path: Path, *arguments: str) -> str:
    """Run Git in a temporary fixture repository."""
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        env={"PATH": __import__("os").environ["PATH"], "LC_ALL": "C"},
    )
    if result.returncode:
        raise AssertionError(f"git {' '.join(arguments)} failed: {result.stderr}")
    return result.stdout.strip()


def write_config(root: Path, *, schema_version: int = 1) -> None:
    """Write one valid Sprint 1 configuration fixture."""
    data = {
        "schema_version": schema_version,
        "multisprint": "foundation",
        "sprint": 1,
        "repositories": [{"name": "managed", "path": "repositories/managed", "branch": "main", "remote": "origin"}],
        "documents": {
            "multisprint_spec": "docs/foundation/multisprint_spec.md",
            "sprint_spec": "docs/foundation/1/sprint_spec.md",
            "sprint_checklist": "docs/foundation/1/sprint_checklist.md",
        },
        "agents": {"builder": "builder", "auditor": "auditor", "ci_fixer": "ci-fixer"},
        "models": {"builder": "test/medium", "auditor": "test/strong", "ci_fixer": "test/medium"},
        "pre_ci_audit": {"enabled": True, "max_rounds": 2},
        "limits": {
            "max_implementation_cycles": 2,
            "max_ci_fix_attempts": 2,
            "invocation_timeout_seconds": 60,
            "server_unavailable_grace_seconds": 30,
        },
        "ci": {"provider": "github", "poll_interval_seconds": 30, "allow_skipped": True, "allow_neutral": True, "zero_checks": "error"},
    }
    (root / "sprint_config.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class SprintRepositoryFixture:
    """A real temporary sprint repository containing one Git submodule."""

    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.remote = self.base / "managed-remote.git"
        self.managed_seed = self.base / "managed-seed"
        self.root = self.base / "sprint"

    def create(self) -> Path:
        """Create and commit a clean valid fixture."""
        git(self.base, "init", "--bare", str(self.remote))
        self.managed_seed.mkdir()
        git(self.managed_seed, "init", "--initial-branch=main")
        git(self.managed_seed, "config", "user.email", "fixture@example.invalid")
        git(self.managed_seed, "config", "user.name", "Fixture")
        (self.managed_seed / "managed.txt").write_text("baseline\n", encoding="utf-8")
        git(self.managed_seed, "add", "managed.txt")
        git(self.managed_seed, "commit", "-m", "Initial managed commit")
        git(self.managed_seed, "remote", "add", "origin", str(self.remote))
        git(self.managed_seed, "push", "-u", "origin", "main")

        self.root.mkdir()
        git(self.root, "init", "--initial-branch=main")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        git(self.root, "config", "user.name", "Fixture")
        (self.root / "AGENTS.md").write_text("fixture instructions\n", encoding="utf-8")
        for document in (
            self.root / "docs" / "foundation" / "multisprint_spec.md",
            self.root / "docs" / "foundation" / "1" / "sprint_spec.md",
            self.root / "docs" / "foundation" / "1" / "sprint_checklist.md",
        ):
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text("fixture document\n", encoding="utf-8")
        for name in ("builder", "auditor", "ci-fixer"):
            agent = self.root / ".opencode" / "agents" / f"{name}.md"
            agent.parent.mkdir(parents=True, exist_ok=True)
            agent.write_text(f"{name} instructions\n", encoding="utf-8")
        write_config(self.root)
        git(self.root, "add", "AGENTS.md", "docs", ".opencode", "sprint_config.json")
        git(self.root, "commit", "-m", "Add sprint inputs")
        git(self.root, "-c", "protocol.file.allow=always", "submodule", "add", "-b", "main", str(self.remote), "repositories/managed")
        git(self.root, "commit", "-m", "Add managed submodule")
        return self.root

    def close(self) -> None:
        """Remove all temporary repositories."""
        self.temporary.cleanup()


class FoundationTests(unittest.TestCase):
    """Test Sprint 1 behavior using only local Git repositories."""

    def setUp(self) -> None:
        self.fixture = SprintRepositoryFixture()
        self.root = self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.close()

    def invoke(self, arguments: list[str]) -> tuple[int, str, str]:
        """Invoke the CLI in-process while capturing both output streams."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_configuration_loads(self) -> None:
        """A complete fixture configuration validates into typed fields."""
        config = load_config(self.root)
        self.assertEqual(config.multisprint, "foundation")
        self.assertEqual(config.repository.path, self.root / "repositories" / "managed")

    def test_duplicate_config_keys_are_rejected(self) -> None:
        """Duplicate JSON keys do not silently overwrite configuration."""
        (self.root / "sprint_config.json").write_text('{"schema_version": 1, "schema_version": 1}\n', encoding="utf-8")
        with self.assertRaises(ControllerError) as context:
            load_config(self.root)
        self.assertEqual(context.exception.code, "invalid_config")

    def test_unknown_schema_fails_without_runtime_artifacts(self) -> None:
        """Unsupported configuration schema fails before controller artifacts exist."""
        write_config(self.root, schema_version=2)
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("unsupported_config_schema", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_float_schema_version_is_rejected(self) -> None:
        """A numerically equal float is not an integer schema version."""
        data = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        data["schema_version"] = 1.0
        (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ControllerError) as context:
            load_config(self.root)
        self.assertEqual(context.exception.code, "unsupported_config_schema")

    def test_unknown_config_field_is_rejected(self) -> None:
        """Schema version one does not silently accept unknown fields."""
        data = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        data["unknown"] = True
        (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ControllerError) as context:
            load_config(self.root)
        self.assertEqual(context.exception.code, "invalid_config")

    def test_no_run_json_status_is_read_only(self) -> None:
        """Status returns a stable no-run projection without worktree artifacts."""
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        data = json.loads(stdout)
        self.assertFalse(data["run_exists"])
        self.assertFalse((self.root / "info").exists())

    def test_valid_run_persists_placeholder_state(self) -> None:
        """A valid run creates the intentional three-event blocked placeholder."""
        code, stdout, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque-value"])
        self.assertEqual(code, 4, (stdout, stderr))
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = load_state(paths.state)
        events = load_events(paths.events)
        self.assertEqual(state["state"], "blocked")
        self.assertEqual(state["reason"]["code"], "execution_not_implemented")
        self.assertEqual([event["type"] for event in events], ["run.started", "state.entered", "run.blocked"])
        self.assertIsNone(state["server"]["url"])

    def test_existing_run_precedes_dirty_worktree_error(self) -> None:
        """Runtime artifacts cause run_already_exists even though they dirty the worktree."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("run_already_exists", stderr)

    def test_dirty_managed_repository_fails_without_runtime_artifacts(self) -> None:
        """Managed changes fail preflight before controller state is created."""
        managed = self.root / "repositories" / "managed"
        (managed / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("dirty_managed_repository", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_dirty_sprint_repository_fails_without_runtime_artifacts(self) -> None:
        """Sprint worktree changes fail before state creation and Git mutation."""
        (self.root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        before = git(self.root, "status", "--porcelain=v2")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("dirty_sprint_repository", stderr)
        self.assertEqual(git(self.root, "status", "--porcelain=v2"), before)
        self.assertFalse((self.root / "info").exists())

    def test_staged_sprint_repository_change_fails(self) -> None:
        """The clean preflight rejects staged work as well as untracked work."""
        (self.root / "AGENTS.md").write_text("changed\n", encoding="utf-8")
        git(self.root, "add", "AGENTS.md")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("dirty_sprint_repository", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_wrong_managed_branch_fails(self) -> None:
        """The configured symbolic managed branch is enforced."""
        managed = self.root / "repositories" / "managed"
        git(managed, "checkout", "-b", "other")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("wrong_branch", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_missing_managed_remote_fails(self) -> None:
        """The configured managed remote is required before a run starts."""
        managed = self.root / "repositories" / "managed"
        git(managed, "remote", "remove", "origin")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("missing_remote", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_deferred_commands_do_not_mutate(self) -> None:
        """Reserved controls return clear errors without creating runtime state."""
        for arguments in (
            ["pause", "--root", str(self.root)],
            ["resume", "--root", str(self.root), "--server-url", "opaque"],
            ["stop", "--root", str(self.root)],
        ):
            code, _, stderr = self.invoke(list(arguments))
            self.assertEqual(code, 2)
            self.assertIn("feature_not_implemented", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_status_rejects_inconsistent_persistence(self) -> None:
        """An event log ahead of state fails closed rather than guessing."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        with paths.events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"schema_version": 1, "sequence": 4, "timestamp": "2026-01-01T00:00:00Z", "run_id": load_state(paths.state)["run_id"], "type": "state.entered", "state": "blocked", "payload": {}}) + "\n")
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)

    def test_status_rejects_partial_event_line(self) -> None:
        """Partial JSONL is corruption and is never automatically truncated."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        with paths.events.open("ab") as handle:
            handle.write(b'{"partial"')
        before = paths.events.read_bytes()
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_event_log", stderr)
        self.assertEqual(paths.events.read_bytes(), before)

    def test_run_rejects_corrupt_existing_persistence(self) -> None:
        """Existing artifacts are validated before the controller reports run exists."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        state["audit"] = {}
        paths.state.write_text(json.dumps(state), encoding="utf-8")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_state", stderr)

    def test_status_rejects_invalid_nested_state(self) -> None:
        """Malformed nested state is an actionable controller error, not KeyError."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        state["audit"] = {}
        paths.state.write_text(json.dumps(state), encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_state", stderr)

    def test_atomic_state_replacement_keeps_valid_json(self) -> None:
        """Atomic state replacement leaves a complete readable snapshot."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = new_state(config)
        write_state_atomic(paths.state, state)
        replacement = dict(state)
        replacement["state"] = "blocked"
        replacement["reason"] = {"code": "test", "message": "test", "details": {}}
        write_state_atomic(paths.state, replacement)
        self.assertEqual(load_state(paths.state)["state"], "blocked")

    def test_atomic_state_replace_failure_preserves_previous_state(self) -> None:
        """A replace failure leaves the prior complete state readable."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        original = new_state(config)
        write_state_atomic(paths.state, original)
        replacement = dict(original)
        replacement["state"] = "blocked"
        replacement["reason"] = {"code": "test", "message": "test", "details": {}}
        with patch("opencode_sprint_loop.state.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "initializing")

    def test_ownership_lock_rejects_concurrent_attempt(self) -> None:
        """A held non-worktree ownership lock prevents a second run."""
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        run_lock = git_dir / "opencode-sprint-loop" / "run.lock"
        with advisory_lock(run_lock, exclusive=True):
            code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("run_already_active", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_separate_process_ownership_lock_rejects_run(self) -> None:
        """Ownership is enforced across controller processes, not only threads."""
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        run_lock = git_dir / "opencode-sprint-loop" / "run.lock"
        ready = multiprocessing.Event()
        process = multiprocessing.Process(target=hold_lock, args=(str(run_lock), ready))
        process.start()
        try:
            self.assertTrue(ready.wait(timeout=5))
            code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
            self.assertEqual(code, 2)
            self.assertIn("run_already_active", stderr)
        finally:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
        self.assertEqual(process.exitcode, 0)

    def test_stale_lock_metadata_does_not_block_first_run(self) -> None:
        """A descriptive stale lock file is replaced after real ownership is acquired."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        paths.lock_metadata.parent.mkdir(parents=True)
        paths.lock_metadata.write_text("not-json\n", encoding="utf-8")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 4, stderr)
        metadata = json.loads(paths.lock_metadata.read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema_version"], 1)

    def test_status_rejects_state_for_different_sprint(self) -> None:
        """Persisted state cannot be projected for a different configured sprint."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        state["sprint"] = 2
        paths.state.write_text(json.dumps(state), encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)
