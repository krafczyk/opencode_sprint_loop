# OpenCode Sprint Loop Controller

The OpenCode Sprint Loop Controller is a Python workflow controller for durable implementation, audit, and CI loops. It is distinct from a sprint-history repository used to run a product sprint and from the optional Neovim plugin in `opencode_sprint_loop.lua/`.

## Current Sprint

Sprint 3, [Neovim Client V1](docs/controller-v1/3/sprint_spec.md), is the current implementation sprint. Its [checklist](docs/controller-v1/3/sprint_checklist.md) tracks the thin Neovim launcher and progress client alongside its backward-compatible controller status additions.

## Implemented Status

Sprint 2 builds on the controller foundation with one deliberately non-mutating
OpenCode execution probe:

- Versioned `sprint_config.json` validation.
- Read-only Git and submodule preflight checks.
- Linux advisory locks, durable state, append-only events, and status output.
- The `sprint-loop` command surface.
- Credential-free OpenCode URL, Basic-auth, health/version, workspace, agent,
  provider, and model preflight.
- One fresh configured-Auditor session with exact ordered wildcard-deny then
  `StructuredOutput`-allow permissions,
  controller-validated JSON output, and bounded sanitized invocation evidence.

It does **not** yet run a product Builder, accept a staged handoff, make commits
or pushes, run audit rounds or CI, or provide functional controls/recovery. The
separate Neovim plugin provides asynchronous command delegation and status
presentation only; it does not implement workflow decisions. A successful probe intentionally ends with
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
release versions are `>=1.17.0, <1.19.0`.

Basic authentication is inherited only from `OPENCODE_SERVER_PASSWORD` and,
optionally, `OPENCODE_SERVER_USERNAME` (default `opencode` with a password).
Never put credentials in argv, configuration, or artifacts. Before creating a
runtime path or session, the controller validates health, default workspace, all
configured agents, and configured provider/model pairs. Provider capability
records must appear in the configured-provider collection, advertise models
through the documented object map, and have their provider ID in the documented
connected-provider list. Malformed provider records or model collections fail
closed.

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

The fresh Auditor probe uses the reviewed OpenCode `1.17.x` and `1.18.x`
last-match permission semantics:
its complete ordered session permission set is first
`{"permission":"*","pattern":"*","action":"deny"}`, then
`{"permission":"StructuredOutput","pattern":"*","action":"allow"}`.
This permits only the built-in structured-output mechanism; shell, repository,
web, task, MCP, and external tools remain denied. The controller validates this
exact order in the created-session response, not merely a deny-only subset. The
probe has a deterministic title. Successful terminal transcript evidence must bind the exact submitted
prompt, created session ID, and configured Auditor/provider/model identity;
absent or mismatched evidence fails closed. The documented assistant `sessionID`
(or supported top-level/`info` `session_id` alias) must reconcile to the created
session. Every retained part must carry exact documented `sessionID` and
`messageID` associations; the reconstructed prompt record retains the exact
parent linkage. Every `tool` part requires a bounded documented `tool` string;
a compatible `name` may only agree, never substitute, and only exact
`StructuredOutput` is permitted. A present session-status entry must use the
documented `1.17.x`/`1.18.x` object with a string `type`; only an absent entry
means missing status.
On timeout, uncertain terminal evidence, or cooperative `SIGINT`/`SIGTERM`, the
controller sends exactly one best-effort abort, uses one monotonic ten-second
confirmation deadline for every status-only follow-up observation, and records
both the strict JSON-boolean abort acknowledgement and whether `idle`
confirmation was obtained. It never retrieves messages after abort. `SIGINT` and
`SIGTERM` return statuses 130 and 143 respectively. Ambiguous session creation
is not retried and an orphan session may remain. Interrupted work is not resumed
or repaired in Sprint 2. When cancellation confirmation is unavailable, the
controller writes a concise sanitized diagnostic that the session may remain
active.

Runtime readers and writers use descriptor-anchored paths and distinct controller-owned Git-metadata lock directories. Git-managed files such as `HEAD` and `config` are never lock anchors because ordinary Git operations can replace them. State/event payloads reject credential-bearing keys, provider tokens (including variable-length stateless GitHub App installation tokens shaped `ghs_<APPID>_<JWT>`), and every URI query value or fragment regardless of its key. CLI diagnostics redact those values along with URI user-info and HTTP authorization values. GitLab forms include `glpat-`, `glcbt-`, `glptt-`, `glrt-`, `glimt-`, `glsoat-`, `gldt-`, `glrtr-`, `glft-`, `glagent-`, `glwt-`, `glffct-`, and `gloas-`. URI user-info is recognized for every URI scheme, including database and SSH URLs.

Transcript sanitization applies the same recognizable-credential checks to
opaque object keys and values. A collision introduced by safe key redaction is
rejected rather than silently dropping one field.

Status and existing-run validation cross-check invocation metadata, prompt,
result, transcript, state, and terminal event identities. Missing or
contradictory terminal evidence fails with `inconsistent_invocation_record`.
Documented result/transcript-before-metadata write-ahead prefixes remain
nonterminal interruption evidence and are never promoted to probe success. If
an immutable result or transcript installs before its temporary cleanup or
directory-sync report fails, the controller preserves that prefix and does not
append metadata or events claiming the installed artifact is unavailable.
The only temporary-name prefix readers accept is the exact controller-generated
same-directory hard-link pair for that result or transcript; it remains
nonterminal evidence. Status reports it as `invocation_record_prefix` and tells
the operator to inspect it. Readers never remove the temporary name or promote
the prefix to success.

Use `status --json` for integrations. It emits one JSON object and writes diagnostics only to standard error. Its stable top-level fields are `schema_version`, `controller_version`, `sprint_root`, `run_exists`, `process_running`, `run_id`, `sprint`, `state`, `reason`, `active`, `commits`, `audit`, `ci`, `counters`, `checklist`, `last_event`, and `updated_at`. The complete V1 Sprint 1 JSON schema is defined in [the status contract](docs/controller-v1/1/sprint_spec.md#12-status-json-contract).

`sprint` contains `multisprint` and `index`; `reason` contains safe `code` and
`message`; `active` contains `role`, `invocation_id`, `session_id`, `status`,
and `interaction`. For an inactive persisted run, `status` and `interaction`
are null. The Sprint 2 probe projects `status: "running"` and
`interaction: null`. These are backward-compatible, read-only status additions:
Sprint 3 does not persist real question state or permit probe questions. Status remains local and never exposes the server URL, prompt,
result, transcript, or credentials.

When no run exists, `run_exists` is `false`, `process_running` is `false`, and every run-specific field from `run_id` through `updated_at` is `null`. No-run status does not create worktree or runtime files. For a placeholder run, `active` is an object containing null `role`, `invocation_id`, `session_id`, `status`, and `interaction` fields; `last_event` identifies the final `run.blocked` record.

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
The controller uses documented synchronous `POST /session/<id>/message` for
the one structured prompt. It does not use the message-list endpoint, including
for transcript capture. The returned assistant message and parts are retained
as bounded sanitized evidence; the exact persisted prompt is reconstructed as
the paired user record only through the returned assistant parent ID. OpenCode
may expose the result at top level or in `info` under `structured` or
`structured_output`; aliases for structured output, role, message ID, session
ID, error, route identity, and supported parent spellings must agree.
Contradictions, missing/wrong part session/message associations, malformed or
non-exact tool identities, errors, permission requests, identity mismatches, and missing output fail
closed. The long HTTP call receives the complete remaining monotonic invocation
budget and runs in a daemon worker while the
controller blocks on a bounded queue wait, so idle waiting uses negligible CPU
but the configured invocation timeout remains a wall-clock deadline. The daemon
worker timestamps completion with monotonic time; after dequeue, evidence is
accepted only if it completed strictly before the deadline and any cancellation
timestamp. A response completed before a later cancellation is retained, while
late results are ignored. Timeout or signal cancellation sends one abort and checks only session status, at most
once per second; it never retries the non-idempotent prompt.
Builder handoff, commits, audits, CI, functional controls/recovery, and Neovim
remain deliberately unimplemented.

### Repeatable sanitized real-server demonstration

Run this opt-in procedure only in a disposable fixture. Build a fresh wheel and
install it into a fresh virtual environment. Create a clean sprint repository
with one initialized managed submodule, `.opencode/agents/` roles, and a valid
configured provider/model. Start the installed `opencode serve` **outside** the
controller at that fixture root with a synthetic `OPENCODE_SERVER_PASSWORD`.
Do not put credentials in command arguments, fixture files, captured output, or
notes.

After the server is healthy, run the installed wheel's `sprint-loop run` with
only the credential-free loopback origin. While it is active, use
`sprint-loop status --json` and an ordinary OpenCode client (`opencode attach
<origin>`) to observe the new `0001-auditor` session. Record only safe hashes,
field shapes, event names, commands, and repository status—not provider output
or transcript content. Confirm the exact permission order above, the persisted
prompt/result/transcript wrapper shapes, the ordered events, both repository
statuses (including ignored files) before and after the run, and final
`blocked/execution_not_implemented` state. Repeat the clean-status check after
generated files settle while the server remains active. Remove only disposable
generated dependency artifacts, stop the external server, and remove the
fixture, virtual environment, logs, and environment-provided synthetic
credentials.

The deterministic local HTTP test additionally captures complete direct-adapter
and full-controller request bodies using the same fixture and route sequence. It
asserts the title and two permission rules for `POST /session`; the agent,
provider/model route, exact prompt, complete JSON schema, and no `retryCount`
for `POST /session/<id>/message`; and that the captured bodies are identical.

## Historical Sprint 1 Demonstration

`scripts/demo_sprint1.py` is retained as a historical Sprint 1 foundation
demonstration. It deliberately uses an opaque demo URL and transition mocking, so
it is not runnable against the current Sprint 2 package: `run` now requires a
healthy, already-running OpenCode server rooted at the sprint repository. Use the
Sprint 2 demonstration and offline test commands above instead.
