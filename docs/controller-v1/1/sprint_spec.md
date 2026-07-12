# Controller V1 Sprint 1 Specification: Controller Foundation

## Document Status

This document defines the implementation scope and acceptance contract for Sprint 1 of the Sprint Loop Controller V1 plan.

The following documents are authoritative over this sprint specification:

- `docs/v1_final_software_specification.md`
- `docs/multi_sprint_plan.md`

If this document appears to conflict with either authoritative document, implement the authoritative behavior and correct this document in the same change.

## 1. Sprint Goal

Create an installable Python controller foundation with:

- A stable command-line surface.
- Strict, versioned sprint configuration validation.
- Read-only Git repository preflight validation.
- Versioned durable state and append-only events.
- Atomic state persistence.
- Exclusive run ownership.
- Guarded initial state transitions.
- Stable human-readable and JSON status output.
- Deterministic tests using temporary Git repositories and submodules.

Sprint 1 does not execute OpenCode agents, commit implementation changes, push branches, monitor CI, or provide Neovim commands.

## 2. Sprint Outcome

At the end of Sprint 1, a user can install `sprint-loop`, point it at a correctly structured clean sprint repository, and exercise the controller foundation.

For a valid repository, `run` acquires ownership, persists the initial transition history, and exits in the intentional placeholder state:

```text
blocked / execution_not_implemented
```

The placeholder demonstrates configuration, repository validation, locking, state persistence, events, and status without pretending later workflow stages exist.

For an invalid repository, `run` exits non-zero with an actionable diagnostic and creates no runtime state, event, lock metadata, Git index changes, or commits.

## 3. Scope

### 3.1 Included

Sprint 1 includes:

1. Python packaging and the `sprint-loop` executable.
2. CLI parsing for the final V1 command names.
3. Configuration loading and validation.
4. Sprint root canonicalization and Git worktree validation.
5. Managed submodule and branch validation.
6. Clean worktree and in-progress Git operation validation.
7. Versioned state and event models.
8. Atomic state writes and durable event appends.
9. Operating-system locking plus descriptive lock metadata.
10. Guarded transitions needed by the Sprint 1 placeholder flow.
11. Human-readable and JSON status projections.
12. A test harness capable of constructing realistic sprint repository fixtures.
13. Initial installation and CLI contract documentation.

### 3.2 Explicitly Excluded

Sprint 1 must not implement:

- Any OpenCode HTTP request or health check.
- Agent runner interfaces, prompts, sessions, results, or transcripts.
- Builder, Auditor, or CI Fixer behavior.
- Agent availability or model/provider availability checks against a server.
- Implementation repository staging, commits, or pushes.
- Sprint repository checkpoint commits.
- GitHub authentication, check discovery, CI polling, or logs.
- Functional pause, resume, or stop coordination with a live workflow process.
- Neovim plugin implementation.
- Multi-repository execution.
- Nested dependency submodule management.
- Automatic recovery from partial agent work.
- A server multiplexer or custom web interface.

Code added solely for one of these deferred capabilities is out of scope unless it is a minimal type or interface required by a Sprint 1 public contract.

## 4. Implementation Constraints

### 4.1 Runtime

- Support Python 3.11 or newer.
- Define project metadata and the console entry point in `pyproject.toml`.
- Use a `src/` package layout.
- Keep the default test suite offline and credential-free.
- Use timezone-aware UTC timestamps serialized in RFC 3339 form.
- Use `pathlib.Path` or equivalent safe path handling internally.
- Represent workflow states and event types with constrained types rather than unchecked strings at transition call sites.
- Document public Python APIs with concise docstrings describing contracts, errors, and side effects.

The implementation may select focused dependencies for model validation, CLI handling, or testing. Every runtime dependency must be documented and justified, and all dependencies must be pinned through the selected reproducible development/build approach. Sprint 1 must not add HTTP, GitHub, Neovim, daemon, database, or web-framework dependencies.

V1 targets Linux in the mkchad container environment. Sprint 1 does not need to implement or test portable ownership semantics for Windows or macOS.

### 4.2 Suggested Source Boundaries

The exact module names may vary, but the implementation must preserve equivalent responsibilities:

```text
pyproject.toml
src/
`-- opencode_sprint_loop/
    |-- __init__.py
    |-- cli.py
    |-- config.py
    |-- errors.py
    |-- events.py
    |-- git.py
    |-- locking.py
    |-- paths.py
    |-- state.py
    |-- status.py
    `-- transitions.py
tests/
|-- fixtures/
|-- integration/
`-- unit/
```

Do not create abstractions for OpenCode or GitHub until their implementation sprints.

### 4.3 External Commands

Git inspection must:

- Invoke Git without a shell command string.
- Pass arguments as an array.
- Set the intended working directory explicitly.
- Capture standard output and standard error separately.
- Prefer stable machine-readable output such as porcelain v2 and NUL delimiters.
- Set `LC_ALL=C` for diagnostics that cannot be obtained in a structured format.
- Never invoke a destructive or mutating Git command during preflight.

## 5. CLI Contract

The installed executable is `sprint-loop`.

### 5.1 Global Behavior

- `sprint-loop --help` exits zero and lists every V1 command.
- `sprint-loop --version` exits zero and prints the package version.
- Usage errors exit non-zero and write diagnostics to standard error.
- Human-readable command output uses standard output for successful results and standard error for failures.
- JSON output writes exactly one JSON document to standard output and writes diagnostics only to standard error.
- Unhandled tracebacks must not be the normal response to user or repository errors.
- File paths shown in diagnostics should be canonical absolute paths when that aids resolution.

### 5.2 `run`

Final V1 command shape:

```bash
sprint-loop run --root <sprint-repository> --server-url <url>
```

Sprint 1 requirements:

- `--root` is required.
- `--server-url` is accepted and required to preserve the final V1 command shape.
- Sprint 1 treats the server URL as an opaque non-empty argument, performs no parsing or network request, and does not persist its value.
- The controller must not log the server URL. Users must not place credentials in it; URL syntax, authentication, sanitization, health, and workspace validation are Sprint 2 work.
- `run` checks for existing persisted state before worktree cleanliness validation. Sprint 1 rejects any existing run for the configured sprint and reports `run_already_exists`; restart and resume policy is implemented in later sprints.
- `run` performs every read-only preflight check before creating runtime directories or files.
- After initial successful validation, `run` acquires ownership and repeats all concurrency-sensitive state and repository checks before creating runtime files. It then creates a run ID, persists Sprint 1 events and state, enters `blocked` with reason `execution_not_implemented`, releases ownership, and exits non-zero.
- The placeholder non-zero exit communicates that no sprint workflow was executed; it is not an internal controller crash.

The expected Sprint 1 transition sequence is:

```text
no run
  -> initializing
  -> validating
  -> blocked / execution_not_implemented
```

The `validating` state records that all mutation-free preflight checks already succeeded. Validation failures before ownership create no run.

### 5.3 `status`

```bash
sprint-loop status --root <sprint-repository>
sprint-loop status --root <sprint-repository> --json
```

Requirements:

- `--root` is required.
- Status is read-only with respect to workflow state and the Git worktree and does not acquire the exclusive run lock. It may create the non-worktree persistence lock file under Git metadata when that local synchronization primitive does not yet exist.
- Status requires an existing directory that is the canonical root of a non-bare Git worktree and requires structurally valid sprint configuration so it can locate the active sprint state. It does not require a clean worktree.
- Status works when no runtime directories or state exist.
- Status works after the Sprint 1 placeholder run exits.
- Status detects malformed or unsupported state and returns a non-zero actionable error rather than guessing.
- Human output is concise and labels the sprint, state, reason, run ID, process activity, and last event when available.
- JSON output follows Section 12.

### 5.4 `pause`, `resume`, and `stop`

The commands and final argument shapes must exist in help output:

```bash
sprint-loop pause --root <sprint-repository>
sprint-loop resume --root <sprint-repository> --server-url <url>
sprint-loop stop --root <sprint-repository>
```

Sprint 1 does not implement live control coordination. Each command must:

- Parse and validate its required arguments.
- Return a concise non-zero `feature_not_implemented` diagnostic.
- Make no state, event, lock, or Git mutation.
- Avoid claiming that a control request was accepted.

The full semantics are implemented in Sprint 7. Reserving these commands now prevents command-name drift without prematurely implementing process control.

## 6. Sprint Repository Inputs

### 6.1 Required Root Files

The sprint repository must contain:

- `AGENTS.md`
- `sprint_config.json`
- The configured multisprint specification.
- The configured sprint specification.
- The configured sprint checklist.
- Project-local Builder, Auditor, and CI Fixer agent definition files.
- Exactly one configured managed repository as an initialized Git submodule.

### 6.2 Runtime Paths

For multisprint `<name>` and sprint `<index>`, Sprint 1 writes beneath:

```text
info/<name>/<index>/
|-- state.json
|-- events.jsonl
`-- lock.json
```

`invocations/`, audit records, CI records, and commit-message directories are not created in Sprint 1 because no invocation occurs.

Runtime paths must be derived from validated configuration values. A multisprint name must be safe as one path component, and a sprint index must be a positive integer.

Sprint 1 does not create checkpoint commits, so a successful placeholder run leaves these controller-owned runtime files uncommitted in the sprint worktree. This is expected for the Sprint 1 demonstration. `status` remains available because it does not enforce worktree cleanliness, and a subsequent `run` reports the existing persisted run before checking cleanliness. Sprint 4 introduces checkpoint commits that restore clean durable boundaries.

## 7. Configuration Contract

### 7.1 File and Encoding

- Configuration is read from `<root>/sprint_config.json`.
- The file must be UTF-8 JSON containing one object.
- Duplicate object keys must be rejected rather than silently taking the last value.
- Unknown top-level and nested fields must be rejected in schema version 1.
- Error messages must identify the field path without printing secrets.

### 7.2 Schema

Sprint 1 validates the complete V1 configuration structure and all foundation-relevant semantics. Later feature sprints may narrow currently reserved feature values when their behavior is implemented, but must not change field names or types without updating the authoritative V1 specification.

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
  "pre_ci_audit": {
    "enabled": true,
    "max_rounds": 2
  },
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

### 7.3 Field Rules

#### Identity

- `schema_version` must equal integer `1`; booleans do not count as integers.
- `multisprint` must match `^[a-z0-9][a-z0-9_-]*$` and be no longer than 64 characters.
- `sprint` must be an integer greater than zero; booleans are invalid.

#### Repositories

- `repositories` must contain exactly one object in V1.
- `name` follows the multisprint identifier rules and is unique by definition in V1.
- `path` must be a non-empty relative path within the sprint root.
- `path` must not contain `..`, resolve through a symlink outside the sprint root, or name the sprint root itself.
- `branch` and `remote` must be non-empty strings without NUL or line-break characters.

#### Documents

- Every document path must be relative, remain within the sprint root after resolution, and identify a regular file.
- The three configured document paths must resolve to distinct files.
- The controller does not parse document contents in Sprint 1, but an empty document is rejected as unusable input.

#### Agents

- Agent identifiers must match `^[a-z0-9][a-z0-9_-]*$` and be no longer than 64 characters.
- For each configured name, a corresponding project agent definition must exist at `.opencode/agents/<name>.md`.
- Sprint 1 verifies local files only. Server-side agent discovery is Sprint 2.

#### Models

- Every model value must contain a non-empty provider, `/`, and non-empty model identifier. Neither component may contain whitespace. The first `/` separates provider from model; additional `/` characters are permitted in the model identifier.
- Sprint 1 does not contact the provider or validate model availability.

#### Audit, Limits, and CI

- `pre_ci_audit.enabled` must be a JSON boolean. Sprint 1 preserves either value without defining execution semantics; Sprint 5 must define and document the disabled policy before using it.
- `pre_ci_audit.max_rounds` must be a positive integer.
- Every `limits` value must be a positive integer.
- `ci.provider` must equal `github` in V1.
- `ci.poll_interval_seconds` must be a positive integer.
- `allow_skipped` and `allow_neutral` must be booleans.
- `zero_checks` must match `^[a-z][a-z0-9_]*$` and be no longer than 64 characters. Sprint 1 preserves it without interpreting it; Sprint 6 defines the supported values and their CI semantics. `error` is the recommended value from the V1 specification.

## 8. Read-Only Repository Preflight

Preflight occurs before runtime directory creation. All checks apply to canonical paths. Existing state and event artifacts are checked first so controller-owned Sprint 1 runtime files cannot mask the more actionable `run_already_exists` result. Valid existing run artifacts produce `run_already_exists`; incomplete, malformed, or inconsistent artifacts produce the corresponding persistence error. Stale `lock.json` by itself is handled through ownership rules. Sprint 1 does not support starting a second run for the same configured sprint.

### 8.1 Sprint Repository Checks

The controller must verify:

1. `--root` exists and is a directory.
2. `git rev-parse --show-toplevel` succeeds.
3. The reported worktree top level equals the canonical `--root`; a child directory is not accepted as the root.
4. The repository is not bare.
5. The current HEAD resolves to a commit.
6. No staged, unstaged, or untracked files exist.
7. No merge, rebase, cherry-pick, revert, or bisect operation is in progress.
8. The configured managed path is a tracked gitlink with mode `160000`.
9. The submodule is registered in `.gitmodules` at the configured path.
10. The submodule is initialized and has a checked-out commit.
11. The checked-out managed repository HEAD equals the gitlink SHA recorded by the sprint repository index.

The clean check must include all untracked files and must not hide submodule dirtiness.

### 8.2 Managed Repository Checks

The controller must verify:

1. The configured path is a Git worktree.
2. Its worktree top level equals the canonical configured path.
3. It is not bare.
4. HEAD resolves to a commit.
5. The symbolic branch name exactly matches the configured branch; detached HEAD is rejected.
6. No staged, unstaged, or untracked files exist.
7. No merge, rebase, cherry-pick, revert, or bisect operation is in progress.
8. The configured remote exists.

Nested submodules are not managed in Sprint 1, but dirty nested submodule state reported by the managed repository causes the clean check to fail.

### 8.3 No-Mutation Guarantee

If any configuration or preflight check fails, the controller must not:

- Create `info/` or any child runtime path.
- Create or append `state.json`, `events.jsonl`, or `lock.json`.
- Change the Git index.
- Create a commit.
- Change branches, HEAD, remotes, submodule state, or worktree files.
- Run `git add`, `git commit`, `git stash`, `git reset`, `git checkout`, `git switch`, `git clean`, or equivalent mutations.

Tests must compare repository status and relevant HEAD/index identities before and after representative failures.

After acquiring the exclusive run lock, the controller must repeat the existing-state check and every repository assumption that could have changed since initial preflight. A post-lock failure creates no runtime files and releases ownership. This closes the race between two processes that initially validate the same clean repository.

## 9. Run Ownership and Locking

### 9.1 Lock Requirements

The controller must combine:

- An operating-system advisory exclusive lock used as the source of truth for ownership.
- A separate short-lived persistence lock that serializes event/state transition writes with status reads.
- Descriptive `lock.json` metadata for status and diagnostics.

A PID file or `lock.json` alone is insufficient.

### 9.2 Lock Location

The run-ownership and persistence advisory locks must use distinct dedicated directories under the sprint repository's Git metadata or another local non-versioned path derived from the canonical root. They must not require the sprint repository worktree to be dirty merely to hold an OS lock. Git-managed files such as `HEAD`, `config`, or the index must not be lock anchors, because ordinary Git operations can replace them while an advisory lock remains attached to the old inode. Status must not create `info/`, state, events, or any other worktree file.

`lock.json` is written under the active sprint information directory only after all preflight checks succeed and ownership is acquired.

### 9.3 Lock Metadata

`lock.json` contains:

```json
{
  "schema_version": 1,
  "run_id": "<uuid>",
  "pid": 1234,
  "process_start": "<platform process-start identity or null>",
  "hostname": "container-name",
  "started_at": "2026-07-12T14:20:00Z"
}
```

The process start identity should be recorded when reliably available on Linux so PID reuse can be distinguished.

When present, `process_start` is a non-empty opaque string containing a Linux boot identity and process start-time identity sufficient for exact comparison. It is serialized as a string so status does not depend on wall-clock conversion. It is null only when the host does not expose the required process metadata, in which case the OS lock remains authoritative.

### 9.4 Ownership Rules

- A second `run` must fail while the first process holds the OS lock.
- A process that acquires ownership must repeat concurrency-sensitive state and repository validation before mutation.
- For the first transition of a new run, the controller acquires the persistence lock before creating runtime paths and holds it through the first event append and state replacement. A concurrent status call therefore observes either a complete no-run view or a complete initialized-run view.
- Transition persistence takes the persistence lock exclusively for the event append and state replacement as one critical section.
- Status takes the persistence lock in shared mode while reading and validating events and state. It does not take the run-ownership lock and therefore remains available while a run is active, apart from a brief transition write.
- Stale or malformed `lock.json` must not block ownership when the OS lock is available.
- The controller may replace stale descriptive metadata only after acquiring the OS lock.
- The controller releases the OS lock on normal exit and best-effort error exit.
- Sprint 1 may retain the final `lock.json` as historical process metadata, but status must derive active ownership from the OS lock/process identity rather than file existence.

## 10. State Model

### 10.1 Location and Authority

`info/<multisprint>/<sprint>/state.json` is the current authoritative state snapshot.

### 10.2 Sprint 1 State Shape

The state model must reserve the stable V1 status concepts using null, empty, or zero values where later features have not run. Later sprints may add feature-specific fields under these objects while preserving schema-version compatibility rules:

```json
{
  "schema_version": 1,
  "run_id": "<uuid>",
  "multisprint": "authentication",
  "sprint": 3,
  "state": "blocked",
  "reason": {
    "code": "execution_not_implemented",
    "message": "Sprint execution begins in a later implementation sprint.",
    "details": {}
  },
  "process": {
    "pid": 1234,
    "process_start": null,
    "hostname": "container-name",
    "active": false
  },
  "server": {
    "url": null,
    "version": null
  },
  "active_invocation": null,
  "commits": {
    "local": {
      "backend": null
    },
    "pushed": {
      "backend": null
    }
  },
  "audit": {
    "phase": null,
    "pre_ci_round": 0,
    "pre_ci_max_rounds": 2,
    "latest_report": null,
    "remaining_effort": null
  },
  "ci": {
    "attempt": 0,
    "commit_sha": null,
    "status": "not_started",
    "checks": []
  },
  "counters": {
    "implementation_cycles": 0,
    "ci_fix_attempts": 0
  },
  "checklist": {
    "satisfied": 0,
    "partial": 0,
    "unsatisfied": 0,
    "not_evaluated": 0,
    "assessed_at": null,
    "items": []
  },
  "control": {
    "requested": null,
    "requested_at": null,
    "resume_state": null
  },
  "last_event_sequence": 3,
  "created_at": "2026-07-12T14:20:00Z",
  "updated_at": "2026-07-12T14:20:01Z",
  "terminal_result": null
}
```

### 10.3 State Rules

- `schema_version` must equal `1`.
- `run_id` must be a UUID generated once per new run.
- `state` must be one of `initializing`, `validating`, `implementing`, `committing`, `pre_ci_auditing`, `pushing`, `waiting_for_ci`, `fixing_ci`, `final_auditing`, `paused`, `blocked`, `stopping`, `stopped`, `failed`, or `finished`.
- `stopped`, `failed`, and `finished` are terminal. `paused` and `blocked` are non-terminal, although Sprint 1 implements no continuation from its placeholder block.
- `reason` is required for `blocked`, `failed`, and `stopped`; it is otherwise nullable.
- Repository keys in commit maps come from validated configuration.
- Sprint 1 never populates implementation SHAs.
- `process.active` records lifecycle intent at the persisted transition. The final Sprint 1 blocked transition writes `false` because no further workflow work follows it. Status computes actual `process_running` from current ownership/process evidence, so it may report running during the brief interval before process exit.
- Sprint 1 persists `server.url` and `server.version` as null. Sprint 2 owns URL validation and persistence.
- `terminal_result` is null for non-terminal states. Later sprints may populate an object containing a terminal status, safe reason, and terminal timestamp.
- Unknown state schema versions are rejected.
- Missing required fields or incorrect field types are corruption errors, not defaults.

## 11. Event Model

### 11.1 Location and Encoding

`info/<multisprint>/<sprint>/events.jsonl` is UTF-8 JSON Lines. Each physical line contains exactly one complete event object and ends with `\n`.

### 11.2 Event Envelope

```json
{
  "schema_version": 1,
  "sequence": 1,
  "timestamp": "2026-07-12T14:20:00Z",
  "run_id": "<uuid>",
  "type": "run.started",
  "state": "initializing",
  "payload": {}
}
```

Rules:

- Sequence numbers start at `1` for a new sprint event log and increase by exactly one.
- Existing event lines are never edited, reordered, or removed.
- The next sequence is determined from validated existing state and event data, not from an in-memory process-global counter alone.
- Sprint 1 permits only one run ID in a sprint event log. Starting another run for the same configured sprint is deferred.
- Unknown event schema versions, malformed or partial JSON lines, duplicate sequences, gaps, or a different run ID fail closed.
- Event payloads are objects and must not contain credentials.

### 11.3 Sprint 1 Events

A successful placeholder run records at least:

1. `run.started` in `initializing`.
2. `state.entered` for `validating`.
3. `run.blocked` for `blocked/execution_not_implemented`.

Each transition event payload includes the prior state and reason where applicable.

## 12. Status JSON Contract

### 12.1 Envelope

`status --json` emits one object with this stable Sprint 1 shape:

```json
{
  "schema_version": 1,
  "controller_version": "0.1.0",
  "sprint_root": "/workspace/authentication-sprints",
  "run_exists": true,
  "process_running": false,
  "run_id": "<uuid>",
  "sprint": {
    "multisprint": "authentication",
    "index": 3
  },
  "state": "blocked",
  "reason": {
    "code": "execution_not_implemented",
    "message": "Sprint execution begins in a later implementation sprint."
  },
  "active": {
    "role": null,
    "invocation_id": null,
    "session_id": null
  },
  "commits": {
    "local": {
      "backend": null
    },
    "pushed": {
      "backend": null
    }
  },
  "audit": {
    "phase": null,
    "pre_ci_round": 0,
    "pre_ci_max_rounds": 2,
    "remaining_effort": null
  },
  "ci": {
    "status": "not_started",
    "attempt": 0,
    "commit_sha": null
  },
  "counters": {
    "implementation_cycles": 0,
    "ci_fix_attempts": 0
  },
  "checklist": {
    "satisfied": 0,
    "partial": 0,
    "unsatisfied": 0,
    "not_evaluated": 0,
    "assessed_at": null
  },
  "last_event": {
    "sequence": 3,
    "type": "run.blocked",
    "timestamp": "2026-07-12T14:20:01Z"
  },
  "updated_at": "2026-07-12T14:20:01Z"
}
```

### 12.2 No-Run Projection

When no state exists, status exits zero with:

- `run_exists: false`
- `process_running: false`
- `run_id`, `sprint`, `state`, `reason`, `active`, `commits`, `audit`, `ci`, `counters`, `checklist`, `last_event`, and `updated_at` set to `null`
- A canonical `sprint_root`
- Valid schema and controller versions

No-run status must not create runtime directories.

### 12.3 Process Activity

`process_running` is computed from current ownership/process evidence. It is not copied blindly from state or inferred from `lock.json` existence.

### 12.4 Compatibility

- Required field names and meanings established here are stable for V1.
- Later sprints may add fields but must not remove or change existing fields without updating the authoritative V1 specification.
- JSON object key order is not a contract.
- Tests compare parsed objects or intentional snapshots, not incidental whitespace.

## 13. Guarded Transitions

The transition layer must:

- Centralize allowed state changes.
- Reject an unknown source or destination state.
- Reject a transition not present in the allowed transition table.
- Require a reason for blocked and failure states.
- Update `updated_at` and event sequence consistently.
- Persist an event and resulting state through one transition operation.
- Avoid direct state assignment from CLI handlers.

Sprint 1 allowed transitions are:

| Source | Destination | Event |
| --- | --- | --- |
| No run | `initializing` | `run.started` |
| `initializing` | `validating` | `state.entered` |
| `validating` | `blocked` | `run.blocked` |
| Any active Sprint 1 state after an internal persistence error | `failed` when persistence remains possible | `state.entered` |

The placeholder blocked state is non-terminal in the long-term V1 model but has no Sprint 1 continuation path.

## 14. Persistence Semantics

### 14.1 Atomic State Writes

State writes must:

1. Serialize and validate the complete new state before touching the current state file.
2. Create a temporary sibling file with restrictive default permissions.
3. Write the complete UTF-8 JSON document and trailing newline.
4. Flush and `fsync` the temporary file.
5. Replace `state.json` atomically with `os.replace` or an equivalent same-filesystem operation.
6. `fsync` the containing directory where supported.
7. Remove abandoned temporary files on handled failure where safe.

A failed state replacement must leave either the complete previous state or complete next state, never truncated JSON.

### 14.2 Event Appends

Event writes must:

1. Serialize and validate one complete event before opening for append.
2. Append one encoded line without rewriting prior bytes.
3. Flush and `fsync` before reporting success.
4. Use exclusive run ownership to prevent concurrent writers.
5. Hold the exclusive persistence lock through event append and matching state replacement.

A short write must be detected and completed or reported as a persistence failure. If process interruption leaves a partial final event line, subsequent reads report `corrupt_event_log`; Sprint 1 does not truncate or rewrite the append-only log automatically.

### 14.3 State/Event Consistency

Because two files cannot be atomically committed together, Sprint 1 must define and test one ordering. The required ordering is:

1. Append and sync the transition event.
2. Atomically write state referencing that event sequence.

On read:

- Status and other readers hold the shared persistence lock across reading and validating both files, so they cannot observe the normal event-ahead window of an active transition.
- `state.last_event_sequence` must identify an existing event with the same run ID and resulting state.
- An event log behind state is corruption.
- An event log ahead of state indicates an interrupted transition and must fail closed with `inconsistent_persistence` in Sprint 1.
- Automatic replay of an ahead event is deferred until recovery behavior is deliberately specified.

## 15. Error Model

Expected errors should use stable machine-oriented reason codes and actionable human messages.

Sprint 1 must distinguish at least:

```text
invalid_arguments
root_not_found
root_not_git_worktree
root_not_worktree_root
invalid_config
unsupported_config_schema
missing_required_file
invalid_agent_definition
dirty_sprint_repository
dirty_managed_repository
git_operation_in_progress
invalid_submodule
uninitialized_submodule
submodule_sha_mismatch
wrong_branch
missing_remote
run_already_active
run_already_exists
feature_not_implemented
execution_not_implemented
unsupported_state_schema
corrupt_state
corrupt_event_log
inconsistent_persistence
persistence_failed
internal_error
```

Diagnostics must:

- State what failed.
- Identify the relevant path or field when safe.
- State the expected condition.
- Suggest a corrective action when one is clear.
- Avoid secrets and full environment dumps.

## 16. Security and Data Handling

- Do not persist environment variables.
- Do not read or copy GitHub or model-provider credentials.
- Do not log or persist the opaque Sprint 1 `--server-url` value. URL parsing and credential-aware validation begin in Sprint 2.
- Create state temporary files with permissions that do not broaden access compared with the final file.
- Do not invoke a shell with user-controlled paths or configuration values.
- Reject configuration files, state files, and individual event lines larger than 1 MiB before JSON decoding.
- Test fixtures must use synthetic identities and URLs only.

## 17. Test Fixture Requirements

The test suite must provide reusable helpers that can create:

1. A bare remote for a managed repository.
2. A managed repository with an initial commit, configured branch, and `origin` remote.
3. A sprint repository with an initial commit.
4. The managed repository added as a real Git submodule.
5. Required sprint documents and agent definitions.
6. A valid `sprint_config.json`.
7. A clean committed baseline.

Fixture Git identity must be configured locally within temporary repositories. Tests must not depend on global Git user configuration, network access, GitHub, OpenCode, or the developer's home directory.

Fixtures must support controlled variants for:

- Staged, unstaged, and untracked changes.
- Wrong and detached branches.
- Missing and unknown remotes.
- Uninitialized submodules.
- Gitlink/HEAD mismatches.
- Merge, rebase, cherry-pick, revert, and bisect markers.
- Missing, empty, malformed, duplicate-key, and unknown-version JSON.
- Existing state, event, lock metadata, and active OS locks.

## 18. Required Automated Tests

### 18.1 Configuration

- Complete valid configuration loads into typed data.
- Every required field missing case fails with its field path.
- Wrong primitive and collection types fail.
- JSON booleans are rejected for integer fields.
- Unknown fields and duplicate JSON keys fail.
- Unknown schema versions fail.
- Invalid identifiers, paths, model IDs, limits, and CI values fail.
- Paths escaping the root directly or through symlinks fail.
- Missing, duplicate-location, or empty documents fail.
- Missing or invalid local agent definitions fail.

### 18.2 Repository Validation

- A valid sprint repository and managed submodule pass.
- Nonexistent root, non-Git root, child directory, and bare repository fail.
- Sprint repository staged, unstaged, and untracked changes each fail.
- Managed repository staged, unstaged, and untracked changes each fail.
- Wrong branch and detached HEAD fail.
- Missing remote fails.
- Non-gitlink path, unregistered path, uninitialized submodule, and SHA mismatch fail.
- Every supported in-progress Git operation fails.
- Failure cases leave status, HEADs, indexes, branches, and runtime paths unchanged.

### 18.3 State and Events

- State serializes and validates round-trip.
- State replacement never exposes truncated JSON under injected write failures.
- Event append preserves all prior bytes.
- Event sequences continue monotonically.
- Unknown versions, malformed or partial lines, gaps, duplicates, run-ID mismatches, and state mismatches fail closed.
- Event short writes are completed or fail explicitly without rewriting prior bytes.
- An event log ahead of state reports `inconsistent_persistence`.
- An event log behind state reports corruption.
- Transition guards reject invalid transitions and missing blocked reasons.

### 18.4 Locking

- One process acquires ownership.
- A concurrent process is rejected.
- Post-lock revalidation prevents a second process from acting on stale preflight results.
- Status remains available while ownership is held and cannot observe a transition's event/state intermediate window.
- Stale or malformed `lock.json` does not prevent acquisition when no OS lock is held.
- PID reuse is not inferred from PID alone when process-start identity is available.
- Normal and handled-error exits release the OS lock.

### 18.5 CLI and Status

- Help and version output succeed.
- All five commands parse their final V1 argument shapes.
- `pause`, `resume`, and `stop` return `feature_not_implemented` errors with verified state, event, lock, and Git non-mutation.
- No-run human and JSON status succeed without creating worktree or runtime files; creation of the local persistence lock under Git metadata is permitted.
- Invalid status roots and structurally invalid configuration fail without creating worktree or runtime files.
- Placeholder-run human and JSON status match the documented contracts.
- Status reports active ownership during execution and inactive state after release without trusting persisted intent alone.
- Invalid state and event data produce non-zero actionable errors.
- JSON output remains valid and free of diagnostic prose.
- Invalid `run` input creates no runtime files.
- Valid `run` records the expected transition and placeholder reason.
- Any existing Sprint 1 state rejects a new `run` before worktree cleanliness validation.
- The opaque server URL is neither parsed, logged, nor persisted.

## 19. Documentation Requirements

Sprint 1 implementation must update user-facing repository documentation with:

- Supported Python version.
- Installation and development setup.
- `sprint-loop --help` overview.
- Sprint 1 command behavior and placeholder limitation.
- Required sprint repository layout.
- Configuration example and field reference.
- Clean repository requirement.
- Status JSON schema or reference.
- The expected uncommitted controller runtime files after a Sprint 1 placeholder run and the absence of checkpoint commits until Sprint 4.
- Default test command.

Documentation must identify OpenCode execution, Git commits, CI, recovery controls, and the Neovim plugin as not yet implemented rather than presenting final V1 behavior as available.

## 20. Acceptance Criteria

Sprint 1 is accepted when:

1. The package installs into a clean Python 3.11+ environment.
2. The `sprint-loop` executable exposes `run`, `status`, `pause`, `resume`, and `stop`.
3. A valid clean sprint fixture passes all configuration and repository preflight checks.
4. A valid `run` records the three Sprint 1 transitions and exits in `blocked/execution_not_implemented` without invoking OpenCode or mutating Git.
5. Human and JSON status accurately describe the persisted placeholder run.
6. No-run status succeeds without creating runtime files.
7. Invalid configuration and repository states produce no runtime or Git mutation.
8. Exactly one managed repository is accepted and collection-shaped configuration/state is preserved.
9. Atomic state and append-only event failure tests pass.
10. A concurrent controller cannot acquire the same sprint repository, and post-lock revalidation prevents stale preflight writes.
11. Stale or malformed descriptive metadata alone does not prevent ownership.
12. Unknown schemas and inconsistent persistence fail closed.
13. Default tests require no credentials, network, OpenCode server, GitHub repository, or global Git identity.
14. User documentation accurately describes implemented Sprint 1 behavior and limitations.
15. The full Sprint 1 test suite and repository formatting/lint checks pass.

## 21. Exit Demonstration

The sprint review must demonstrate:

1. Installation into a clean environment.
2. Help and version output.
3. No-run human and JSON status.
4. Creation of a temporary valid sprint repository with a real managed submodule.
5. A successful foundation `run` reaching `blocked/execution_not_implemented`.
6. Persisted `state.json` and three ordered event records.
7. Human and JSON status after process exit.
8. A concurrent lock rejection using a controlled lock-holder test.
9. A dirty managed repository failure with no new runtime artifacts or Git mutation.
10. An unknown configuration schema failure with no mutation.

The demonstration must not require a live OpenCode server or GitHub credentials. The supplied `--server-url` is syntactic placeholder input in Sprint 1 only.

## 22. Handoff to Sprint 2

Sprint 1 must leave Sprint 2 with:

- A stable CLI entry point and required `--server-url` argument.
- Structurally validated V1 configuration data with feature-specific semantics explicitly deferred to their implementation sprints.
- A canonical sprint root.
- Safe state, event, and ownership primitives.
- A status projection capable of later agent fields.
- Clear extension points in the run flow after repository validation.

Sprint 2 will add the `AgentRunner` boundary, validate the OpenCode server, create fresh sessions, monitor invocation completion, and persist invocation records. Sprint 1 must not preempt those decisions with an undocumented OpenCode client abstraction.
