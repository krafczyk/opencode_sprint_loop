"""Create and demonstrate one complete Sprint 1 placeholder repository."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run(command: list[str], *, cwd: Path | None = None, expected: int = 0) -> None:
    """Run one demonstration command and print its complete safe output."""
    result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    print(f"$ {' '.join(command)}")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode != expected:
        raise SystemExit(f"Expected exit {expected}, received {result.returncode}")


def git(root: Path, *arguments: str) -> None:
    """Run one local fixture Git command with deterministic diagnostics."""
    run(["git", *arguments], cwd=root)


def write_config(root: Path, *, schema_version: int = 1) -> None:
    """Write the complete valid Sprint 1 configuration fixture."""
    config = {
        "schema_version": schema_version,
        "multisprint": "demo",
        "sprint": 1,
        "repositories": [{"name": "managed", "path": "repositories/managed", "branch": "main", "remote": "origin"}],
        "documents": {
            "multisprint_spec": "docs/demo/multisprint_spec.md",
            "sprint_spec": "docs/demo/1/sprint_spec.md",
            "sprint_checklist": "docs/demo/1/sprint_checklist.md",
        },
        "agents": {"builder": "builder", "auditor": "auditor", "ci_fixer": "ci-fixer"},
        "models": {"builder": "demo/medium", "auditor": "demo/strong", "ci_fixer": "demo/medium"},
        "pre_ci_audit": {"enabled": True, "max_rounds": 1},
        "limits": {
            "max_implementation_cycles": 1,
            "max_ci_fix_attempts": 1,
            "invocation_timeout_seconds": 60,
            "server_unavailable_grace_seconds": 30,
        },
        "ci": {"provider": "github", "poll_interval_seconds": 30, "allow_skipped": True, "allow_neutral": True, "zero_checks": "error"},
    }
    (root / "sprint_config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def create_fixture(base: Path) -> Path:
    """Create a committed sprint repository and initialized managed submodule."""
    remote = base / "managed-remote.git"
    seed = base / "managed-seed"
    root = base / "sprint"
    run(["git", "init", "--bare", str(remote)], cwd=base)
    seed.mkdir()
    git(seed, "init", "--initial-branch=main")
    git(seed, "config", "user.email", "demo@example.invalid")
    git(seed, "config", "user.name", "Sprint Demo")
    (seed / "managed.txt").write_text("baseline\n", encoding="utf-8")
    git(seed, "add", "managed.txt")
    git(seed, "commit", "-m", "Initial managed commit")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "main")

    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.email", "demo@example.invalid")
    git(root, "config", "user.name", "Sprint Demo")
    (root / "AGENTS.md").write_text("demo instructions\n", encoding="utf-8")
    for document in ("multisprint_spec.md", "1/sprint_spec.md", "1/sprint_checklist.md"):
        path = root / "docs" / "demo" / document
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("demo document\n", encoding="utf-8")
    for name in ("builder", "auditor", "ci-fixer"):
        path = root / ".opencode" / "agents" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("demo agent\n", encoding="utf-8")
    write_config(root)
    git(root, "add", "AGENTS.md", ".opencode", "docs", "sprint_config.json")
    git(root, "commit", "-m", "Add sprint inputs")
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-b", "main", str(remote), "repositories/managed")
    git(root, "commit", "-m", "Add managed submodule")
    return root


def start_paused_controller(executable: str, root: Path) -> tuple[subprocess.Popen[str], Path]:
    """Start a real controller and pause it after its durable validating transition."""
    executable_path = Path(executable).resolve()
    interpreter = executable_path.parent / "python"
    if not interpreter.exists():
        interpreter = Path(sys.executable)
    ready = root / ".controller-ready"
    release = root / ".controller-release"
    code = """
import sys
import time
from pathlib import Path
from unittest.mock import patch
from opencode_sprint_loop import cli

ready = Path(sys.argv[1])
release = Path(sys.argv[2])
original = cli.transition

def pause_after_validating(*arguments, **keywords):
    result = original(*arguments, **keywords)
    destination = keywords.get("destination", arguments[4])
    if destination == "validating":
        ready.write_text("ready\\n", encoding="utf-8")
        while not release.exists():
            time.sleep(0.01)
    return result

with patch("opencode_sprint_loop.cli.transition", side_effect=pause_after_validating):
    raise SystemExit(cli.main(sys.argv[3:]))
"""
    process = subprocess.Popen(
        [str(interpreter), "-c", code, str(ready), str(release), "run", "--root", str(root), "--server-url", "opaque-demo-url"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while not ready.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            stdout, stderr = process.communicate()
            raise SystemExit(f"Controller did not reach validating state: {stdout}{stderr}")
        time.sleep(0.01)
    return process, release


@contextlib.contextmanager
def hold_run_lock(root: Path) -> object:
    """Hold the controller ownership anchor for the explicit OS-lock rejection check."""
    lock_path = root / ".git" / "opencode-sprint-loop" / "run"
    lock_path.mkdir(parents=True)
    descriptor = os.open(lock_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def create_named_fixture(base: Path, name: str) -> Path:
    """Create an isolated fixture variant without changing the primary demo."""
    fixture_base = base / name
    fixture_base.mkdir()
    return create_fixture(fixture_base)


def require_no_runtime_artifacts(root: Path) -> None:
    """Fail the demonstration if an invalid run created controller runtime paths."""
    if (root / "info").exists():
        raise SystemExit(f"Invalid run unexpectedly created runtime artifacts: {root / 'info'}")


def main() -> int:
    """Run the repeatable Sprint 1 demonstration against isolated temporary fixtures."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", default="sprint-loop")
    parser.add_argument("--keep", type=Path)
    arguments = parser.parse_args()
    if shutil.which(arguments.executable) is None:
        raise SystemExit(f"Controller executable not found: {arguments.executable}")
    temporary = None
    if arguments.keep is None:
        temporary = tempfile.TemporaryDirectory(prefix="sprint-loop-demo-")
        base = Path(temporary.name)
    else:
        base = arguments.keep.resolve()
        base.mkdir(parents=True, exist_ok=False)
    root = create_fixture(base)
    print(f"\nDemo sprint root: {root}")
    run([arguments.executable, "--help"])
    run([arguments.executable, "--version"])
    run([arguments.executable, "status", "--root", str(root)])
    run([arguments.executable, "status", "--root", str(root), "--json"])

    active_root = create_named_fixture(base, "active-controller")
    print(f"\nActive controller fixture: {active_root}")
    active, release = start_paused_controller(arguments.executable, active_root)
    run([arguments.executable, "status", "--root", str(active_root), "--json"])
    run([arguments.executable, "run", "--root", str(active_root), "--server-url", "opaque-demo-url"], expected=2)
    release.write_text("release\n", encoding="utf-8")
    stdout, stderr = active.communicate(timeout=10)
    print(f"$ {arguments.executable} run --root {active_root} --server-url opaque-demo-url")
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="")
    if active.returncode != 4:
        raise SystemExit(f"Expected active controller exit 4, received {active.returncode}")

    lock_root = create_named_fixture(base, "lock-rejection")
    print(f"\nOS lock rejection fixture: {lock_root}")
    with hold_run_lock(lock_root):
        run([arguments.executable, "run", "--root", str(lock_root), "--server-url", "opaque-demo-url"], expected=2)
        run([arguments.executable, "status", "--root", str(lock_root), "--json"])
    require_no_runtime_artifacts(lock_root)

    dirty_root = create_named_fixture(base, "dirty-managed")
    dirty_managed = dirty_root / "repositories" / "managed"
    (dirty_managed / "user-work.txt").write_text("uncommitted user work\n", encoding="utf-8")
    print(f"\nDirty managed no-mutation fixture: {dirty_root}")
    git(dirty_managed, "status", "--short")
    run([arguments.executable, "run", "--root", str(dirty_root), "--server-url", "opaque-demo-url"], expected=2)
    git(dirty_managed, "status", "--short")
    require_no_runtime_artifacts(dirty_root)

    schema_root = create_named_fixture(base, "unknown-schema")
    write_config(schema_root, schema_version=2)
    print(f"\nUnknown schema no-mutation fixture: {schema_root}")
    git(schema_root, "status", "--short")
    run([arguments.executable, "run", "--root", str(schema_root), "--server-url", "opaque-demo-url"], expected=2)
    git(schema_root, "status", "--short")
    require_no_runtime_artifacts(schema_root)

    print("No OpenCode server or GitHub credentials were used.")
    git(root, "submodule", "status")
    git(root, "status", "--short")
    git(root / "repositories" / "managed", "status", "--short")
    run([arguments.executable, "run", "--root", str(root), "--server-url", "opaque-demo-url"], expected=4)
    print((root / "info" / "demo" / "1" / "state.json").read_text(encoding="utf-8"), end="")
    print((root / "info" / "demo" / "1" / "events.jsonl").read_text(encoding="utf-8"), end="")
    run([arguments.executable, "status", "--root", str(root)])
    run([arguments.executable, "status", "--root", str(root), "--json"])
    if temporary is None:
        print(f"Demo repository retained at: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
