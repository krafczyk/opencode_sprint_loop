"""Offline integration tests for the Sprint 1 controller foundation."""

from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import queue
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Any

from opencode_sprint_loop import __version__
from opencode_sprint_loop.cli import _lock_paths, main
from opencode_sprint_loop.config import load_config
from opencode_sprint_loop.errors import ControllerError
from opencode_sprint_loop.events import append_event, load_events, transition_event
from opencode_sprint_loop.jsonio import MAX_JSON_BYTES
from opencode_sprint_loop.locking import advisory_lock, ownership_lock
from opencode_sprint_loop.paths import runtime_paths
from opencode_sprint_loop.security import redact_diagnostic
from opencode_sprint_loop.state import load_state, new_state, validate_state, write_state_atomic
from opencode_sprint_loop.transitions import persist_initial, transition


def hold_lock(path: str, ready: multiprocessing.synchronize.Event) -> None:
    """Hold an ownership lock in a separate process for contention tests."""
    with advisory_lock(Path(path), exclusive=True):
        ready.set()
        __import__("time").sleep(1)


def invoke_in_child(
    arguments: list[str],
    started: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue[tuple[int, str, str]],
) -> None:
    """Invoke the CLI in a separate process and return captured streams."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    started.set()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(arguments)
    results.put((code, stdout.getvalue(), stderr.getvalue()))


def run_paused_after_validating(
    arguments: list[str],
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue[int],
) -> None:
    """Run a real controller process while retaining ownership after validation."""
    from opencode_sprint_loop import cli

    original_transition = cli.transition

    def pause_after_validating(*transition_arguments: object, **keywords: object) -> object:
        result = original_transition(*transition_arguments, **keywords)  # type: ignore[arg-type]
        destination = keywords.get("destination", transition_arguments[4])
        if destination == "validating":
            ready.set()
            if not release.wait(timeout=10):
                raise RuntimeError("timed out waiting to complete controller run")
        return result

    with patch("opencode_sprint_loop.cli.transition", side_effect=pause_after_validating):
        results.put(main(arguments))


def pause_transition_in_child(
    state: dict[str, Any],
    events_path: Path,
    state_path: Path,
    persistence_lock: Path,
    state_write_started: multiprocessing.synchronize.Event,
    release_state_write: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue[str | None],
) -> None:
    """Pause a transition after event sync to exercise concurrent status safely."""
    from opencode_sprint_loop import transitions

    real_write_state = transitions.write_state_atomic_at

    def pause_before_state_replace(*arguments: object) -> None:
        state_write_started.set()
        if not release_state_write.wait(timeout=10):
            raise RuntimeError("timed out waiting to complete transition")
        real_write_state(*arguments)  # type: ignore[arg-type]

    try:
        with patch(
            "opencode_sprint_loop.transitions.write_state_atomic_at",
            side_effect=pause_before_state_replace,
        ):
            transition(state, events_path, state_path, persistence_lock, "validating")
    except Exception as error:
        results.put(repr(error))
    else:
        results.put(None)


def pause_initial_transition_in_child(
    state: dict[str, Any],
    events_path: Path,
    state_path: Path,
    persistence_lock: Path,
    state_write_started: multiprocessing.synchronize.Event,
    release_state_write: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue[str | None],
) -> None:
    """Pause the first state replacement while retaining the exclusive persistence lock."""
    from opencode_sprint_loop import transitions

    real_write_state = transitions.write_state_atomic_at

    def pause_before_state_replace(*arguments: object) -> None:
        state_write_started.set()
        if not release_state_write.wait(timeout=10):
            raise RuntimeError("timed out waiting to complete initial transition")
        real_write_state(*arguments)  # type: ignore[arg-type]

    try:
        with patch(
            "opencode_sprint_loop.transitions.write_state_atomic_at",
            side_effect=pause_before_state_replace,
        ):
            persist_initial(state, events_path, state_path, persistence_lock)
    except Exception as error:
        results.put(repr(error))
    else:
        results.put(None)


def read_state_until_stopped(
    path: Path,
    stop: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue[str | None],
) -> None:
    """Continuously validate atomic state snapshots from a separate process."""
    try:
        while not stop.is_set():
            load_state(path)
    except Exception as error:
        results.put(repr(error))
    else:
        results.put(None)


def git(path: Path, *arguments: str) -> str:
    """Run Git in a temporary fixture repository."""
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={"PATH": __import__("os").environ["PATH"], "LC_ALL": "C"},
    )
    if result.returncode:
        raise AssertionError(f"git {' '.join(arguments)} failed: {result.stderr}")
    return result.stdout.strip()


def git_optional(path: Path, *arguments: str) -> str:
    """Run a Git inspection command that may have no result."""
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={"PATH": os.environ["PATH"], "LC_ALL": "C"},
    )
    return result.stdout.strip()


def controller_locks(root: Path) -> tuple[Path, Path]:
    """Return the controller's actual stable advisory lock anchors for a fixture."""
    git_dir = Path(git(root, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve()
    return _lock_paths(git_dir)


def controller_metadata_directory(root: Path) -> Path:
    """Return the controller-owned Git metadata directory for a fixture."""
    return controller_locks(root)[0].parent


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


def write_config(root: Path, *, schema_version: int = 1) -> None:
    """Write one valid Sprint 1 configuration fixture."""
    data = {
        "schema_version": schema_version,
        "multisprint": "foundation",
        "sprint": 1,
        "repositories": [
            {
                "name": "managed",
                "path": "repositories/managed",
                "branch": "main",
                "remote": "origin",
            }
        ],
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
        "ci": {
            "provider": "github",
            "poll_interval_seconds": 30,
            "allow_skipped": True,
            "allow_neutral": True,
            "zero_checks": "error",
        },
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
        git(
            self.root,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-b",
            "main",
            str(self.remote),
            "repositories/managed",
        )
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
        return {
            "sprint": repository_snapshot(self.root),
            "managed": repository_snapshot(self.managed),
        }

    def make_dirty(self, repository: Path, kind: str) -> None:
        """Create one staged, unstaged, or untracked change in a fixture repository."""
        tracked = "AGENTS.md" if repository == self.root else "managed.txt"
        if kind == "untracked_directory":
            target = repository / "untracked-directory" / "nested.txt"
            target.parent.mkdir()
        else:
            target = repository / ("untracked.txt" if kind == "untracked" else tracked)
        target.write_text(f"{kind} fixture change\n", encoding="utf-8")
        if kind == "staged":
            git(repository, "add", target.name)
        elif kind not in {"unstaged", "untracked", "untracked_directory"}:
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
            target = {"state": paths.state, "events": paths.events, "lock": paths.lock_metadata}[
                record
            ]
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
        code, _, stderr = self.invoke(
            ["run", "--root", str(fixture.root), "--server-url", "opaque"]
        )
        self.assertEqual(code, 2)
        self.assertIn(expected_code, stderr)
        self.assertEqual(fixture.snapshot(), before)
        self.assertFalse((fixture.root / "info").exists())
        self.assertFalse(controller_metadata_directory(fixture.root).exists())

    def test_help_and_version_are_successful(self) -> None:
        """The public CLI help and version paths exit successfully with stable content."""
        for arguments, expected in (
            (["--help"], "{run,status,pause,resume,stop}"),
            (["--version"], "0.1.0"),
        ):
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
        """Argument parsing failures use the stable controller error contract."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["run"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(stderr.getvalue().startswith("invalid_arguments:"), stderr.getvalue())

    def test_configuration_loads(self) -> None:
        """A complete fixture configuration validates into typed fields."""
        config = load_config(self.root)
        self.assertEqual(config.multisprint, "foundation")
        self.assertEqual(config.repositories[0].path, self.root / "repositories" / "managed")
        with self.assertRaises(TypeError):
            config.limits["max_ci_fix_attempts"] = 0  # type: ignore[index]

    def test_duplicate_config_keys_are_rejected(self) -> None:
        """Duplicate JSON keys do not silently overwrite configuration."""
        (self.root / "sprint_config.json").write_text(
            '{"schema_version": 1, "schema_version": 1}\n', encoding="utf-8"
        )
        with self.assertRaises(ControllerError) as context:
            load_config(self.root)
        self.assertEqual(context.exception.code, "invalid_config")

    def test_nested_duplicate_config_keys_are_rejected(self) -> None:
        """Duplicate keys below the configuration root cannot silently override a value."""
        path = self.root / "sprint_config.json"
        contents = path.read_text(encoding="utf-8")
        path.write_text(
            contents.replace(
                '"provider": "github"', '"provider": "github", "provider": "github"', 1
            ),
            encoding="utf-8",
        )
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
        external.write_text(
            (self.root / "sprint_config.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
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

    def test_configuration_rejects_every_required_field_and_integer_boolean(self) -> None:
        """Every required field reports its path, and booleans never satisfy integer fields."""
        original = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        required_paths = (
            ("schema_version",),
            ("multisprint",),
            ("sprint",),
            ("repositories",),
            ("repositories", 0, "name"),
            ("repositories", 0, "path"),
            ("repositories", 0, "branch"),
            ("repositories", 0, "remote"),
            *(
                ("documents", field)
                for field in ("multisprint_spec", "sprint_spec", "sprint_checklist")
            ),
            *(("agents", field) for field in ("builder", "auditor", "ci_fixer")),
            *(("models", field) for field in ("builder", "auditor", "ci_fixer")),
            ("pre_ci_audit", "enabled"),
            ("pre_ci_audit", "max_rounds"),
            *(
                ("limits", field)
                for field in (
                    "max_implementation_cycles",
                    "max_ci_fix_attempts",
                    "invocation_timeout_seconds",
                    "server_unavailable_grace_seconds",
                )
            ),
            ("ci", "provider"),
            ("ci", "poll_interval_seconds"),
            ("ci", "allow_skipped"),
            ("ci", "allow_neutral"),
            ("ci", "zero_checks"),
        )
        integer_paths = (
            ("schema_version",),
            ("sprint",),
            ("pre_ci_audit", "max_rounds"),
            *(
                ("limits", field)
                for field in (
                    "max_implementation_cycles",
                    "max_ci_fix_attempts",
                    "invocation_timeout_seconds",
                    "server_unavailable_grace_seconds",
                )
            ),
            ("ci", "poll_interval_seconds"),
        )
        for path in required_paths:
            with self.subTest(missing=path):
                data = json.loads(json.dumps(original))
                target: object = data
                for key in path[:-1]:
                    target = target[key]  # type: ignore[index]
                del target[path[-1]]  # type: ignore[index]
                (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "invalid_config")
                expected = (
                    f"repositories[0].{path[-1]}"
                    if path[0] == "repositories" and len(path) > 1
                    else f"{path[0]}.{path[-1]}"
                    if len(path) > 1
                    else f"sprint_config.json.{path[-1]}"
                )
                self.assertIn(expected, context.exception.message)
        for path in integer_paths:
            with self.subTest(boolean=path):
                data = json.loads(json.dumps(original))
                target: object = data
                for key in path[:-1]:
                    target = target[key]  # type: ignore[index]
                target[path[-1]] = True  # type: ignore[index]
                (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertIn(
                    context.exception.code, {"invalid_config", "unsupported_config_schema"}
                )

    def test_configuration_path_escapes_are_rejected_for_repository_and_documents(self) -> None:
        """Configured repository and document paths cannot leave the sprint root directly or by symlink."""
        original = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        outside = self.fixture.base / "outside"
        outside.mkdir()
        (outside / "document.md").write_text("outside\n", encoding="utf-8")
        link = self.root / "outside-link"
        link.symlink_to(outside, target_is_directory=True)
        paths = (
            ("repositories", 0, "path"),
            *(("documents", field) for field in original["documents"]),
        )
        for path in paths:
            for value in ("../outside", "outside-link/document.md"):
                with self.subTest(path=path, value=value):
                    data = json.loads(json.dumps(original))
                    target: object = data
                    for key in path[:-1]:
                        target = target[key]  # type: ignore[index]
                    target[path[-1]] = value  # type: ignore[index]
                    (self.root / "sprint_config.json").write_text(
                        json.dumps(data), encoding="utf-8"
                    )
                    with self.assertRaises(ControllerError) as context:
                        load_config(self.root)
                    self.assertEqual(context.exception.code, "invalid_config")

    def test_configuration_rejects_invalid_identifiers_models_and_ci_values(self) -> None:
        """Every constrained configuration value rejects malformed identifiers, types, and semantic values."""
        original = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        cases: list[tuple[tuple[str | int, ...], object]] = [
            (("multisprint",), "Uppercase"),
            (("sprint",), 0),
            (("repositories",), {}),
            (("repositories", 0, "name"), "not valid"),
            (("repositories", 0, "branch"), "main\nnext"),
            (("repositories", 0, "remote"), ""),
            (("documents",), []),
            (("agents", "builder"), "Builder"),
            (("models", "auditor"), "provider only"),
            (("pre_ci_audit", "enabled"), 1),
            (("pre_ci_audit", "max_rounds"), 0),
            (("limits", "max_ci_fix_attempts"), 0),
            (("ci", "provider"), "other"),
            (("ci", "poll_interval_seconds"), 0),
            (("ci", "allow_skipped"), "true"),
            (("ci", "allow_neutral"), 1),
            (("ci", "zero_checks"), "Not-valid"),
            (("multisprint",), "a" * 65),
            (("repositories", 0, "name"), "a" * 65),
            (("agents", "builder"), "a" * 65),
            (("ci", "zero_checks"), "a" * 65),
            (("repositories", 0, "branch"), "main\x00hidden"),
            (("repositories", 0, "remote"), "origin\x00hidden"),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                data = json.loads(json.dumps(original))
                target: object = data
                for key in path[:-1]:
                    target = target[key]  # type: ignore[index]
                target[path[-1]] = value  # type: ignore[index]
                (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "invalid_config")

    def test_configuration_rejects_repository_cardinality_root_paths_and_model_boundaries(
        self,
    ) -> None:
        """Collection, containment, and provider/model boundary rules are explicit."""
        original = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        for repositories in ([], [original["repositories"][0], original["repositories"][0]]):
            with self.subTest(repositories=repositories):
                data = json.loads(json.dumps(original))
                data["repositories"] = repositories
                (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "invalid_config")
        for model in ("/model", "provider/", "provider /model", "provider/model name"):
            with self.subTest(model=model):
                data = json.loads(json.dumps(original))
                data["models"]["builder"] = model
                (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "invalid_config")
        data = json.loads(json.dumps(original))
        data["repositories"][0]["path"] = "."
        (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ControllerError) as context:
            load_config(self.root)
        self.assertEqual(context.exception.code, "invalid_config")
        data = json.loads(json.dumps(original))
        data["models"]["builder"] = "provider/model/with/slash"
        (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(load_config(self.root).models["builder"], "provider/model/with/slash")

    def test_configuration_rejects_wrong_container_types_and_nested_unknown_fields(self) -> None:
        """Every nested configuration object rejects incompatible collection types and extra fields."""
        original = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        container_paths = (
            ("repositories",),
            ("documents",),
            ("agents",),
            ("models",),
            ("pre_ci_audit",),
            ("limits",),
            ("ci",),
        )
        for path in container_paths:
            with self.subTest(container=path):
                data = json.loads(json.dumps(original))
                data[path[0]] = []
                (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "invalid_config")
        for path in (
            ("repositories", 0),
            ("documents",),
            ("agents",),
            ("models",),
            ("pre_ci_audit",),
            ("limits",),
            ("ci",),
        ):
            with self.subTest(unknown=path):
                data = json.loads(json.dumps(original))
                target: object = data
                for key in path:
                    target = target[key]  # type: ignore[index]
                target["unexpected"] = True  # type: ignore[index]
                (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "invalid_config")

    def test_configuration_requires_nonempty_documents_and_local_agents(self) -> None:
        """Every configured document and agent definition must remain usable local input."""
        original = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        for field, relative in original["documents"].items():
            with self.subTest(document=field, failure="missing"):
                path = self.root / relative
                contents = path.read_text(encoding="utf-8")
                path.unlink()
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "missing_required_file")
                path.write_text(contents, encoding="utf-8")
            with self.subTest(document=field, failure="empty"):
                path = self.root / relative
                contents = path.read_text(encoding="utf-8")
                path.write_text("", encoding="utf-8")
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "missing_required_file")
                path.write_text(contents, encoding="utf-8")
        for name in original["agents"].values():
            with self.subTest(agent=name):
                path = self.root / ".opencode" / "agents" / f"{name}.md"
                contents = path.read_text(encoding="utf-8")
                path.unlink()
                with self.assertRaises(ControllerError) as context:
                    load_config(self.root)
                self.assertEqual(context.exception.code, "invalid_agent_definition")
                path.write_text(contents, encoding="utf-8")
        agent = self.root / ".opencode" / "agents" / "builder.md"
        external = self.fixture.base / "external-agent.md"
        external.write_text("external\n", encoding="utf-8")
        agent.unlink()
        agent.symlink_to(external)
        with self.assertRaises(ControllerError) as context:
            load_config(self.root)
        self.assertEqual(context.exception.code, "invalid_agent_definition")

    def test_configuration_preserves_disabled_pre_ci_audit(self) -> None:
        """The reserved disabled-audit setting remains valid without Sprint 1 semantics."""
        data = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        data["pre_ci_audit"]["enabled"] = False
        (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(load_config(self.root).pre_ci_enabled)

    def test_no_run_json_status_is_read_only(self) -> None:
        """Status returns a stable no-run projection without worktree artifacts."""
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(
            set(data),
            {
                "schema_version",
                "controller_version",
                "sprint_root",
                "run_exists",
                "process_running",
                "run_id",
                "sprint",
                "state",
                "reason",
                "active",
                "commits",
                "audit",
                "ci",
                "counters",
                "checklist",
                "last_event",
                "updated_at",
            },
        )
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["controller_version"], __version__)
        self.assertEqual(data["sprint_root"], str(self.root.resolve()))
        self.assertFalse(data["run_exists"])
        self.assertFalse(data["process_running"])
        for field in (
            "run_id",
            "sprint",
            "state",
            "reason",
            "active",
            "commits",
            "audit",
            "ci",
            "counters",
            "checklist",
            "last_event",
            "updated_at",
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

    def test_git_preflight_disables_configured_filesystem_monitor(self) -> None:
        """Read-only preflight never executes a repository-configured fsmonitor program."""
        sentinel = self.fixture.base / "fsmonitor-ran"
        monitor = self.fixture.base / "fsmonitor"
        monitor.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
        monitor.chmod(0o700)
        git(self.root, "config", "core.fsmonitor", str(monitor))
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 4, stderr)
        self.assertFalse(sentinel.exists())

    def test_git_preflight_never_uses_mutating_commands(self) -> None:
        """The Git adapter command set excludes all prohibited mutation commands."""
        from opencode_sprint_loop import git as controller_git

        observed: list[tuple[str, ...]] = []
        original = controller_git.subprocess.run

        def record(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            observed.append(tuple(arguments))
            return original(arguments, **kwargs)  # type: ignore[arg-type]

        with patch("opencode_sprint_loop.git.subprocess.run", side_effect=record):
            code, _, stderr = self.invoke(
                ["run", "--root", str(self.root), "--server-url", "opaque"]
            )
        self.assertEqual(code, 4, stderr)
        prohibited = {"add", "commit", "stash", "reset", "checkout", "switch", "clean", "push"}
        self.assertTrue(observed)
        self.assertTrue(all(prohibited.isdisjoint(command) for command in observed))

    def test_submodule_path_with_spaces_is_registered_correctly(self) -> None:
        """NUL-delimited .gitmodules parsing preserves a valid path containing spaces."""
        git(self.root, "mv", "repositories/managed", "repositories/managed repo")
        data = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        data["repositories"][0]["path"] = "repositories/managed repo"
        (self.root / "sprint_config.json").write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
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
                (root / "sprint_config.json").write_text(
                    json.dumps(data, indent=2) + "\n", encoding="utf-8"
                )
                git(root, "add", "sprint_config.json")
                git(root, "commit", "-m", "Update sprint identity")
            calls += 1
            return result

        with patch("opencode_sprint_loop.cli.validate_preflight", side_effect=validate_then_change):
            code, _, stderr = self.invoke(
                ["run", "--root", str(self.root), "--server-url", "opaque"]
            )
        self.assertEqual(code, 4, stderr)
        self.assertTrue((self.root / "info" / "updated" / "1" / "state.json").exists())
        self.assertFalse((self.root / "info" / "foundation").exists())

    def test_valid_run_persists_placeholder_state(self) -> None:
        """A valid run creates the intentional three-event blocked placeholder."""
        code, stdout, stderr = self.invoke(
            ["run", "--root", str(self.root), "--server-url", "opaque-value"]
        )
        self.assertEqual(code, 4, (stdout, stderr))
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = load_state(paths.state)
        events = load_events(paths.events)
        self.assertEqual(state["state"], "blocked")
        self.assertEqual(state["reason"]["code"], "execution_not_implemented")
        self.assertEqual(
            [event["type"] for event in events], ["run.started", "state.entered", "run.blocked"]
        )
        self.assertIsNone(state["server"]["url"])
        self.assertIn("execution_not_implemented", stderr)

    def test_sprint_one_state_rejects_populated_reserved_fields_and_credentials(self) -> None:
        """Persisted state cannot claim deferred Sprint 1 work or expose credential-bearing data."""
        config = load_config(self.root)
        cases: list[tuple[tuple[str, ...], object]] = [
            (("server", "url"), "https://user:secret@example.invalid"),
            (("server", "version"), "later"),
            (("commits", "local", "managed"), "Authorization: Bearer secret"),
            (("audit", "phase"), "pre_ci"),
            (("audit", "pre_ci_round"), 1),
            (("ci", "attempt"), 1),
            (("ci", "commit_sha"), "deadbeef"),
            (("ci", "checks"), [{"name": "later"}]),
            (("counters", "implementation_cycles"), 1),
            (("checklist", "satisfied"), 1),
            (("checklist", "items"), [{"id": "later"}]),
            (("control", "requested"), "pause"),
            (("terminal_result",), {"status": "finished"}),
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

    def test_placeholder_status_matches_stable_projection(self) -> None:
        """Persisted status exposes only the documented status projection fields."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        status = json.loads(stdout)
        paths = runtime_paths(self.root, "foundation", 1)
        state = load_state(paths.state)
        events = load_events(paths.events)
        self.assertEqual(
            status,
            {
                "schema_version": 1,
                "controller_version": __version__,
                "sprint_root": str(self.root.resolve()),
                "run_exists": True,
                "process_running": False,
                "run_id": state["run_id"],
                "sprint": {"multisprint": "foundation", "index": 1},
                "state": "blocked",
                "reason": {
                    "code": "execution_not_implemented",
                    "message": "Sprint execution begins in a later implementation sprint.",
                },
                "active": {"role": None, "invocation_id": None, "session_id": None},
                "commits": {"local": {"managed": None}, "pushed": {"managed": None}},
                "audit": {
                    "phase": None,
                    "pre_ci_round": 0,
                    "pre_ci_max_rounds": 2,
                    "remaining_effort": None,
                },
                "ci": {"status": "not_started", "attempt": 0, "commit_sha": None},
                "counters": {"implementation_cycles": 0, "ci_fix_attempts": 0},
                "checklist": {
                    "satisfied": 0,
                    "partial": 0,
                    "unsatisfied": 0,
                    "not_evaluated": 0,
                    "assessed_at": None,
                },
                "last_event": {
                    "sequence": 3,
                    "type": "run.blocked",
                    "timestamp": events[-1]["timestamp"],
                },
                "updated_at": state["updated_at"],
            },
        )

    def test_status_rejects_configuration_derived_audit_mismatch(self) -> None:
        """Status refuses persisted audit limits that contradict current configuration."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        paths = runtime_paths(self.root, "foundation", 1)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        state["audit"]["pre_ci_max_rounds"] = 999
        paths.state.write_text(json.dumps(state), encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)

    def test_existing_run_precedes_dirty_worktree_error(self) -> None:
        """Runtime artifacts cause run_already_exists even though they dirty the worktree."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
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

    def test_root_identity_failures_create_no_runtime_paths(self) -> None:
        """Missing, non-Git, child, and bare roots fail before any controller runtime mutation."""
        non_git = self.fixture.base / "non-git"
        non_git.mkdir()
        regular = self.fixture.base / "regular-file"
        regular.write_text("not a directory\n", encoding="utf-8")
        bare = self.fixture.base / "bare.git"
        git(self.fixture.base, "init", "--bare", str(bare))
        cases = (self.root / "missing", non_git, regular, self.root / "docs", bare)
        for root in cases:
            with self.subTest(root=root):
                info_existed = (root / "info").exists()
                code, _, stderr = self.invoke(
                    ["run", "--root", str(root), "--server-url", "opaque"]
                )
                self.assertEqual(code, 2)
                self.assertTrue(stderr)
                self.assertEqual((root / "info").exists(), info_existed)

    def test_status_root_failures_create_no_runtime_paths(self) -> None:
        """Status rejects invalid roots without creating runtime paths or worktree files."""
        non_git = self.fixture.base / "status-non-git"
        non_git.mkdir()
        bare = self.fixture.base / "status-bare.git"
        git(self.fixture.base, "init", "--bare", str(bare))
        regular = self.fixture.base / "status-regular-file"
        regular.write_text("not a directory\n", encoding="utf-8")
        for root in (self.root / "missing", non_git, regular, self.root / "docs", bare):
            with self.subTest(root=root):
                code, stdout, stderr = self.invoke(["status", "--root", str(root), "--json"])
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr)
                if root != bare:
                    self.assertFalse((root / "info").exists())

    def test_status_allows_user_dirty_worktrees(self) -> None:
        """Status reports no-run state without applying run cleanliness preflight."""
        (self.root / "user-dirty.txt").write_text("user work\n", encoding="utf-8")
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertFalse(json.loads(stdout)["run_exists"])

    def test_missing_config_uses_missing_required_file_reason(self) -> None:
        """Absent required configuration is distinct from malformed configuration."""
        (self.root / "sprint_config.json").unlink()
        for arguments in (
            ["run", "--root", str(self.root), "--server-url", "opaque"],
            ["status", "--root", str(self.root), "--json"],
        ):
            with self.subTest(command=arguments[0]):
                code, _, stderr = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertIn("missing_required_file", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_missing_root_agents_file_fails_without_runtime_artifacts(self) -> None:
        """Run preflight requires the root instructions file before mutation."""
        (self.root / "AGENTS.md").unlink()
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("missing_required_file", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_sprint_root_without_a_resolvable_head_is_rejected_without_runtime_artifacts(
        self,
    ) -> None:
        """A worktree whose current HEAD does not resolve cannot enter controller preflight."""
        git(self.root, "update-ref", "-d", "HEAD")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("root_not_git_worktree", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_bare_managed_repository_is_rejected_without_runtime_artifacts(self) -> None:
        """A bare repository cannot replace the configured initialized submodule checkout."""
        shutil.rmtree(self.fixture.managed)
        git(self.fixture.base, "init", "--bare", str(self.fixture.managed))
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("uninitialized_submodule", stderr)
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
            for kind in ("staged", "unstaged", "untracked", "untracked_directory"):
                with self.subTest(repository=repository_name, kind=kind):
                    fixture, _ = self.variant()
                    repository = fixture.root if repository_name == "sprint" else fixture.managed
                    fixture.make_dirty(repository, kind)
                    expected = (
                        "dirty_sprint_repository"
                        if repository_name == "sprint"
                        else "dirty_managed_repository"
                    )
                    self.assert_preflight_preserves_snapshot(fixture, expected)

    def test_ignored_untracked_files_are_rejected_and_preserved(self) -> None:
        """Ignored user files remain preflight blockers in both managed worktrees."""
        for repository_name in ("sprint", "managed"):
            with self.subTest(repository=repository_name):
                fixture, _ = self.variant()
                repository = fixture.root if repository_name == "sprint" else fixture.managed
                exclude = Path(git(repository, "rev-parse", "--git-path", "info/exclude"))
                if not exclude.is_absolute():
                    exclude = repository / exclude
                with exclude.open("a", encoding="utf-8") as handle:
                    handle.write("ignored.txt\n")
                (repository / "ignored.txt").write_text("user work\n", encoding="utf-8")
                expected = (
                    "dirty_sprint_repository"
                    if repository_name == "sprint"
                    else "dirty_managed_repository"
                )
                self.assert_preflight_preserves_snapshot(fixture, expected)
                self.assertEqual(
                    (repository / "ignored.txt").read_text(encoding="utf-8"), "user work\n"
                )

    def test_nested_submodule_dirtiness_cannot_be_hidden_by_git_configuration(self) -> None:
        """Managed preflight overrides submodule ignore settings when checking cleanliness."""
        nested = self.fixture.managed / "nested"
        git(
            self.fixture.managed,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-b",
            "main",
            str(self.fixture.remote),
            "nested",
        )
        git(self.fixture.managed, "config", "user.email", "fixture@example.invalid")
        git(self.fixture.managed, "config", "user.name", "Fixture")
        git(self.fixture.managed, "add", ".gitmodules", "nested")
        git(self.fixture.managed, "commit", "-m", "Add nested submodule")
        git(self.root, "add", "repositories/managed")
        git(self.root, "commit", "-m", "Update managed gitlink")
        git(self.fixture.managed, "config", "submodule.nested.ignore", "all")
        (nested / "nested-dirty.txt").write_text("dirty\n", encoding="utf-8")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("dirty_managed_repository", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_replacement_clone_is_not_accepted_as_registered_submodule(self) -> None:
        """A same-HEAD repository cannot impersonate the registered submodule checkout."""
        shutil.rmtree(self.fixture.managed)
        git(
            self.fixture.base,
            "clone",
            "--branch",
            "main",
            str(self.fixture.remote),
            str(self.fixture.managed),
        )
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("uninitialized_submodule", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_old_form_registered_submodule_is_accepted(self) -> None:
        """A registered submodule with an embedded Git directory remains valid."""
        module_dir = Path(git(self.fixture.managed, "rev-parse", "--git-dir"))
        if not module_dir.is_absolute():
            module_dir = (self.fixture.managed / module_dir).resolve()
        (self.fixture.managed / ".git").unlink()
        shutil.move(str(module_dir), self.fixture.managed / ".git")
        config_path = self.fixture.managed / ".git" / "config"
        config_path.write_text(
            "\n".join(
                line
                for line in config_path.read_text(encoding="utf-8").splitlines()
                if "worktree" not in line
            )
            + "\n",
            encoding="utf-8",
        )
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 4, stderr)

    def test_worktree_backed_by_another_submodule_is_rejected(self) -> None:
        """An absorbed checkout must use the Git directory for its own .gitmodules entry."""
        other = self.root / "repositories" / "other"
        git(
            self.root,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-b",
            "main",
            str(self.fixture.remote),
            str(other.relative_to(self.root)),
        )
        git(self.root, "add", ".gitmodules", "repositories")
        git(self.root, "commit", "-m", "Add second submodule")
        other_git_dir = Path(git(other, "rev-parse", "--git-dir"))
        if not other_git_dir.is_absolute():
            other_git_dir = (other / other_git_dir).resolve()
        shutil.rmtree(self.fixture.managed)
        git(
            self.fixture.base,
            f"--git-dir={other_git_dir}",
            "worktree",
            "add",
            "--force",
            str(self.fixture.managed),
            "main",
        )
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("uninitialized_submodule", stderr)

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

    def test_option_like_remote_name_is_not_accepted_from_git_usage_output(self) -> None:
        """A failed remote query cannot be mistaken for a configured remote URL."""
        data = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        data["repositories"][0]["remote"] = "-h"
        (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
        git(self.root, "add", "sprint_config.json")
        git(self.root, "commit", "-m", "Use invalid remote name")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("missing_remote", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_git_failure_diagnostics_include_path_and_expected_values(self) -> None:
        """Git preflight errors report the repository, expected setting, and observed condition."""
        variants = (
            (lambda fixture: fixture.set_managed_branch(), ("main", "wrong")),
            (lambda fixture: fixture.remove_managed_remote(), ("origin",)),
            (lambda fixture: fixture.make_gitlink_mismatch(), ("expected gitlink",)),
        )
        for mutate, expected in variants:
            with self.subTest(expected=expected):
                fixture, _ = self.variant()
                mutate(fixture)
                code, _, stderr = self.invoke(
                    ["run", "--root", str(fixture.root), "--server-url", "opaque"]
                )
                self.assertEqual(code, 2)
                for text in (str(fixture.managed), *expected):
                    self.assertIn(text, stderr)

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

    def test_deferred_commands_preserve_existing_runtime_artifacts(self) -> None:
        """Reserved controls leave an existing Sprint 1 run byte-for-byte unchanged."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        paths = runtime_paths(self.root, "foundation", 1)
        before = {
            path: path.read_bytes() for path in (paths.state, paths.events, paths.lock_metadata)
        }
        for arguments in (
            ["pause", "--root", str(self.root)],
            ["resume", "--root", str(self.root), "--server-url", "opaque"],
            ["stop", "--root", str(self.root)],
        ):
            with self.subTest(command=arguments[0]):
                code, _, stderr = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertIn("feature_not_implemented", stderr)
                self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_human_no_run_and_placeholder_status(self) -> None:
        """Human status remains concise before and after the placeholder run."""
        code, stdout, stderr = self.invoke(["status", "--root", str(self.root)])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, f"Sprint root: {self.root.resolve()}\nState: no run\n")
        self.assertEqual(stderr, "")
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
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
        code, stdout, stderr = self.invoke(
            ["run", "--root", str(self.root), "--server-url", opaque]
        )
        self.assertEqual(code, 4)
        self.assertEqual(stdout, "")
        self.assertNotIn(opaque, stderr)
        self.assertNotIn("synthetic-secret", stderr)
        artifacts = b"".join(
            path.read_bytes() for path in (self.root / "info").rglob("*") if path.is_file()
        )
        self.assertNotIn(opaque.encode(), artifacts)
        self.assertNotIn(b"synthetic-secret", artifacts)

    def test_credential_fields_are_rejected_from_events_and_state_reasons(self) -> None:
        """Controller artifacts reject explicit credential-bearing payload and reason fields."""
        state = new_state(load_config(self.root))
        event = transition_event(
            state,
            "run.started",
            "initializing",
            {"previous_state": None, "api_token": "synthetic-secret"},
        )
        with self.assertRaises(ControllerError) as context:
            append_event(self.root / "info" / "events.jsonl", event)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertFalse((self.root / "info").exists())

        state["state"] = "blocked"
        state["reason"] = {
            "code": "execution_not_implemented",
            "message": "safe message",
            "details": {"authorization": "synthetic-secret"},
        }
        with self.assertRaises(ControllerError) as context:
            validate_state(state)
        self.assertEqual(context.exception.code, "corrupt_state")

    def test_credential_values_are_rejected_and_diagnostics_are_redacted(self) -> None:
        """Benign fields cannot hide credentials in durable values or command diagnostics."""
        state = new_state(load_config(self.root))
        event = transition_event(
            state,
            "run.started",
            "initializing",
            {"previous_state": None, "note": "Authorization: Bearer synthetic-secret"},
        )
        with self.assertRaises(ControllerError) as context:
            append_event(self.root / "info" / "events.jsonl", event)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertFalse((self.root / "info").exists())

        state["state"] = "blocked"
        state["reason"] = {
            "code": "test",
            "message": "https://user:synthetic-secret@example.invalid/?token=synthetic-secret",
            "details": {},
        }
        with self.assertRaises(ControllerError) as context:
            validate_state(state)
        self.assertEqual(context.exception.code, "corrupt_state")

        diagnostic = redact_diagnostic(
            "https://user:synthetic-secret@example.invalid/?token=synthetic-secret"
        )
        self.assertNotIn("synthetic-secret", diagnostic)
        self.assertIn("[REDACTED]", diagnostic)

        for value in (
            "password: synthetic-secret",
            "token=synthetic-secret",
            "https://example.invalid/#token=synthetic-secret",
            "ghp_" + "A" * 36,
            "github_pat_" + "A" * 20,
            "sk-" + "A" * 20,
            "AKIA" + "A" * 16,
        ):
            with self.subTest(value=value):
                event = transition_event(
                    new_state(load_config(self.root)),
                    "run.started",
                    "initializing",
                    {"previous_state": None, "note": value},
                )
                with self.assertRaises(ControllerError) as context:
                    append_event(self.root / "info" / "events.jsonl", event)
                self.assertEqual(context.exception.code, "persistence_failed")
                self.assertNotIn("synthetic-secret", redact_diagnostic(value))

    def test_configured_remote_secret_is_redacted_from_diagnostics(self) -> None:
        """Configuration-derived Git diagnostics cannot expose sensitive remote values."""
        data = json.loads((self.root / "sprint_config.json").read_text(encoding="utf-8"))
        data["repositories"][0]["remote"] = "token=synthetic-secret"
        (self.root / "sprint_config.json").write_text(json.dumps(data), encoding="utf-8")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("missing_remote", stderr)
        self.assertNotIn("synthetic-secret", stderr)
        self.assertIn("[REDACTED]", stderr)
        self.assertFalse((self.root / "info").exists())

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
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        with paths.events.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sequence": 4,
                        "timestamp": "2026-01-01T00:00:00Z",
                        "run_id": load_state(paths.state)["run_id"],
                        "type": "state.entered",
                        "state": "blocked",
                        "payload": {},
                    }
                )
                + "\n"
            )
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)

    def test_status_rejects_partial_event_line(self) -> None:
        """Partial JSONL is corruption and is never automatically truncated."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
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
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        original = [
            json.loads(line) for line in paths.events.read_text(encoding="utf-8").splitlines()
        ]
        for label, mutate in (
            ("duplicate", lambda records: records.__setitem__(1, {**records[1], "sequence": 1})),
            ("gap", lambda records: records.__setitem__(1, {**records[1], "sequence": 3})),
            (
                "run_id",
                lambda records: records.__setitem__(
                    1, {**records[1], "run_id": "00000000-0000-4000-8000-000000000000"}
                ),
            ),
            ("schema", lambda records: records.__setitem__(1, {**records[1], "schema_version": 2})),
        ):
            with self.subTest(label=label):
                records = [dict(event) for event in original]
                mutate(records)
                paths.events.write_text(
                    "".join(json.dumps(event) + "\n" for event in records), encoding="utf-8"
                )
                with self.assertRaises(ControllerError) as context:
                    load_events(paths.events)
                self.assertEqual(context.exception.code, "corrupt_event_log")
        paths.events.write_text(
            "".join(json.dumps(event) + "\n" for event in original[:2]), encoding="utf-8"
        )
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_event_log", stderr)

    def test_duplicate_state_and_event_json_keys_fail_closed(self) -> None:
        """Duplicate state and event keys cannot be hidden behind otherwise valid persistence."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        paths = runtime_paths(self.root, "foundation", 1)
        state = paths.state.read_text(encoding="utf-8")
        paths.state.write_text(
            state.replace('"state": "blocked"', '"state": "blocked", "state": "blocked"', 1),
            encoding="utf-8",
        )
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_state", stderr)

        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 2
        )
        paths.state.write_text(state, encoding="utf-8")
        events = paths.events.read_text(encoding="utf-8")
        paths.events.write_text(
            events.replace('"sequence": 1', '"sequence": 1, "sequence": 1', 1), encoding="utf-8"
        )
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_event_log", stderr)

    def test_event_short_write_preserves_existing_bytes_and_fails_explicitly(self) -> None:
        """A detected short append never rewrites valid history and leaves corruption visible."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = load_state(paths.state)
        before = paths.events.read_bytes()
        event = transition_event(state, "state.entered", "blocked", {"previous_state": "blocked"})

        real_write = os.write

        def short_write(descriptor: int, data: bytes) -> int:
            return real_write(descriptor, data[:-1])

        with patch("opencode_sprint_loop.events.os.write", side_effect=short_write):
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
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
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
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
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
        replacement["process"] = dict(state["process"], active=False)
        write_state_atomic(paths.state, replacement)
        self.assertEqual(load_state(paths.state)["state"], "blocked")

    def test_atomic_state_readers_observe_only_complete_snapshots(self) -> None:
        """A concurrent reader validates every snapshot during atomic replacements."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        initial = new_state(config)
        write_state_atomic(paths.state, initial)
        context = multiprocessing.get_context("fork")
        stop = context.Event()
        results = context.Queue()
        reader = context.Process(target=read_state_until_stopped, args=(paths.state, stop, results))
        reader.start()
        try:
            for index in range(50):
                replacement = dict(initial)
                replacement["state"] = "blocked"
                replacement["reason"] = {"code": "test", "message": f"test {index}", "details": {}}
                replacement["process"] = dict(initial["process"], active=False)
                write_state_atomic(paths.state, replacement)
                write_state_atomic(paths.state, initial)
            stop.set()
            reader.join(timeout=5)
            self.assertFalse(reader.is_alive())
            self.assertIsNone(results.get(timeout=1))
        finally:
            stop.set()
            if reader.is_alive():
                reader.terminate()
                reader.join(timeout=5)
            results.close()

    def test_oversized_state_is_rejected_before_creating_runtime_paths(self) -> None:
        """The writer cannot persist state that exceeds the bounded reader contract."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = new_state(config)
        state["state"] = "blocked"
        state["reason"] = {
            "code": "test",
            "message": "test",
            "details": {"note": "x" * MAX_JSON_BYTES},
        }
        state["process"]["active"] = False
        with self.assertRaises(ControllerError) as context:
            write_state_atomic(paths.state, state)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertFalse(paths.info_dir.exists())

    def test_atomic_state_replace_failure_preserves_previous_state(self) -> None:
        """A replace failure leaves the prior complete state readable."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        original = new_state(config)
        write_state_atomic(paths.state, original)
        replacement = dict(original)
        replacement["state"] = "blocked"
        replacement["reason"] = {"code": "test", "message": "test", "details": {}}
        replacement["process"] = dict(original["process"], active=False)
        with patch("opencode_sprint_loop.state.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "initializing")
        self.assertEqual(list(paths.info_dir.glob(".state-*.tmp")), [])

    def test_atomic_state_write_and_sync_failures_leave_complete_snapshots(self) -> None:
        """Write, pre-replace sync, and post-replace sync failures never truncate state."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        original = new_state(config)
        write_state_atomic(paths.state, original)
        replacement = dict(original)
        replacement["state"] = "blocked"
        replacement["reason"] = {"code": "test", "message": "test", "details": {}}
        replacement["process"] = dict(original["process"], active=False)

        real_open = os.open

        def fail_temporary_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            *arguments: object,
            **keywords: object,
        ) -> int:
            if isinstance(path, str) and path.startswith(".state-"):
                raise OSError("injected")
            return real_open(path, *arguments, **keywords)  # type: ignore[arg-type]

        with patch("opencode_sprint_loop.state.os.open", side_effect=fail_temporary_open):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "initializing")
        self.assertEqual(list(paths.info_dir.glob(".state-*.tmp")), [])

        with patch("opencode_sprint_loop.state.os.write", side_effect=OSError("injected")):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "initializing")
        self.assertEqual(list(paths.info_dir.glob(".state-*.tmp")), [])

        with patch("opencode_sprint_loop.state.os.fsync", side_effect=OSError("injected")):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "initializing")
        self.assertEqual(list(paths.info_dir.glob(".state-*.tmp")), [])

        with patch("opencode_sprint_loop.state.os.fsync", side_effect=[None, OSError("injected")]):
            with self.assertRaises(ControllerError) as context:
                write_state_atomic(paths.state, replacement)
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(load_state(paths.state)["state"], "blocked")
        self.assertEqual(list(paths.info_dir.glob(".state-*.tmp")), [])
        self.assertEqual(paths.state.stat().st_mode & 0o077, 0)

    def test_interrupted_transition_leaves_event_ahead_of_state_inconsistent(self) -> None:
        """An interruption after event sync is detected and is never automatically replayed."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = new_state(config)
        _, persistence_lock = controller_locks(self.root)
        with patch(
            "opencode_sprint_loop.transitions.write_state_atomic_at",
            side_effect=OSError("injected"),
        ):
            with self.assertRaises(OSError):
                persist_initial(state, paths.events, paths.state, persistence_lock)
        self.assertFalse(paths.state.exists())
        self.assertEqual(len(load_events(paths.events)), 1)
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)

    def test_runtime_directory_replacement_cannot_split_transition_artifacts(self) -> None:
        """A directory swap cannot redirect the event/state pair to different locations."""
        from opencode_sprint_loop import transitions

        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        _, persistence_lock = controller_locks(self.root)
        state = persist_initial(new_state(config), paths.events, paths.state, persistence_lock)
        original = transitions.write_state_atomic_at
        displaced = self.fixture.base / "displaced-runtime"

        def replace_runtime_directory(
            directory: int, name: str, path: Path, serialized: str
        ) -> None:
            paths.info_dir.rename(displaced)
            paths.info_dir.mkdir(parents=True)
            original(directory, name, path, serialized)

        with patch(
            "opencode_sprint_loop.transitions.write_state_atomic_at",
            side_effect=replace_runtime_directory,
        ):
            with self.assertRaises(ControllerError) as context:
                transition(state, paths.events, paths.state, persistence_lock, "validating")
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertFalse((paths.info_dir / "events.jsonl").exists())
        self.assertFalse((paths.info_dir / "state.json").exists())
        self.assertEqual(load_state(displaced / "state.json")["state"], "validating")
        self.assertEqual(load_events(displaced / "events.jsonl")[-1]["state"], "validating")

    def test_transition_guards_preserve_durable_pair_on_invalid_requests(self) -> None:
        """Missing reasons and unsupported destinations cannot append partial transitions."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        _, persistence_lock = controller_locks(self.root)
        state = persist_initial(new_state(config), paths.events, paths.state, persistence_lock)
        state = transition(state, paths.events, paths.state, persistence_lock, "validating")
        before_state = paths.state.read_bytes()
        before_events = paths.events.read_bytes()
        for destination, reason in (("blocked", None), ("finished", None), ("unknown", None)):
            with self.subTest(destination=destination):
                with self.assertRaises(ControllerError) as context:
                    transition(
                        state,
                        paths.events,
                        paths.state,
                        persistence_lock,
                        destination,
                        reason=reason,
                    )
                self.assertEqual(context.exception.code, "internal_error")
                self.assertEqual(paths.state.read_bytes(), before_state)
                self.assertEqual(paths.events.read_bytes(), before_events)

    def test_invalid_reason_never_appends_an_event(self) -> None:
        """A malformed transition reason is rejected before durable history changes."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        _, persistence_lock = controller_locks(self.root)
        state = persist_initial(new_state(config), paths.events, paths.state, persistence_lock)
        state = transition(state, paths.events, paths.state, persistence_lock, "validating")
        before_state = paths.state.read_bytes()
        before_events = paths.events.read_bytes()
        with self.assertRaises(ControllerError) as context:
            transition(
                state,
                paths.events,
                paths.state,
                persistence_lock,
                "blocked",
                reason={"code": "", "message": "invalid", "details": {}},
            )
        self.assertEqual(context.exception.code, "corrupt_state")
        self.assertEqual(paths.state.read_bytes(), before_state)
        self.assertEqual(paths.events.read_bytes(), before_events)

    def test_initial_transition_rejects_non_new_or_existing_state_under_lock(self) -> None:
        """The first transition cannot overwrite a pair or accept a later state."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        _, persistence_lock = controller_locks(self.root)
        not_new = new_state(config)
        not_new["state"] = "validating"
        with advisory_lock(persistence_lock, exclusive=True):
            with self.assertRaises(ControllerError) as context:
                persist_initial(
                    not_new, paths.events, paths.state, persistence_lock, lock_held=True
                )
        self.assertEqual(context.exception.code, "internal_error")
        self.assertFalse(paths.state.exists())
        self.assertFalse(paths.events.exists())

        with advisory_lock(persistence_lock, exclusive=True):
            persisted = persist_initial(
                new_state(config), paths.events, paths.state, persistence_lock, lock_held=True
            )
        before_state = paths.state.read_bytes()
        before_events = paths.events.read_bytes()
        with advisory_lock(persistence_lock, exclusive=True):
            with self.assertRaises(ControllerError) as context:
                persist_initial(
                    persisted, paths.events, paths.state, persistence_lock, lock_held=True
                )
        self.assertEqual(context.exception.code, "inconsistent_persistence")
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

        self.assertEqual(
            self.invoke(["run", "--root", str(fixture.root), "--server-url", "opaque"])[0], 2
        )
        paths.state.unlink()
        write_state_atomic(paths.state, new_state(load_config(root)))
        paths.events.write_text(oversized + "\n", encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_event_log", stderr)

    def test_nonfinite_and_deep_json_inputs_have_stable_errors(self) -> None:
        """Non-finite values and parser-depth failures remain actionable controller errors."""
        deep_json = "[" * 1_100 + "0" + "]" * 1_100
        for contents in ("NaN", "Infinity", "-Infinity", deep_json):
            with self.subTest(input=contents[:10]):
                fixture, root = self.variant()
                (root / "sprint_config.json").write_text(contents, encoding="utf-8")
                code, stdout, stderr = self.invoke(["status", "--root", str(root), "--json"])
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.startswith("invalid_config:"), stderr)

        fixture, root = self.variant()
        self.assertEqual(self.invoke(["run", "--root", str(root), "--server-url", "opaque"])[0], 4)
        paths = runtime_paths(root, "foundation", 1)
        paths.state.write_text(deep_json, encoding="utf-8")
        code, stdout, stderr = self.invoke(["status", "--root", str(root), "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("corrupt_state:"), stderr)

        fixture, root = self.variant()
        self.assertEqual(self.invoke(["run", "--root", str(root), "--server-url", "opaque"])[0], 4)
        paths = runtime_paths(root, "foundation", 1)
        paths.events.write_text("NaN\n", encoding="utf-8")
        code, stdout, stderr = self.invoke(["status", "--root", str(root), "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("corrupt_event_log:"), stderr)

    def test_hardlinked_configured_documents_are_rejected(self) -> None:
        """Distinct configured documents cannot alias one filesystem object."""
        documents = self.root / "docs" / "foundation"
        checklist = documents / "1" / "sprint_checklist.md"
        checklist.unlink()
        os.link(documents / "multisprint_spec.md", checklist)
        with self.assertRaises(ControllerError) as context:
            load_config(self.root)
        self.assertEqual(context.exception.code, "invalid_config")

    def test_runtime_symlinks_are_rejected_without_following_them(self) -> None:
        """Runtime directories and artifacts, including dangling events, cannot redirect I/O."""
        for label, prepare in (
            (
                "info",
                lambda root, paths, outside: (root / "info").symlink_to(
                    outside, target_is_directory=True
                ),
            ),
            (
                "multisprint",
                lambda root, paths, outside: (
                    (root / "info").mkdir(),
                    (root / "info" / "foundation").symlink_to(outside, target_is_directory=True),
                ),
            ),
            (
                "sprint",
                lambda root, paths, outside: (
                    (root / "info" / "foundation").mkdir(parents=True),
                    paths.info_dir.symlink_to(outside, target_is_directory=True),
                ),
            ),
            (
                "state",
                lambda root, paths, outside: (
                    paths.info_dir.mkdir(parents=True),
                    paths.state.symlink_to(outside / "state.json"),
                ),
            ),
            (
                "dangling_events",
                lambda root, paths, outside: (
                    paths.info_dir.mkdir(parents=True),
                    paths.events.symlink_to(outside / "missing-events.jsonl"),
                ),
            ),
            (
                "lock_metadata",
                lambda root, paths, outside: (
                    paths.info_dir.mkdir(parents=True),
                    paths.lock_metadata.symlink_to(outside / "lock.json"),
                ),
            ),
        ):
            with self.subTest(path=label):
                fixture, root = self.variant()
                paths = runtime_paths(root, "foundation", 1)
                outside = fixture.base / "outside"
                outside.mkdir()
                prepare(root, paths, outside)
                code, stdout, stderr = self.invoke(["status", "--root", str(root), "--json"])
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("inconsistent_persistence", stderr)
                self.assertEqual(list(outside.iterdir()), [])

    def test_runtime_reader_rejects_ancestor_replacement_after_preflight(self) -> None:
        """Descriptor-anchored reads do not follow an `info/` symlink installed after checks."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        outside = self.fixture.base / "outside-info"
        (self.root / "info").rename(outside)
        (self.root / "info").symlink_to(outside, target_is_directory=True)
        before = {
            path.relative_to(outside): path.read_bytes()
            for path in outside.rglob("*")
            if path.is_file()
        }
        with patch("opencode_sprint_loop.cli.ensure_runtime_paths_safe"):
            code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("corrupt_state", stderr)
        after = {
            path.relative_to(outside): path.read_bytes()
            for path in outside.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_fifo_runtime_artifacts_are_rejected_without_blocking(self) -> None:
        """Status uses metadata checks and never opens non-regular runtime artifacts."""
        for label, artifact_name in (
            ("state", "state"),
            ("events", "events"),
            ("lock", "lock_metadata"),
        ):
            with self.subTest(artifact=label):
                fixture, root = self.variant()
                paths = runtime_paths(root, "foundation", 1)
                paths.info_dir.mkdir(parents=True)
                os.mkfifo(getattr(paths, artifact_name))
                context = multiprocessing.get_context("fork")
                started = context.Event()
                results = context.Queue()
                process = context.Process(
                    target=invoke_in_child,
                    args=(["status", "--root", str(root), "--json"], started, results),
                )
                process.start()
                try:
                    self.assertTrue(started.wait(timeout=5))
                    process.join(timeout=5)
                    self.assertFalse(
                        process.is_alive(), "status blocked on a FIFO runtime artifact"
                    )
                    code, stdout, stderr = results.get(timeout=1)
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout, "")
                    self.assertIn("inconsistent_persistence", stderr)
                finally:
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                    results.close()

    def test_lock_metadata_temporary_symlink_is_not_followed(self) -> None:
        """Lock metadata replacement refuses a preexisting temporary-file symlink."""
        from opencode_sprint_loop.cli import _write_lock_metadata

        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        paths.info_dir.mkdir(parents=True)
        outside = self.fixture.base / "outside-lock-metadata"
        outside.write_text("outside remains unchanged\n", encoding="utf-8")
        temporary = paths.info_dir / ".lock-fixed.tmp"
        temporary.symlink_to(outside)
        with patch("opencode_sprint_loop.cli.secrets.token_hex", return_value="fixed"):
            with self.assertRaises(ControllerError) as context:
                _write_lock_metadata(self.root, paths.lock_metadata, new_state(config))
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside remains unchanged\n")
        self.assertFalse(paths.lock_metadata.exists())

    def test_build_and_clean_wheel_installation(self) -> None:
        """A temporary source copy builds, includes documents, and installs as an executable wheel."""
        project_root = Path(__file__).resolve().parents[2]
        offline_environment = {
            **os.environ,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            shutil.copytree(
                project_root,
                source,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "build",
                    "dist",
                    "*.egg-info",
                    "__pycache__",
                    "opencode_sprint_loop.lua",
                ),
            )
            result = subprocess.run(
                [sys.executable, "-m", "build", "--no-isolation"],
                cwd=source,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=offline_environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            archives = list((source / "dist").glob("*.tar.gz"))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0]) as archive:
                names = set(archive.getnames())
            wheel = next((source / "dist").glob("*.whl"))
            environment = Path(temporary) / "wheel-environment"
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(environment)],
                text=True,
                capture_output=True,
                check=False,
                env=offline_environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(
                [
                    str(environment / "bin" / "python"),
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    str(wheel),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=offline_environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            executable = environment / "bin" / "sprint-loop"
            for arguments in (
                ("--help",),
                ("--version",),
                ("status", "--root", str(self.root), "--json"),
            ):
                result = subprocess.run(
                    [str(executable), *arguments],
                    text=True,
                    capture_output=True,
                    check=False,
                    env=offline_environment,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        for document in (
            "docs/v1_final_software_specification.md",
            "docs/multi_sprint_plan.md",
            "docs/controller-v1/1/sprint_spec.md",
            "docs/controller-v1/1/sprint_checklist.md",
        ):
            self.assertTrue(any(name.endswith(f"/{document}") for name in names), document)

    def test_ownership_lock_rejects_concurrent_attempt(self) -> None:
        """A held non-worktree ownership lock prevents a second run."""
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        run_lock, _ = _lock_paths(git_dir)
        with advisory_lock(run_lock, exclusive=True):
            code, _, stderr = self.invoke(
                ["run", "--root", str(self.root), "--server-url", "opaque"]
            )
        self.assertEqual(code, 2)
        self.assertIn("run_already_active", stderr)
        self.assertFalse((self.root / "info").exists())

    def test_controller_locks_use_stable_git_metadata_anchors(self) -> None:
        """Ownership and persistence use dedicated directories Git never rewrites."""
        run_lock, persistence_lock = controller_locks(self.root)
        self.assertEqual((run_lock.name, persistence_lock.name), ("run", "persistence"))
        with advisory_lock(run_lock, exclusive=True):
            before = tuple(os.stat(path).st_ino for path in (run_lock, persistence_lock.parent))
            git(self.root, "config", "controller.lock-test", "true")
            git(self.root, "checkout", "-b", "lock-anchor-test")
            git(self.root, "checkout", "main")
            git(self.root, "config", "--unset", "controller.lock-test")
            self.assertEqual(
                tuple(os.stat(path).st_ino for path in (run_lock, persistence_lock.parent)), before
            )
        with advisory_lock(persistence_lock, exclusive=False):
            pass
        self.assertTrue(run_lock.is_dir())
        self.assertTrue(persistence_lock.is_dir())

    def test_replaced_run_anchor_cannot_create_a_second_owner(self) -> None:
        """The ownership namespace remains locked if the visible run anchor is replaced."""
        run_lock, _ = controller_locks(self.root)
        displaced = self.fixture.base / "displaced-run-lock"
        with ownership_lock(run_lock, blocking=False) as first:
            run_lock.rename(displaced)
            run_lock.mkdir()
            with self.assertRaises(ControllerError) as context:
                with ownership_lock(run_lock, blocking=False):
                    pass
            self.assertEqual(context.exception.code, "run_already_active")
            with self.assertRaises(ControllerError) as context:
                first.ensure_current()
            self.assertEqual(context.exception.code, "persistence_failed")

    def test_status_reports_real_separate_process_ownership_without_lock_metadata(self) -> None:
        """Status derives activity from the authoritative lock holder and persisted process identity."""
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        release = context.Event()
        results = context.Queue()
        process = context.Process(
            target=run_paused_after_validating,
            args=(
                ["run", "--root", str(self.root), "--server-url", "opaque"],
                ready,
                release,
                results,
            ),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(timeout=5))
            paths = runtime_paths(self.root, "foundation", 1)
            paths.lock_metadata.unlink()
            code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
            self.assertEqual(code, 0, stderr)
            self.assertTrue(json.loads(stdout)["process_running"])
            release.set()
            process.join(timeout=5)
            self.assertFalse(process.is_alive())
            self.assertEqual(results.get(timeout=1), 4)
            code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
            self.assertEqual(code, 0, stderr)
            self.assertFalse(json.loads(stdout)["process_running"])
        finally:
            release.set()
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            results.close()

    def test_foreign_lock_holder_does_not_make_a_prior_run_active(self) -> None:
        """A kernel lock held by a different PID cannot be attributed to persisted state."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        run_lock, _ = controller_locks(self.root)
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        process = context.Process(target=hold_lock, args=(str(run_lock), ready))
        process.start()
        try:
            self.assertTrue(ready.wait(timeout=5))
            code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
            self.assertEqual(code, 0, stderr)
            self.assertFalse(json.loads(stdout)["process_running"])
        finally:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()

    def test_status_never_acquires_the_exclusive_ownership_lock(self) -> None:
        """Status uses the shared persistence lock and read-only ownership evidence only."""
        from opencode_sprint_loop import cli

        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        observed: list[tuple[str, bool]] = []
        original_lock = cli.advisory_lock

        @contextlib.contextmanager
        def record_lock(path: Path, **kwargs: object) -> Any:
            observed.append((path.name, bool(kwargs["exclusive"])))
            with original_lock(path, **kwargs):  # type: ignore[arg-type]
                yield

        with patch("opencode_sprint_loop.cli.advisory_lock", side_effect=record_lock):
            code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(observed, [("persistence", False)])

    def test_status_rejects_pid_only_liveness_when_linux_identity_is_available(self) -> None:
        """Null persisted process identity cannot fall back to a reusable PID on Linux."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        metadata = json.loads(paths.lock_metadata.read_text(encoding="utf-8"))
        state["process"]["process_start"] = None
        metadata["process_start"] = None
        paths.state.write_text(json.dumps(state), encoding="utf-8")
        paths.lock_metadata.write_text(json.dumps(metadata), encoding="utf-8")
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        run_lock, _ = _lock_paths(git_dir)
        with advisory_lock(run_lock, exclusive=True):
            code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertFalse(json.loads(stdout)["process_running"])

    def test_status_ignores_malformed_lock_metadata(self) -> None:
        """Malformed descriptive metadata cannot override authoritative ownership."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        paths = runtime_paths(self.root, "foundation", 1)
        metadata = json.loads(paths.lock_metadata.read_text(encoding="utf-8"))
        metadata["schema_version"] = True
        paths.lock_metadata.write_text(json.dumps(metadata), encoding="utf-8")
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        run_lock, _ = _lock_paths(git_dir)
        with advisory_lock(run_lock, exclusive=True):
            code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertTrue(json.loads(stdout)["process_running"])

    def test_status_ignores_semantically_invalid_lock_timestamp(self) -> None:
        """Descriptive timestamps cannot override matching OS lock/process evidence."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        paths = runtime_paths(self.root, "foundation", 1)
        metadata = json.loads(paths.lock_metadata.read_text(encoding="utf-8"))
        metadata["started_at"] = "2026-99-99T99:99:99Z"
        paths.lock_metadata.write_text(json.dumps(metadata), encoding="utf-8")
        run_lock, _ = controller_locks(self.root)
        with advisory_lock(run_lock, exclusive=True):
            code, stdout, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertTrue(json.loads(stdout)["process_running"])

    def test_separate_process_ownership_lock_rejects_run(self) -> None:
        """Ownership is enforced across controller processes, not only threads."""
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        run_lock, _ = _lock_paths(git_dir)
        ready = multiprocessing.Event()
        process = multiprocessing.Process(target=hold_lock, args=(str(run_lock), ready))
        process.start()
        try:
            self.assertTrue(ready.wait(timeout=5))
            code, _, stderr = self.invoke(
                ["run", "--root", str(self.root), "--server-url", "opaque"]
            )
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

    def test_ignored_stale_lock_metadata_does_not_block_first_run(self) -> None:
        """An aggregate ignored `info/` record still permits only stale lock metadata."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        paths.lock_metadata.parent.mkdir(parents=True)
        paths.lock_metadata.write_text("not-json\n", encoding="utf-8")
        exclude = Path(git(self.root, "rev-parse", "--git-path", "info/exclude"))
        if not exclude.is_absolute():
            exclude = self.root / exclude
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write("info/\n")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 4, stderr)
        self.assertEqual(
            json.loads(paths.lock_metadata.read_text(encoding="utf-8"))["schema_version"], 1
        )

    def test_tracked_lock_metadata_is_never_replaced(self) -> None:
        """A user-tracked lock.json cannot be claimed as stale controller metadata."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        paths.lock_metadata.parent.mkdir(parents=True)
        paths.lock_metadata.write_text("tracked user content\n", encoding="utf-8")
        git(self.root, "add", str(paths.lock_metadata.relative_to(self.root)))
        git(self.root, "commit", "-m", "Track unrelated lock metadata")
        before = paths.lock_metadata.read_bytes()
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)
        self.assertEqual(paths.lock_metadata.read_bytes(), before)
        self.assertEqual(git(self.root, "status", "--porcelain=v2"), "")

    def test_lock_metadata_tracking_race_preserves_the_existing_file(self) -> None:
        """Metadata installation never overwrites a path tracked after initial validation."""
        from opencode_sprint_loop.cli import _write_lock_metadata

        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        paths.info_dir.mkdir(parents=True)
        paths.lock_metadata.write_text("stale metadata\n", encoding="utf-8")
        with patch("opencode_sprint_loop.cli.is_tracked_path", side_effect=[False, True]):
            with self.assertRaises(ControllerError) as context:
                _write_lock_metadata(self.root, paths.lock_metadata, new_state(config))
        self.assertEqual(context.exception.code, "inconsistent_persistence")
        self.assertEqual(paths.lock_metadata.read_text(encoding="utf-8"), "stale metadata\n")

    def test_ignored_stale_lock_metadata_cannot_hide_extra_directories(self) -> None:
        """The stale-lock exception rejects ignored controller trees with any extra content."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        paths.lock_metadata.parent.mkdir(parents=True)
        paths.lock_metadata.write_text("not-json\n", encoding="utf-8")
        (paths.info_dir / "unexpected").mkdir()
        exclude = Path(git(self.root, "rev-parse", "--git-path", "info/exclude"))
        if not exclude.is_absolute():
            exclude = self.root / exclude
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write("info/\n")
        code, _, stderr = self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])
        self.assertEqual(code, 2)
        self.assertIn("dirty_sprint_repository", stderr)

    def test_post_lock_revalidation_rejects_a_run_created_by_a_racing_process(self) -> None:
        """A run that passed initial preflight cannot overwrite a later completed run."""
        from opencode_sprint_loop import cli

        context = multiprocessing.get_context("fork")
        waiting_for_ownership = context.Event()
        release_ownership = context.Event()
        results = context.Queue()
        parent_pid = os.getpid()
        original_lock = cli.ownership_lock

        @contextlib.contextmanager
        def delay_child_ownership(path: Path, **kwargs: object) -> Any:
            if os.getpid() != parent_pid and path.name == "run":
                waiting_for_ownership.set()
                if not release_ownership.wait(timeout=10):
                    raise RuntimeError("timed out waiting to continue ownership race")
            with original_lock(path, **kwargs) as lock:
                yield lock

        with patch("opencode_sprint_loop.cli.ownership_lock", side_effect=delay_child_ownership):
            started = context.Event()
            process = context.Process(
                target=invoke_in_child,
                args=(
                    ["run", "--root", str(self.root), "--server-url", "opaque"],
                    started,
                    results,
                ),
            )
            process.start()
            try:
                self.assertTrue(started.wait(timeout=5))
                self.assertTrue(waiting_for_ownership.wait(timeout=5))
                code, _, stderr = self.invoke(
                    ["run", "--root", str(self.root), "--server-url", "opaque"]
                )
                self.assertEqual(code, 4, stderr)
                paths = runtime_paths(self.root, "foundation", 1)
                before_state = paths.state.read_bytes()
                before_events = paths.events.read_bytes()
                release_ownership.set()
                process.join(timeout=5)
                self.assertFalse(process.is_alive())
                child_code, _, child_stderr = results.get(timeout=1)
                self.assertEqual(child_code, 2)
                self.assertIn("run_already_exists", child_stderr)
                self.assertEqual(paths.state.read_bytes(), before_state)
                self.assertEqual(paths.events.read_bytes(), before_events)
            finally:
                release_ownership.set()
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                results.close()

    def test_post_lock_revalidation_rejects_changed_repository_assumptions(self) -> None:
        """Changes after initial preflight fail closed before creating runtime artifacts."""
        from opencode_sprint_loop import cli

        variants = (
            ("wrong_branch", lambda fixture: fixture.set_managed_branch()),
            ("missing_remote", lambda fixture: fixture.remove_managed_remote()),
            (
                "dirty_managed_repository",
                lambda fixture: fixture.make_dirty(fixture.managed, "untracked"),
            ),
            (
                "git_operation_in_progress",
                lambda fixture: fixture.mark_git_operation(fixture.managed, "merge"),
            ),
        )
        for expected, mutate in variants:
            with self.subTest(expected=expected):
                fixture, _ = self.variant()
                original_lock = cli.ownership_lock
                mutated = False

                @contextlib.contextmanager
                def mutate_after_ownership(path: Path, **kwargs: object) -> Any:
                    nonlocal mutated
                    with original_lock(path, **kwargs) as lock:  # type: ignore[arg-type]
                        if path.name == "run" and not mutated:
                            mutate(fixture)
                            mutated = True
                        yield lock

                with patch(
                    "opencode_sprint_loop.cli.ownership_lock", side_effect=mutate_after_ownership
                ):
                    code, _, stderr = self.invoke(
                        ["run", "--root", str(fixture.root), "--server-url", "opaque"]
                    )
                self.assertTrue(mutated)
                self.assertEqual(code, 2)
                self.assertIn(expected, stderr)
                self.assertFalse((fixture.root / "info").exists())
                run_lock, _ = controller_locks(fixture.root)
                with advisory_lock(run_lock, exclusive=True, blocking=False):
                    pass

    def test_status_waits_for_a_complete_normal_transition(self) -> None:
        """Status cannot expose the event-ahead snapshot during a locked transition."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        _, persistence_lock = _lock_paths(git_dir)
        initial = persist_initial(new_state(config), paths.events, paths.state, persistence_lock)
        context = multiprocessing.get_context("fork")
        state_write_started = context.Event()
        release_state_write = context.Event()
        transition_results = context.Queue()
        transition_process = context.Process(
            target=pause_transition_in_child,
            args=(
                initial,
                paths.events,
                paths.state,
                persistence_lock,
                state_write_started,
                release_state_write,
                transition_results,
            ),
        )
        transition_process.start()
        self.assertTrue(state_write_started.wait(timeout=5))
        status_started = context.Event()
        status_results = context.Queue()
        status_process = context.Process(
            target=invoke_in_child,
            args=(["status", "--root", str(self.root), "--json"], status_started, status_results),
        )
        status_process.start()
        try:
            self.assertTrue(status_started.wait(timeout=5))
            with self.assertRaises(queue.Empty):
                status_results.get(timeout=0.25)
            release_state_write.set()
            transition_process.join(timeout=5)
            self.assertFalse(transition_process.is_alive())
            self.assertIsNone(transition_results.get(timeout=1))
            status_process.join(timeout=5)
            self.assertFalse(status_process.is_alive())
            code, stdout, stderr = status_results.get(timeout=1)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["state"], "validating")
        finally:
            release_state_write.set()
            for process in (transition_process, status_process):
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            transition_results.close()
            status_results.close()

    def test_status_observes_a_complete_first_transition(self) -> None:
        """Concurrent status sees no run or a complete initialized run during creation."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        _, persistence_lock = _lock_paths(git_dir)
        context = multiprocessing.get_context("fork")
        state_write_started = context.Event()
        release_state_write = context.Event()
        transition_results = context.Queue()
        transition_process = context.Process(
            target=pause_initial_transition_in_child,
            args=(
                new_state(config),
                paths.events,
                paths.state,
                persistence_lock,
                state_write_started,
                release_state_write,
                transition_results,
            ),
        )
        transition_process.start()
        self.assertTrue(state_write_started.wait(timeout=5))
        status_started = context.Event()
        status_results = context.Queue()
        status_process = context.Process(
            target=invoke_in_child,
            args=(["status", "--root", str(self.root), "--json"], status_started, status_results),
        )
        status_process.start()
        try:
            self.assertTrue(status_started.wait(timeout=5))
            with self.assertRaises(queue.Empty):
                status_results.get(timeout=0.25)
            release_state_write.set()
            transition_process.join(timeout=5)
            self.assertFalse(transition_process.is_alive())
            self.assertIsNone(transition_results.get(timeout=1))
            status_process.join(timeout=5)
            self.assertFalse(status_process.is_alive())
            code, stdout, stderr = status_results.get(timeout=1)
            self.assertEqual(code, 0, stderr)
            status = json.loads(stdout)
            self.assertTrue(status["run_exists"])
            self.assertEqual(status["state"], "initializing")
        finally:
            release_state_write.set()
            for process in (transition_process, status_process):
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            transition_results.close()
            status_results.close()

    def test_status_rejects_state_for_different_sprint(self) -> None:
        """Persisted state cannot be projected for a different configured sprint."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        state["sprint"] = 2
        paths.state.write_text(json.dumps(state), encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)

    def test_status_rejects_reason_that_differs_from_last_event(self) -> None:
        """Mutable state cannot replace the append-only transition reason."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        paths = runtime_paths(self.root, "foundation", 1)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        state["reason"]["message"] = "different reason"
        paths.state.write_text(json.dumps(state), encoding="utf-8")
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("inconsistent_persistence", stderr)

    def test_status_rejects_semantically_forged_event_history(self) -> None:
        """Matching sequence and final state cannot disguise an impossible transition history."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        paths = runtime_paths(self.root, "foundation", 1)
        events = [
            json.loads(line) for line in paths.events.read_text(encoding="utf-8").splitlines()
        ]
        events[0]["type"] = "state.entered"
        paths.events.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("corrupt_event_log", stderr)

    def test_hardlinked_runtime_artifacts_and_locks_are_rejected(self) -> None:
        """Controller writes cannot target runtime or lock inodes aliased outside their roots."""
        self.assertEqual(
            self.invoke(["run", "--root", str(self.root), "--server-url", "opaque"])[0], 4
        )
        paths = runtime_paths(self.root, "foundation", 1)
        outside_event = self.fixture.base / "outside-events"
        outside_event.write_text("outside\n", encoding="utf-8")
        paths.events.unlink()
        os.link(outside_event, paths.events)
        code, _, stderr = self.invoke(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("persistence_failed", stderr)
        self.assertEqual(outside_event.read_text(encoding="utf-8"), "outside\n")

        git_dir = Path(git(self.root, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.root / git_dir).resolve()
        lock = self.fixture.base / "controller.lock"
        outside_lock = self.fixture.base / "outside-lock"
        outside_lock.write_text("outside\n", encoding="utf-8")
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists():
            lock.unlink()
        os.link(outside_lock, lock)
        with self.assertRaises(ControllerError) as context:
            with advisory_lock(lock, exclusive=True):
                pass
        self.assertEqual(context.exception.code, "persistence_failed")
        self.assertEqual(outside_lock.read_text(encoding="utf-8"), "outside\n")

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
        for state_name in ("stopped", "failed", "finished"):
            with self.subTest(terminal_state=state_name):
                state = new_state(config)
                state["state"] = state_name
                if state_name != "finished":
                    state["reason"] = {"code": "test", "message": "test", "details": {}}
                with self.assertRaises(ControllerError) as context:
                    validate_state(state)
                self.assertEqual(context.exception.code, "corrupt_state")

    def test_date_only_timestamps_are_rejected_for_state_and_events(self) -> None:
        """Date-only values cannot be accepted where RFC 3339 UTC timestamps are required."""
        config = load_config(self.root)
        for field in ("created_at", "updated_at"):
            with self.subTest(state_field=field):
                state = new_state(config)
                state[field] = "2026-01-01"
                with self.assertRaises(ControllerError) as context:
                    validate_state(state)
                self.assertEqual(context.exception.code, "corrupt_state")
        event = transition_event(
            new_state(config), "run.started", "initializing", {"previous_state": None}
        )
        event["timestamp"] = "2026-01-01"
        with self.assertRaises(ControllerError) as context:
            append_event(self.root / "info" / "events.jsonl", event)
        self.assertEqual(context.exception.code, "persistence_failed")

    def test_state_reserves_null_active_invocation_in_sprint_one(self) -> None:
        """Sprint 1 rejects active invocation data it cannot represent in status."""
        state = new_state(load_config(self.root))
        state["future"] = {"value": True}
        state["server"]["future"] = "reserved"
        state["server"]["url"] = "http://later.example.invalid"
        state["active_invocation"] = {"role": "builder"}
        state["ci"]["checks"] = [{"name": "later"}]
        with self.assertRaises(ControllerError) as context:
            validate_state(state)
        self.assertEqual(context.exception.code, "corrupt_state")

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
        state["process"]["active"] = False
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
        _, persistence_lock = controller_locks(self.root)
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

    def test_transition_allows_failure_from_validating_state(self) -> None:
        """The documented validating-to-failed recovery transition remains reachable."""
        config = load_config(self.root)
        paths = runtime_paths(self.root, config.multisprint, config.sprint)
        _, persistence_lock = controller_locks(self.root)
        state = persist_initial(new_state(config), paths.events, paths.state, persistence_lock)
        state = transition(state, paths.events, paths.state, persistence_lock, "validating")
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

        original = transitions.append_event_at

        def fail_validating(
            directory: int, name: str, path: Path, event: dict[str, object]
        ) -> None:
            if event["state"] == "validating":
                raise OSError("injected")
            original(directory, name, path, event)  # type: ignore[arg-type]

        with patch("opencode_sprint_loop.transitions.append_event_at", side_effect=fail_validating):
            code, _, stderr = self.invoke(
                ["run", "--root", str(self.root), "--server-url", "opaque"]
            )
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
