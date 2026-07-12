"""Offline integration tests for the Sprint 1 controller foundation."""

from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Any

from opencode_sprint_loop.cli import main
from opencode_sprint_loop.config import load_config
from opencode_sprint_loop.errors import ControllerError
from opencode_sprint_loop.events import append_event, load_events, transition_event
from opencode_sprint_loop.jsonio import MAX_JSON_BYTES
from opencode_sprint_loop.locking import advisory_lock
from opencode_sprint_loop.paths import runtime_paths
from opencode_sprint_loop.state import load_state, new_state, validate_state, write_state_atomic
from opencode_sprint_loop.transitions import persist_initial, transition


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


def git_optional(path: Path, *arguments: str) -> str:
    """Run a Git inspection command that may have no result."""
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        env={"PATH": os.environ["PATH"], "LC_ALL": "C"},
    )
    return result.stdout.strip()


def repository_snapshot(path: Path) -> dict[str, object]:
    """Capture controller-relevant Git state without modifying the repository."""
    if not path.exists():
        return {"exists": False}
    index = Path(git(path, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = (path / index).resolve()
    return {
        "exists": True,
        "head": git(path, "rev-parse", "HEAD"),
        "branch": git_optional(path, "symbolic-ref", "--quiet", "--short", "HEAD"),
        "status": git(path, "status", "--porcelain=v2", "--untracked-files=all"),
        "index": index.read_bytes(),
    }


class ShortWriteHandle:
    """Wrap a binary file handle while reporting a deliberately short write."""

    def __init__(self, handle: Any) -> None:
        self.handle = handle

    def __enter__(self) -> "ShortWriteHandle":
        self.handle.__enter__()
        return self

    def __exit__(self, *arguments: object) -> object:
        return self.handle.__exit__(*arguments)

    def write(self, data: bytes) -> int:
        return self.handle.write(data[:-1])

    def flush(self) -> None:
        self.handle.flush()

    def fileno(self) -> int:
        return self.handle.fileno()


class FailingWriteHandle:
    """Wrap a text file handle and fail before any state payload is written."""

    def __init__(self, handle: Any) -> None:
        self.handle = handle

    def __enter__(self) -> "FailingWriteHandle":
        self.handle.__enter__()
        return self

    def __exit__(self, *arguments: object) -> object:
        return self.handle.__exit__(*arguments)

    def write(self, _: str) -> int:
        raise OSError("injected write failure")

    def flush(self) -> None:
        self.handle.flush()

    def fileno(self) -> int:
        return self.handle.fileno()


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

    @property
    def managed(self) -> Path:
        """Return the configured managed submodule path."""
        return self.root / "repositories" / "managed"

    def snapshot(self) -> dict[str, object]:
        """Capture Git state that read-only controller preflight must preserve."""
        return {"sprint": repository_snapshot(self.root), "managed": repository_snapshot(self.managed)}

    def make_dirty(self, repository: Path, kind: str) -> None:
        """Create one staged, unstaged, or untracked change in a fixture repository."""
        tracked = "AGENTS.md" if repository == self.root else "managed.txt"
        target = repository / ("untracked.txt" if kind == "untracked" else tracked)
        target.write_text(f"{kind} fixture change\n", encoding="utf-8")
        if kind == "staged":
            git(repository, "add", target.name)
        elif kind not in {"unstaged", "untracked"}:
            raise ValueError(f"Unsupported dirty fixture kind: {kind}")

    def set_managed_branch(self, branch: str = "wrong") -> None:
        """Move the managed worktree to a deliberately incorrect branch."""
        git(self.managed, "checkout", "-b", branch)

    def detach_managed_head(self) -> None:
        """Detach the managed worktree HEAD without changing its commit."""
        git(self.managed, "checkout", "--detach")

    def remove_managed_remote(self) -> None:
        """Remove the configured managed remote for negative preflight coverage."""
        git(self.managed, "remote", "remove", "origin")

    def uninitialize_submodule(self) -> None:
        """Remove the managed checkout while retaining the parent gitlink."""
        shutil.rmtree(self.managed)

    def make_gitlink_mismatch(self) -> None:
        """Commit locally in the submodule so its HEAD differs from the parent gitlink."""
        git(self.managed, "config", "user.email", "fixture@example.invalid")
        git(self.managed, "config", "user.name", "Fixture")
        (self.managed / "managed.txt").write_text("different HEAD\n", encoding="utf-8")
        git(self.managed, "add", "managed.txt")
        git(self.managed, "commit", "-m", "Advance managed HEAD")

    def remove_gitlink(self) -> None:
        """Remove the submodule index entry without changing the worktree checkout."""
        git(self.root, "update-index", "--force-remove", "repositories/managed")

    def unregister_submodule(self) -> None:
        """Make .gitmodules omit the otherwise valid managed gitlink."""
        (self.root / ".gitmodules").write_text("", encoding="utf-8")

    def mark_git_operation(self, repository: Path, operation: str) -> None:
        """Create Git's operation marker for safe, non-destructive preflight tests."""
        git_dir = Path(git(repository, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (repository / git_dir).resolve()
        marker = {
            "merge": "MERGE_HEAD",
            "cherry-pick": "CHERRY_PICK_HEAD",
            "revert": "REVERT_HEAD",
            "bisect": "BISECT_LOG",
        }.get(operation)
        if marker is not None:
            (git_dir / marker).write_text("fixture\n", encoding="utf-8")
        elif operation in {"rebase-apply", "rebase-merge"}:
            (git_dir / operation).mkdir()
        else:
            raise ValueError(f"Unsupported Git operation fixture: {operation}")

    def write_fixture_record(self, record: str, contents: str) -> Path:
        """Write a controlled malformed configuration, state, event, or lock record."""
        if record == "config":
            target = self.root / "sprint_config.json"
        else:
            config = load_config(self.root)
            paths = runtime_paths(self.root, config.multisprint, config.sprint)
            target = {"state": paths.state, "events": paths.events, "lock": paths.lock_metadata}[record]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        return target


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

    def variant(self) -> tuple[SprintRepositoryFixture, Path]:
        """Create an independent fixture variant for a destructive negative case."""
        fixture = SprintRepositoryFixture()
        root = fixture.create()
        self.addCleanup(fixture.close)
        return fixture, root

    def assert_preflight_preserves_snapshot(
        self, fixture: SprintRepositoryFixture, expected_code: str
    ) -> None:
        """Assert a rejected run leaves both repository snapshots untouched."""
        before = fixture.snapshot()
        code, _, stderr = self.invoke(["run", "--root", str(fixture.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn(expected_code, stderr)
        self.assertEqual(fixture.snapshot(), before)
        self.assertFalse((fixture.root / "info").exists())

    def test_help_and_version_are_successful(self) -> None:
        """The public CLI help and version paths exit successfully with stable content."""
        for arguments, expected in ((["--help"], "{run,status,pause,resume,stop}"), (["--version"], "0.1.0")):
            with self.subTest(arguments=arguments):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as context:
                        main(arguments)
                self.assertEqual(context.exception.code, 0)
                self.assertIn(expected, stdout.getvalue())
                self.assertEqual(stderr.getvalue(), "")

    def test_usage_errors_do_not_write_to_standard_output(self) -> None:
        """Argument parsing failures are non-zero diagnostics on standard error only."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as context:
                main(["run"])
        self.assertEqual(context.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("--root", stderr.getvalue())

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

    def test_unknown_schema_with_future_fields_uses_stable_reason_code(self) -> None:
        """Future schema objects fail as unsupported before V1 field validation."""
        self.fixture.write_fixture_record("config", '{"schema_version": 2, "future_field": true}\n')
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("unsupported_config_schema", stderr)

    def test_external_symlinked_configuration_is_rejected(self) -> None:
        """The controller cannot load configuration through an external symlink."""
        external = self.fixture.base / "external.json"
        external.write_text((self.root / "sprint_config.json").read_text(encoding="utf-8"), encoding="utf-8")
        config_path = self.root / "sprint_config.json"
        config_path.unlink()
        config_path.symlink_to(external)
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("invalid_config", stderr)

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
        for field in (
            "run_id", "sprint", "state", "reason", "active", "commits", "audit", "ci",
            "counters", "checklist", "last_event", "updated_at",
        ):
            self.assertIsNone(data[field], field)
        self.assertFalse((self.root / "info").exists())

    def test_git_preflight_does_not_refresh_index(self) -> None:
        """Read-only validation leaves index bytes unchanged even when stat data is stale."""
        tracked = self.root / "AGENTS.md"
        os.utime(tracked, None)
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        before = (git_dir / "index").read_bytes()
        (self.root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("dirty_sprint_repository", stderr)
        self.assertEqual((git_dir / "index").read_bytes(), before)

    def test_submodule_path_with_spaces_is_registered_correctly(self) -> None:
        """NUL-delimited .gitmodules parsing preserves a valid path containing spaces."""
        git(self.root, "mv", "repositories/managed", "repositories/managed repo")
        data = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        data["repositories"][0]["path"] = "repositories/managed repo"
        (self.root / "sprint_config.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        git(self.root, "add", ".gitmodules", "sprint_config.json", "repositories")
        git(self.root, "commit", "-m", "Move managed submodule")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 4, stderr)

    def test_post_lock_reload_uses_current_committed_configuration(self) -> None:
        """A clean configuration change before ownership uses its current runtime path."""
        from opencode_sprint_loop import cli

        original = cli.validate_preflight
        calls = 0

        def validate_then_change(root: Path, config: object, **kwargs: object) -> object:
            nonlocal calls
            result = original(root, config, **kwargs)  # type: ignore[arg-type]
            if calls == 0:
                data = json.loads((root / "sprint_config.json").read_text(encoding="utf-8"))
                data["multisprint"] = "updated"
                (root / "sprint_config.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                git(root, "add", "sprint_config.json")
                git(root, "commit", "-m", "Update sprint identity")
            calls += 1
            return result

        with patch("opencode_sprint_loop.cli.validate_preflight", side_effect=validate_then_change):
            code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 4, stderr)
        self.assertTrue((self.root / "info" / "updated" / "1" / "state.json").exists())
        self.assertFalse((self.root / "info" / "foundation").exists())

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
        self.assertIn("execution_not_implemented", stderr)

    def test_placeholder_status_matches_stable_projection(self) -> None:
        """Persisted status exposes only the documented status projection fields."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        status = json.loads(stdout)
        self.assertEqual(status["reason"], {
            "code": "execution_not_implemented",
            "message": "Sprint execution begins in a later implementation sprint.",
        })
        self.assertEqual(set(status["checklist"]), {
            "satisfied", "partial", "unsatisfied", "not_evaluated", "assessed_at",
        })

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

    def test_dirty_repository_variants_preserve_git_snapshots(self) -> None:
        """All staged, unstaged, and untracked root and managed changes remain untouched."""
        for repository_name in ("sprint", "managed"):
            for kind in ("staged", "unstaged", "untracked"):
                with self.subTest(repository=repository_name, kind=kind):
                    fixture, _ = self.variant()
                    repository = fixture.root if repository_name == "sprint" else fixture.managed
                    fixture.make_dirty(repository, kind)
                    expected = "dirty_sprint_repository" if repository_name == "sprint" else "dirty_managed_repository"
                    self.assert_preflight_preserves_snapshot(fixture, expected)

    def test_managed_branch_and_remote_failures_preserve_git_snapshots(self) -> None:
        """Managed branch, detached-head, and remote failures do not alter either repository."""
        variants = (
            ("wrong_branch", lambda fixture: fixture.set_managed_branch()),
            ("wrong_branch", lambda fixture: fixture.detach_managed_head()),
            ("missing_remote", lambda fixture: fixture.remove_managed_remote()),
        )
        for expected, mutate in variants:
            with self.subTest(expected=expected, mutate=mutate):
                fixture, _ = self.variant()
                mutate(fixture)
                self.assert_preflight_preserves_snapshot(fixture, expected)

    def test_submodule_failure_variants_preserve_git_snapshots(self) -> None:
        """Submodule shape and identity failures leave prepared fixture state unchanged."""
        variants = (
            ("uninitialized_submodule", lambda fixture: fixture.uninitialize_submodule()),
            ("submodule_sha_mismatch", lambda fixture: fixture.make_gitlink_mismatch()),
            ("invalid_submodule", lambda fixture: fixture.remove_gitlink()),
            ("invalid_submodule", lambda fixture: fixture.unregister_submodule()),
        )
        for expected, mutate in variants:
            with self.subTest(expected=expected, mutate=mutate):
                fixture, _ = self.variant()
                mutate(fixture)
                self.assert_preflight_preserves_snapshot(fixture, expected)

    def test_in_progress_git_operations_preserve_git_snapshots(self) -> None:
        """Every supported operation marker blocks root and managed repository preflight."""
        operations = ("merge", "rebase-apply", "rebase-merge", "cherry-pick", "revert", "bisect")
        for repository_name in ("sprint", "managed"):
            for operation in operations:
                with self.subTest(repository=repository_name, operation=operation):
                    fixture, _ = self.variant()
                    repository = fixture.root if repository_name == "sprint" else fixture.managed
                    fixture.mark_git_operation(repository, operation)
                    self.assert_preflight_preserves_snapshot(fixture, "git_operation_in_progress")

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

    def test_deferred_commands_preserve_complete_repository_snapshots(self) -> None:
        """Reserved controls cannot change heads, branches, indexes, or worktrees."""
        before = self.fixture.snapshot()
        for arguments in (
            ["pause", "--root", str(self.root)],
            ["resume", "--root", str(self.root), "--server-url", "opaque"],
            ["stop", "--root", str(self.root)],
        ):
            with self.subTest(command=arguments[0]):
                code, stdout, stderr = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("feature_not_implemented", stderr)
                self.assertEqual(self.fixture.snapshot(), before)
        self.assertFalse((self.root / "info").exists())

    def test_human_no_run_and_placeholder_status(self) -> None:
        """Human status remains concise before and after the placeholder run."""
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root)])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, f"Sprint root: {self.root.resolve()}\nState: no run\n")
        self.assertEqual(stderr, "")
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root)])
        self.assertEqual(code, 0, stderr)
        self.assertIn("Sprint: foundation / 1", stdout)
        self.assertIn("State: blocked", stdout)
        self.assertIn("Reason: execution_not_implemented", stdout)
        self.assertIn("Last event: run.blocked", stdout)

    def test_json_status_uses_standard_output_without_diagnostics(self) -> None:
        """Successful JSON status writes exactly one JSON document to standard output."""
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["run_exists"], False)
        self.assertEqual(stdout.count("\n"), 1)

    def test_server_url_is_opaque_and_absent_from_controller_artifacts(self) -> None:
        """Sprint 1 neither parses, emits, nor persists the supplied server URL value."""
        opaque = "not a URL user:synthetic-secret?token=synthetic-secret#fragment"
        code, stdout, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", opaque])
        self.assertEqual(code, 4)
        self.assertEqual(stdout, "")
        self.assertNotIn(opaque, stderr)
        self.assertNotIn("synthetic-secret", stderr)
        artifacts = b"".join(path.read_bytes() for path in (self.root / "info").rglob("*") if path.is_file())
        self.assertNotIn(opaque.encode(), artifacts)
        self.assertNotIn(b"synthetic-secret", artifacts)

    def test_deferred_commands_validate_required_inputs(self) -> None:
        """Reserved controls reject invalid roots and empty resume URLs before feature errors."""
        code, _, stderr = self.invoke(["pause", "--root", str(self.root / "missing")])
        self.assertEqual(code, 2)
        self.assertIn("root_not_found", stderr)
        code, _, stderr = self.invoke(["resume", "--root", str(self.root), "--server-url", ""])
        self.assertEqual(code, 2)
        self.assertIn("invalid_arguments", stderr)

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

    def test_event_sequence_corruption_fails_closed(self) -> None:
        """Duplicate, gapped, and mismatched event records cannot be interpreted as history."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        original = [json.loads(line) for line in paths.events.read_text(encoding="utf-8").splitlines()]
        for label, mutate in (
            ("duplicate", lambda records: records.__setitem__(1, {**records[1], "sequence": 1})),
            ("gap", lambda records: records.__setitem__(1, {**records[1], "sequence": 3})),
            ("run_id", lambda records: records.__setitem__(1, {**records[1], "run_id": "00000000-0000-4000-8000-000000000000"})),
        ):
            with self.subTest(label=label):
                records = [dict(event) for event in original]
                mutate(records)
                paths.events.write_text("".join(json.dumps(event) + "\n" for event in records), encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_events(paths.events)
                self.assertEqual(context.exception.code, "corrupt_event_log")
        paths.events.write_text("".join(json.dumps(event) + "\n" for event in original[:2]), encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_event_log", stderr)

    def test_event_short_write_preserves_existing_bytes_and_fails_explicitly(self) -> None:
        """A detected short append never rewrites valid history and leaves corruption visible."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = load_state(paths.state)
        before = paths.events.read_bytes()
        event = transition_event(state, "state.entered", "blocked", {"previous_state": "blocked"})
        original_open = Path.open

        def short_open(path: Path, *arguments: object, **keywords: object) -> Any:
            handle = original_open(path, *arguments, **keywords)
            return ShortWriteHandle(handle) if path == paths.events else handle

        with patch("opencode_sprint_loop.events.Path.open", autospec=True, side_effect=short_open):
            with self.assertRaises(ControllerError) as context:
                append_event(paths.events, event)
        self.assertEqual(context.exception.code, "persistence_failed")
        after = paths.events.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertGreater(len(after), len(before))
        with self.assertRaises(ControllerError) as context:
            load_events(paths.events)
        self.assertEqual(context.exception.code, "corrupt_event_log")

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

    def test_atomic_state_write_and_sync_failures_leave_complete_snapshots(self) -> None:
        """Write, pre-replace sync, and post-replace sync failures never truncate state."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        original = new_state(config)
        write_state_atomic(paths.state, original)
        replacement = dict(original)
        replacement["state"] = "blocked"
        replacement["reason"] = {"code": "test", "message": "test", "details": {}}

        with patch("opencode_sprint_loop.state.tempfile.mkstemp", side_effect=OSError("injected")):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "initializing")

        real_fdopen = os.fdopen

        def fail_write(descriptor: int, *arguments: object, **keywords: object) -> FailingWriteHandle:
            return FailingWriteHandle(real_fdopen(descriptor, *arguments, **keywords))

        with patch("opencode_sprint_loop.state.os.fdopen", side_effect=fail_write):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "initializing")

        with patch("opencode_sprint_loop.state.os.fsync", side_effect=OSError("injected")):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "initializing")

        with patch("opencode_sprint_loop.state.os.fsync", side_effect=[None, OSError("injected")]):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "blocked")

    def test_interrupted_transition_leaves_event_ahead_of_state_inconsistent(self) -> None:
        """An interruption after event sync is detected and is never automatically replayed."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = new_state(config)
        persistence_lock = self.root / ".git" / "opencode-sprint-loop" / "persistence.lock"
        with patch("opencode_sprint_loop.transitions.write_state_atomic", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                persist_initial(state, paths.events, paths.state, persistence_lock)
        self.assertFalse(paths.state.exists())
        self.assertEqual(len(load_events(paths.events)), 1)
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)

    def test_transition_guards_preserve_durable_pair_on_invalid_requests(self) -> None:
        """Missing reasons and unsupported destinations cannot append partial transitions."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        persistence_lock = self.root / ".git" / "opencode-sprint-loop" / "persistence.lock"
        state = persist_initial(new_state(config), paths.events, paths.state, persistence_lock)
        state = transition(state, paths.events, paths.state, persistence_lock, "validating")
        before_state = paths.state.read_bytes()
        before_events = paths.events.read_bytes()
        for destination, reason in (("blocked", None), ("finished", None), ("unknown", None)):
            with self.subTest(destination=destination):
                with self.assertRaises(ControllerError) as context:
                    transition(state, paths.events, paths.state, persistence_lock, destination, reason=reason)
                self.assertEqual(context.exception.code, "internal_error")
                self.assertEqual(paths.state.read_bytes(), before_state)
                self.assertEqual(paths.events.read_bytes(), before_events)

    def test_json_input_size_bounds_fail_closed(self) -> None:
        """Configuration, state, and event inputs larger than one MiB are rejected safely."""
        oversized = "x" * (MAX_JSON_BYTES + 1)
        (self.root / "sprint_config.json").write_text(oversized, encoding="utf-8")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("invalid_config", stderr)
        self.assertFalse((self.root / "info").exists())

        fixture, root = self.variant()
        self.assertEqual(self.invoke(["run", "--root", str(root), "--server-url", "opaque"])[0], 4)
        paths = runtime_paths(root, "foundation", 1)
        paths.state.write_text(oversized, encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_state", stderr)

        self.assertEqual(self.invoke(["run", "--root", str(fixture.root), "--server-url", "opaque"])[0], 2)
        paths.state.unlink()
        write_state_atomic(paths.state, new_state(load_config(root)))
        paths.events.write_text(oversized + "\n", encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_event_log", stderr)

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

    def test_status_reports_live_ownership_without_trusting_persisted_intent(self) -> None:
        """Status remains readable and derives activity from the held ownership lock."""
        self.assertEqual(self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4)
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        run_lock = git_dir / "opencode-sprint-loop" / "run.lock"
        with advisory_lock(run_lock, exclusive=True):
            code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
            self.assertEqual(code, 0, stderr)
            self.assertTrue(json.loads(stdout)["process_running"])
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertFalse(json.loads(stdout)["process_running"])

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

    def test_state_validation_rejects_contract_invalid_values(self) -> None:
        """Persisted state fails closed for invalid fields and unknown schema members."""
        config = load_config(self.root)
        cases: list[tuple[list[str], object]] = [
            (["process", "pid"], True),
            (["process", "process_start"], ""),
            (["ci", "attempt"], -1),
            (["terminal_result"], {}),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                state = new_state(config)
                target: object = state
                for key in path[:-1]:
                    target = target[key]  # type: ignore[index]
                target[path[-1]] = value  # type: ignore[index]
                with self.assertRaises(ControllerError) as context:
                    validate_state(state)
                self.assertEqual(context.exception.code, "corrupt_state")

    def test_state_accepts_additive_schema_version_one_fields(self) -> None:
        """Later Sprint 1-compatible fields do not invalidate the stable state schema."""
        state = new_state(load_config(self.root))
        state["future"] = {"value": True}
        state["server"]["future"] = "reserved"
        state["server"]["url"] = "http://later.example.invalid"
        state["active_invocation"] = {"role": "builder"}
        state["ci"]["checks"] = [{"name": "later"}]
        validate_state(state)

    def test_unknown_state_schema_precedes_version_one_shape_validation(self) -> None:
        """Future state schemas receive the stable unsupported-schema reason."""
        with self.assertRaises(ControllerError) as context:
            validate_state({"schema_version": 2, "future": True})
        self.assertEqual(context.exception.code, "unsupported_state_schema")

    def test_invalid_state_serialization_does_not_create_runtime_paths(self) -> None:
        """Serialization failures occur before the target directory or temporary file exists."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = new_state(config)
        state["state"] = "blocked"
        state["reason"] = {"code": "test", "message": "test", "details": {"bad": object()}}
        with self.assertRaises(ControllerError) as context:
            write_state_atomic(paths.state, state)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertFalse(paths.info_dir.exists())

    def test_invalid_event_is_not_appended(self) -> None:
        """The event API validates its envelope before creating an append target."""
        path = self.root / "info" / "events.jsonl"
        with self.assertRaises(ControllerError) as context:
            append_event(path, {"invalid": True})
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertFalse(path.exists())

    def test_transition_allows_best_effort_failure_from_active_state(self) -> None:
        """Active Sprint 1 states can record a failure when persistence remains available."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = new_state(config)
        persistence_lock = self.root / ".git" / "opencode-sprint-loop" / "persistence.lock"
        state = persist_initial(state, paths.events, paths.state, persistence_lock)
        transition(
            state,
            paths.events,
            paths.state,
            persistence_lock,
            "failed",
            reason={"code": "internal_error", "message": "test failure", "details": {}},
        )
        self.assertEqual(load_state(paths.state)["state"], "failed")
        self.assertEqual(load_events(paths.events)[-1]["state"], "failed")

    def test_run_persists_best_effort_failure_after_initial_state(self) -> None:
        """An internal transition error records failed when the prior pair is durable."""
        from opencode_sprint_loop import transitions

        original = transitions.append_event

        def fail_validating(path: Path, event: dict[str, object]) -> None:
            if event["state"] == "validating":
                raise OSError("injected")
            original(path, event)  # type: ignore[arg-type]

        with patch("opencode_sprint_loop.transitions.append_event", side_effect=fail_validating):
            code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("internal_error", stderr)
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = load_state(paths.state)
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["reason"]["code"], "internal_error")

    def test_json_input_directory_reports_stable_error(self) -> None:
        """Malformed filesystem inputs do not cause a CLI traceback."""
        config_path = self.root / "sprint_config.json"
        config_path.unlink()
        config_path.mkdir()
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("invalid_config", stderr)
        self.assertNotIn("Traceback", stderr)
