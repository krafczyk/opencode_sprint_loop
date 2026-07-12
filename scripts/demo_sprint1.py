"""Create and demonstrate one complete Sprint 1 placeholder repository."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
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


def write_config(root: Path) -> None:
    """Write the complete valid Sprint 1 configuration fixture."""
    config = {
        "schema_version": 1,
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


def main() -> int:
    """Run the repeatable Sprint 1 demonstration against one temporary repository."""
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
