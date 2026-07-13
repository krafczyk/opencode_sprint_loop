# OpenCode Sprint Loop Controller

The OpenCode Sprint Loop Controller is a Python workflow controller for durable implementation, audit, and CI loops. It is distinct from a sprint-history repository used to run a product sprint and from the optional Neovim plugin in `opencode_sprint_loop.lua/`.

## Current Sprint

Sprint 2, [OpenCode Execution Layer](docs/controller-v1/2/sprint_spec.md), is the current implementation sprint. Its [checklist](docs/controller-v1/2/sprint_checklist.md) tracks the planned server validation, fresh-session execution probe, durable invocation records, and interruption behavior.

## Implemented Status

Sprint 2 builds on the controller foundation with one deliberately non-mutating
OpenCode execution probe:

- Versioned `sprint_config.json` validation.
- Read-only Git and submodule preflight checks.
- Linux advisory locks, durable state, append-only events, and status output.
- The `sprint-loop` command surface.
- Credential-free OpenCode URL, Basic-auth, health/version, workspace, agent,
  provider, and model preflight.
- One fresh configured-Auditor session with wildcard-deny permissions,
  controller-validated JSON output, and bounded sanitized invocation evidence.

It does **not** yet run a product Builder, accept a staged handoff, make commits
or pushes, run audit rounds or CI, provide functional controls/recovery, or
implement Neovim commands. A successful probe intentionally ends with
`blocked / execution_not_implemented` and a non-zero exit status.

## Requirements

- Linux mkchad container environment.
- Python 3.11 or newer.
- Git.

The package has no runtime dependencies. The exact build backend is pinned in `pyproject.toml`; the `dev` extra and `requirements-dev.lock` pin the development toolchain and its resolved dependencies.

## Installation

Create a virtual environment and install the package:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps .
.venv/bin/sprint-loop --help
```

The source-install command allows pip to provision the pinned build backend and
may therefore require package-index access. In an air-gapped, pre-provisioned
environment where the pinned build backend is already installed, use:

```bash
.venv/bin/python -m pip install --no-index --no-deps --no-build-isolation .
```

For development:

```bash
python3 -m pip install --constraint requirements-dev.lock -e '.[dev]'
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

In Sprint 2, `run` requires an already-running OpenCode server rooted at the
sprint repository. The URL must be a credential-free absolute HTTP(S) origin,
such as `http://127.0.0.1:4096`; paths, query strings, fragments, and user-info
are rejected. A trailing port separator without a port, such as
`http://127.0.0.1:`, is also invalid rather than selecting the default port.
HTTP is only appropriate on the trusted local mkchad transport;
use HTTPS and server authentication outside that boundary. Supported OpenCode
release versions are `>=1.17.0, <1.18.0`.

Basic authentication is inherited only from `OPENCODE_SERVER_PASSWORD` and,
optionally, `OPENCODE_SERVER_USERNAME` (default `opencode` with a password).
Never put credentials in argv, configuration, or artifacts. Before creating a
runtime path or session, the controller validates health, default workspace, all
configured agents, and configured provider/model pairs. Provider capability
records must explicitly report `connected: true` and advertise models through
the documented object map; missing or non-boolean connection state and list-shaped
model collections are rejected as malformed server responses.

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

V1 configuration is collection-shaped but Sprint 1 accepts exactly one managed repository. The sprint repository and managed repository must have no staged, unstaged, untracked (including ignored), or in-progress Git operation state. Tracked paths marked `assume-unchanged` or `skip-worktree` are rejected because those index flags can hide changes from Git status. Dirty nested submodules also block the managed repository preflight. The configured managed branch must be checked out, its configured remote must exist, and its HEAD must match the sprint repository gitlink. Preflight disables Git filesystem-monitor hooks and accepts both absorbed and old-form initialized submodules.

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

Configuration field rules:

| Field | Validation |
| --- | --- |
| `schema_version` | Integer `1`; booleans are rejected. |
| `multisprint`, repository `name`, agent names | Lowercase identifier matching `^[a-z0-9][a-z0-9_-]{0,63}$`. |
| `sprint`, audit rounds, limits, CI interval | Positive integers; booleans are rejected. |
| `repositories` | Exactly one object. Its path is non-empty, relative, contained by the root, not the root itself, and may not contain `..`. |
| Repository branch and remote | Non-empty single-line strings without NUL. |
| `documents` | Three distinct, non-empty regular files contained by the sprint root. |
| `agents` | Each configured name requires `.opencode/agents/<name>.md`. |
| `models` | `provider/model` with non-empty, whitespace-free components; additional `/` characters are allowed in the model identifier. |
| `pre_ci_audit.enabled`, CI allow flags | JSON booleans. Sprint 1 preserves audit-enabled state without acting on it. |
| `ci` | Provider is `github`; `zero_checks` is a lower-case identifier. `error` is the recommended value. |

Schema version 1 rejects duplicate JSON keys and unknown fields at every level. Configuration paths are relative to the sprint root and may not escape it.

## Runtime Records and Status

A successful Sprint 2 probe creates uncommitted controller runtime records:

```text
info/<multisprint>/<sprint>/
|-- state.json
|-- events.jsonl
`-- lock.json
```

It also creates `invocations/<multisprint>/<sprint>/0001-auditor/` containing
owner-only `metadata.json`, exact `prompt.md`, validated `result.json`, and a
sanitized bounded `transcript.json`. The event log records `run.started`,
`state.entered`, `server.validated`, `agent.started`, `agent.completed`, and
`run.blocked`; state ends at `blocked` with `execution_not_implemented`. No
checkpoint commit is made until Sprint 4.

The fresh Auditor probe has a wildcard-deny tool override and a deterministic
title. On timeout, uncertain terminal evidence, or cooperative `SIGINT`/
`SIGTERM`, the controller sends exactly one best-effort abort, waits up to ten
seconds for idle or terminal evidence, then captures any available sanitized
transcript before recording the interruption. `SIGINT` and `SIGTERM` return
statuses 130 and 143 respectively. Ambiguous session creation is not retried
and an orphan session may remain. Interrupted work is not resumed or repaired
in Sprint 2.

Runtime readers and writers use descriptor-anchored paths and distinct controller-owned Git-metadata lock directories. Git-managed files such as `HEAD` and `config` are never lock anchors because ordinary Git operations can replace them. State/event payloads reject credential-bearing keys and common credential-bearing values, and CLI diagnostics redact URI user-info, query values, fragments, and HTTP authorization values.

Status and existing-run validation cross-check invocation metadata, prompt,
result, transcript, state, and terminal event identities. Missing or
contradictory terminal evidence fails with `inconsistent_invocation_record`.
Documented result/transcript-before-metadata write-ahead prefixes remain
nonterminal interruption evidence and are never promoted to probe success.

Use `status --json` for integrations. It emits one JSON object and writes diagnostics only to standard error. Its stable top-level fields are `schema_version`, `controller_version`, `sprint_root`, `run_exists`, `process_running`, `run_id`, `sprint`, `state`, `reason`, `active`, `commits`, `audit`, `ci`, `counters`, `checklist`, `last_event`, and `updated_at`. The complete V1 Sprint 1 JSON schema is defined in [the status contract](docs/controller-v1/1/sprint_spec.md#12-status-json-contract).

`sprint` contains `multisprint` and `index`; `reason` contains safe `code` and
`message`; `active` contains `role`, `invocation_id`, and `session_id` while the
probe runs. Status remains local and never exposes the server URL, prompt,
result, transcript, or credentials.

When no run exists, `run_exists` is `false`, `process_running` is `false`, and every run-specific field from `run_id` through `updated_at` is `null`. No-run status does not create worktree or runtime files. For a placeholder run, `active` is an object containing null `role`, `invocation_id`, and `session_id` fields; `last_event` identifies the final `run.blocked` record.

## Verification

Run the default offline test suite:

```bash
python3 -m pip install --constraint requirements-dev.lock -e '.[dev]'
python3 -m unittest discover -s tests -v
python3 -m compileall -q src
python3 -m ruff check src tests
python3 -m ruff format --check src tests scripts
python3 -m mypy
python3 -m build --no-isolation
git diff --check
```

The tests create temporary local Git repositories and submodules. They do not require a model, OpenCode server, GitHub account, network access, or global Git identity.

Default Sprint 2 tests use deterministic fakes and a local fake HTTP server.
Real-server checks are opt-in: start a supported server rooted at a clean sprint
repository outside the controller, inherit any authentication, and pass only its
credential-free origin. The non-mutating preflight smoke test can then be run as:

```bash
SPRINT_LOOP_REAL_SPRINT_ROOT=/path/to/sprint-repo \
SPRINT_LOOP_REAL_SERVER_URL=http://127.0.0.1:4096 \
python3 -m unittest \
  tests.unit.test_opencode_execution.OpenCodeExecutionTests.test_opt_in_real_server_preflight -v
```

These variables contain no credentials; Basic authentication remains inherited
through the OpenCode variables documented above. The complete real-server exit
demonstration additionally runs `sprint-loop run`, observes its fresh session in
an ordinary OpenCode client, and checks the invocation records and final block.
Builder handoff, commits, audits, CI, functional controls/recovery, and Neovim
remain deliberately unimplemented.

## Sprint 1 Demonstration

Build and install a wheel in a clean environment, then run the demonstration against a real temporary Git repository and initialized submodule:

```bash
python3 -m build --no-isolation
python3 -m venv /tmp/sprint-loop-demo-venv
/tmp/sprint-loop-demo-venv/bin/python -m pip install --no-deps dist/opencode_sprint_loop-0.1.0-py3-none-any.whl
/tmp/sprint-loop-demo-venv/bin/python scripts/demo_sprint1.py --executable /tmp/sprint-loop-demo-venv/bin/sprint-loop
```

Pass `--keep /tmp/sprint-loop-demo` to retain the generated repository for manual inspection. The script shows help, version, human and JSON no-run status, a real controller paused in `validating` with cross-process status, explicit OS-lock rejection, the real submodule, placeholder execution, `state.json`, ordered events, and human and JSON post-run status without a live OpenCode server or GitHub credentials.
