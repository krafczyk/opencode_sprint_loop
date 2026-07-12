"""Read-only Git inspection used by Sprint 1 preflight."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import SprintConfig
from .errors import ControllerError
from .security import redact_diagnostic


@dataclass(frozen=True, slots=True)
class GitRepository:
    """Read-only identity information for a Git worktree."""

    root: Path
    git_dir: Path
    head: str


def _run(path: Path, *arguments: str, allow_failure: bool = False) -> str:
    """Run a read-only Git command with deterministic diagnostics."""
    environment = {
        "LC_ALL": "C",
        "LANG": "C",
        "PATH": os.environ.get("PATH", ""),
        # Git status may otherwise refresh cached stat information in the index.
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *arguments],
            cwd=path,
            env=environment,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, UnicodeError) as error:
        raise ControllerError("root_not_git_worktree", f"Cannot inspect Git repository at {path}") from error
    if result.returncode != 0 and not allow_failure:
        detail = redact_diagnostic(result.stderr.strip() or result.stdout.strip() or "Git command failed")
        raise ControllerError("root_not_git_worktree", f"Git inspection failed in {path}: {detail}")
    return result.stdout


def inspect_worktree(root: Path, *, expected_root: Path, error_code: str) -> GitRepository:
    """Validate one non-bare worktree and return its stable identity."""
    top_level = _run(root, "rev-parse", "--show-toplevel").strip()
    resolved_top_level = Path(top_level).resolve()
    if resolved_top_level != expected_root:
        raise ControllerError(error_code, f"Expected Git worktree root {expected_root}, found {resolved_top_level}")
    if _run(root, "rev-parse", "--is-bare-repository").strip() == "true":
        raise ControllerError(error_code, f"Bare repository is not supported: {expected_root}")
    head = _run(root, "rev-parse", "--verify", "HEAD").strip()
    git_dir_value = _run(root, "rev-parse", "--git-dir").strip()
    git_dir = (root / git_dir_value).resolve() if not Path(git_dir_value).is_absolute() else Path(git_dir_value).resolve()
    return GitRepository(expected_root, git_dir, head)


def _ensure_clean(repository: GitRepository, code: str, label: str, *, allowed_untracked: set[str] | None = None) -> None:
    """Fail when porcelain v2 reports any tracked or untracked change."""
    status = _run(
        repository.root,
        "status",
        "--porcelain=v2",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
        "--ignore-submodules=none",
    )
    records = [record for record in status.split("\0") if record]
    disallowed = []
    for record in records:
        if record.startswith("? ") and allowed_untracked is not None and record[2:] in allowed_untracked:
            continue
        if record.startswith("! ") and allowed_untracked is not None:
            ignored_path = record[2:].rstrip("/")
            if record[2:] in allowed_untracked or _ignored_tree_contains_only(repository.root, ignored_path, allowed_untracked):
                continue
        disallowed.append(record)
    if disallowed:
        raise ControllerError(
            code,
            f"{label} must be clean; commit, remove, or use 'git stash --all' for its changes first: {repository.root}",
        )


def _ignored_tree_contains_only(root: Path, relative: str, allowed: set[str]) -> bool:
    """Allow Git's aggregate ignored record only for the stale metadata exception."""
    tree = root / relative
    if not tree.is_dir() or tree.is_symlink():
        return False
    for current, directories, files in os.walk(tree, followlinks=False):
        if any((Path(current) / directory).is_symlink() for directory in directories):
            return False
        for filename in files:
            candidate = Path(current) / filename
            if candidate.is_symlink() or candidate.relative_to(root).as_posix() not in allowed:
                return False
    return True


def _ensure_no_operation(repository: GitRepository, label: str) -> None:
    """Reject Git operations that make repository state ambiguous."""
    markers = {
        "MERGE_HEAD": "merge",
        "CHERRY_PICK_HEAD": "cherry-pick",
        "REVERT_HEAD": "revert",
        "BISECT_LOG": "bisect",
    }
    for marker, operation in markers.items():
        if (repository.git_dir / marker).exists():
            raise ControllerError("git_operation_in_progress", f"{label} has an active {operation}: {repository.root}")
    if (repository.git_dir / "rebase-apply").exists() or (repository.git_dir / "rebase-merge").exists():
        raise ControllerError("git_operation_in_progress", f"{label} has an active rebase: {repository.root}")


def _ensure_submodule(root: GitRepository, config: SprintConfig) -> GitRepository:
    """Validate the configured managed repository as an initialized gitlink."""
    repository = config.repositories[0]
    relative = repository.path.relative_to(root.root).as_posix()
    stage = _run(root.root, "ls-files", "--stage", "--", relative).strip()
    fields = stage.split(maxsplit=3)
    if len(fields) < 3 or fields[0] != "160000":
        raise ControllerError("invalid_submodule", f"Managed repository is not a tracked gitlink: {repository.path}")
    gitlink_sha = fields[1]
    modules = root.root / ".gitmodules"
    if modules.is_symlink() or not modules.is_file():
        raise ControllerError("invalid_submodule", f"Missing .gitmodules for managed repository: {repository.path}")
    registered = _run(
        root.root,
        "config",
        "--null",
        "--file",
        str(modules),
        "--get-regexp",
        r"^submodule\..*\.path$",
        allow_failure=True,
    )
    registered_paths = [record.partition("\n")[2] for record in registered.split("\0") if record]
    if relative not in registered_paths:
        raise ControllerError("invalid_submodule", f"Managed repository is not registered in .gitmodules: {repository.path}")
    if not repository.path.exists():
        raise ControllerError("uninitialized_submodule", f"Managed submodule is not initialized; initialize it before running: {repository.path}")
    try:
        managed = inspect_worktree(repository.path, expected_root=repository.path, error_code="uninitialized_submodule")
    except ControllerError as error:
        if error.code == "uninitialized_submodule":
            raise
        raise ControllerError("uninitialized_submodule", f"Managed submodule is not a usable worktree: {repository.path}") from error
    module_root = root.git_dir / "modules"
    if module_root.exists():
        try:
            managed.git_dir.relative_to(module_root)
        except ValueError:
            if (module_root / relative).exists() or not (repository.path / ".git").is_dir():
                raise ControllerError("uninitialized_submodule", f"Managed repository is not the registered submodule; initialize it before running: {repository.path}")
    elif not (repository.path / ".git").is_dir():
        raise ControllerError("uninitialized_submodule", f"Managed repository is not the registered submodule; initialize it before running: {repository.path}")
    if managed.head != gitlink_sha:
        raise ControllerError("submodule_sha_mismatch", "Managed HEAD differs from the sprint gitlink; restore the recorded submodule commit")
    return managed


def validate_root(root: Path) -> GitRepository:
    """Validate the basic sprint repository worktree identity without mutation."""
    return inspect_worktree(root, expected_root=root, error_code="root_not_worktree_root")


def validate_preflight(root: Path, config: SprintConfig, *, require_clean: bool, allowed_root_untracked: set[str] | None = None) -> GitRepository:
    """Validate read-only repository assumptions or raise ``ControllerError`` without mutation."""
    agents_path = root / "AGENTS.md"
    if agents_path.is_symlink() or not agents_path.is_file():
        raise ControllerError("missing_required_file", f"Missing root AGENTS.md: {root / 'AGENTS.md'}")
    sprint = validate_root(root)
    _ensure_no_operation(sprint, "Sprint repository")
    managed = _ensure_submodule(sprint, config)
    _ensure_no_operation(managed, "Managed repository")
    branch = _run(managed.root, "symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True).strip()
    if not branch:
        raise ControllerError("wrong_branch", "Managed repository is detached; check out the configured branch")
    repository = config.repositories[0]
    if branch != repository.branch:
        raise ControllerError("wrong_branch", "Managed repository is not on the configured branch; check it out before running")
    remote = _run(managed.root, "remote", "get-url", repository.remote, allow_failure=True).strip()
    if not remote:
        raise ControllerError("missing_remote", "Managed repository has no configured remote; add it before running")
    if require_clean:
        _ensure_clean(managed, "dirty_managed_repository", "Managed repository")
        # A dirty submodule also marks its parent gitlink dirty. Validate the
        # managed worktree first so users receive the actionable root cause.
        _ensure_clean(sprint, "dirty_sprint_repository", "Sprint repository", allowed_untracked=allowed_root_untracked)
    return sprint
