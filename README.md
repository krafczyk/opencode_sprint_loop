# OpenCode Sprint Loop Controller

The OpenCode Sprint Loop Controller is a Python workflow controller for durable implementation, audit, and CI loops. It is distinct from a sprint-history repository used to run a product sprint and from the optional Neovim plugin in `opencode_sprint_loop.lua/`.

## Sprint 1 Status

Sprint 1 implements the controller foundation only:

- Versioned `sprint_config.json` validation.
- Read-only Git and submodule preflight checks.
- Linux advisory locks, durable state, append-only events, and status output.
- The `sprint-loop` command surface.

It does **not** yet run OpenCode sessions, make commits or pushes, monitor GitHub CI, provide functional pause/resume/stop controls, or implement the Neovim commands. A valid `run` intentionally ends with `blocked / execution_not_implemented` and a non-zero exit status.

## Requirements

- Linux mkchad container environment.
- Python 3.11 or newer.
- Git.

The package has no runtime dependencies. The pinned build requirement is declared in `pyproject.toml` for reproducible package builds.

## Installation

Create a virtual environment and install the package:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps .
.venv/bin/sprint-loop --help
```

For development:

```bash
python3 -m pip install --no-deps -e .
python3 -m opencode_sprint_loop.cli --help
```

## Commands

```bash
sprint-loop run --root <sprint-repository> --server-url <url>
sprint-loop status --root <sprint-repository>
sprint-loop status --root <sprint-repository> --json
sprint-loop pause --root <sprint-repository>
sprint-loop resume --root <sprint-repository> --server-url <url>
sprint-loop stop --root <sprint-repository>
```

In Sprint 1, `run` requires `--server-url` to preserve the final V1 command shape but treats it as opaque input. It does not contact, parse, log, or persist the value. Do not embed credentials in the argument.

`pause`, `resume`, and `stop` are reserved command names and return `feature_not_implemented` without changing state or Git repositories.

## Sprint Repository Layout

The controller expects a clean Git worktree structured as follows:

```text
sprint-repo/
|-- AGENTS.md
|-- sprint_config.json
|-- .opencode/agents/
|   |-- builder.md
|   |-- auditor.md
|   `-- ci-fixer.md
|-- docs/
|   `-- <multisprint>/
|       |-- multisprint_spec.md
|       `-- <sprint>/
|           |-- sprint_spec.md
|           `-- sprint_checklist.md
`-- repositories/
    `-- <managed-repository>/       # initialized Git submodule
```

V1 configuration is collection-shaped but Sprint 1 accepts exactly one managed repository. The sprint repository and managed repository must have no staged, unstaged, untracked, or in-progress Git operation state. The configured managed branch must be checked out, its configured remote must exist, and its HEAD must match the sprint repository gitlink.

## Configuration

`sprint_config.json` uses schema version 1:

```json
{
  "schema_version": 1,
  "multisprint": "authentication",
  "sprint": 3,
  "repositories": [
    {
      "name": "backend",
      "path": "repositories/backend",
      "branch": "sprint/authentication-3",
      "remote": "origin"
    }
  ],
  "documents": {
    "multisprint_spec": "docs/authentication/multisprint_spec.md",
    "sprint_spec": "docs/authentication/3/sprint_spec.md",
    "sprint_checklist": "docs/authentication/3/sprint_checklist.md"
  },
  "agents": {
    "builder": "builder",
    "auditor": "auditor",
    "ci_fixer": "ci-fixer"
  },
  "models": {
    "builder": "provider/medium-model",
    "auditor": "provider/strong-model",
    "ci_fixer": "provider/medium-model"
  },
  "pre_ci_audit": { "enabled": true, "max_rounds": 2 },
  "limits": {
    "max_implementation_cycles": 6,
    "max_ci_fix_attempts": 3,
    "invocation_timeout_seconds": 7200,
    "server_unavailable_grace_seconds": 30
  },
  "ci": {
    "provider": "github",
    "poll_interval_seconds": 30,
    "allow_skipped": true,
    "allow_neutral": true,
    "zero_checks": "error"
  }
}
```

Configuration paths are relative to the sprint root and may not escape it. Configuration rejects duplicate JSON keys, unknown fields, unsupported schema versions, invalid identifiers, and missing or empty required documents. Agent files must be present in `.opencode/agents/`.

## Runtime Records and Status

A successful Sprint 1 placeholder run creates:

```text
info/<multisprint>/<sprint>/
|-- state.json
|-- events.jsonl
`-- lock.json
```

The event log records `run.started`, `state.entered`, and `run.blocked`; state ends at `blocked` with reason code `execution_not_implemented`. Sprint 1 does not create checkpoint commits, so these controller-owned runtime files remain uncommitted until Sprint 4 introduces checkpoint commits.

Use `status --json` for integrations. It emits one JSON object and writes diagnostics only to standard error. With no existing run it reports `run_exists: false` and does not create worktree or runtime files.

## Verification

Run the default offline test suite:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src
python3 -m pip wheel --no-deps --wheel-dir /tmp/opencode-sprint-loop-wheel .
git diff --check
```

The tests create temporary local Git repositories and submodules. They do not require a model, OpenCode server, GitHub account, network access, or global Git identity.
