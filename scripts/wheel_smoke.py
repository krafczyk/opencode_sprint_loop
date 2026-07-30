"""Verify the built wheel works from an isolated environment outside the checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def _run(arguments: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    """Run one bounded local verification command or report its output safely."""
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"wheel smoke command failed: {' '.join(arguments)}")
    return completed.stdout


def main() -> int:
    """Install exactly one wheel without an index and verify package-owned identity."""
    parser = argparse.ArgumentParser()
    parser.add_argument("task_root")
    parser.add_argument("--wheel-dir", default="dist")
    arguments = parser.parse_args()
    task_root = Path(arguments.task_root).resolve()
    wheel_directory = Path(arguments.wheel_dir).resolve()
    if not task_root.is_dir():
        raise SystemExit("wheel smoke task root does not exist")
    wheels = sorted(wheel_directory.glob("*.whl")) if wheel_directory.is_dir() else []
    if len(wheels) != 1:
        raise SystemExit("wheel smoke requires exactly one built wheel")
    environment = {
        "HOME": str(task_root / "home"),
        "PATH": os.environ["PATH"],
        "PYTHONNOUSERSITE": "1",
        "XDG_CACHE_HOME": str(task_root / "xdg-cache"),
        "XDG_CONFIG_HOME": str(task_root / "xdg-config"),
        "XDG_DATA_HOME": str(task_root / "xdg-data"),
        "XDG_STATE_HOME": str(task_root / "xdg-state"),
    }
    virtualenv = task_root / "venv"
    _run([sys.executable, "-m", "venv", str(virtualenv)], cwd=task_root, environment=environment)
    python = virtualenv / "bin" / "python"
    command = virtualenv / "bin" / "sprint-loop"
    _run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheels[0])],
        cwd=task_root,
        environment=environment,
    )
    version = _run([str(command), "--version"], cwd=task_root, environment=environment).strip()
    document = json.loads(
        _run([str(command), "component-info", "--json"], cwd=task_root, environment=environment)
    )
    metadata = json.loads(
        _run(
            [
                str(python),
                "-c",
                "import importlib.resources; "
                "print(importlib.resources.files('opencode_sprint_loop').joinpath('component.json').read_text())",
            ],
            cwd=task_root,
            environment=environment,
        )
    )
    if (
        document.get("controller_version") != version
        or metadata.get("component_version") != version
        or document.get("component_id") != "sprint-loop-controller"
    ):
        raise SystemExit("wheel metadata does not match the installed controller")
    print("wheel smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
