# Sprint Loop Controller V1 Final Software Specification

## Document Status

This document defines the final functional and technical scope for version 1 of the Sprint Loop Controller and its companion Neovim plugin. It is the implementation contract for V1. Features described as future work are not required for V1 acceptance.

## 1. Purpose

The Sprint Loop Controller automates an implementation, verification, CI, and audit workflow for OpenCode-based development. It coordinates fresh OpenCode agent sessions, Git operations, GitHub Actions, durable workflow state, and user controls.

The controller does not implement product changes itself. Product changes are made by OpenCode Builder and CI Fixer sessions. The controller owns workflow decisions and the Git operations that establish and publish authoritative checkpoints.

The system must remain observable through OpenCode's existing web interface and through a concise Neovim progress view. Closing Neovim must not stop an active loop while its OpenCode server remains available.

## 2. Goals

V1 must:

1. Run a complete Builder -> local audit -> push -> CI -> final audit loop.
2. Use a fresh OpenCode session for every Builder, Auditor, and CI Fixer invocation.
3. Require an active, healthy OpenCode server rooted at the sprint repository.
4. Keep agent sessions visible through the server's normal OpenCode clients.
5. Require a clean sprint repository and managed implementation repository before starting.
6. Let agents choose and stage implementation changes while reserving commit and push operations for the controller.
7. Batch local implementation and audit work before running hosted CI.
8. Evaluate all applicable GitHub checks for the exact pushed commit.
9. Persist enough state to inspect, pause, stop, and resume a run safely.
10. Record sprint specifications, agent invocations, audit results, CI evidence, events, and state in a sprint-history Git repository.
11. Expose a stable CLI consumed by a thin Neovim plugin.
12. Report evidence-based sprint completion using checklist item assessments.

## 3. Non-Goals

V1 does not need to provide:

- Simultaneous changes to multiple managed implementation repositories.
- Atomic multi-repository publication.
- Modification of nested dependency submodules.
- A custom web dashboard or OpenCode server multiplexer.
- Generic third-party workflow plugins.
- Support for CI systems other than GitHub Actions and GitHub checks.
- Parallel agent invocations.
- Parallel sprint loops in one sprint repository.
- Automatic recovery or disposal of partial changes left by an interrupted agent.
- Automatic sprint specification generation.
- Exact time-to-completion estimates.
- Support for operating systems other than the Linux-based mkchad environment.

The configuration and persisted commit model will use repository collections so these exclusions do not require a destructive data-model change later.

## 4. Terminology

**Sprint repository**
: The Git repository that contains sprint specifications, controller configuration, durable state, event history, invocation records, and one managed implementation repository as a Git submodule.

**Managed repository**
: The implementation repository that agents may modify and that the controller may commit and push. V1 requires exactly one.

**Controller checkpoint commit**
: A local commit in the sprint repository recording a meaningful durable workflow transition and any updated implementation submodule gitlink.

**Implementation commit**
: A commit in the managed repository created by the controller from changes staged by a Builder or CI Fixer.

**Commit set**
: A mapping from managed repository name to implementation commit SHA. It contains exactly one entry in V1.

**Invocation**
: One fresh OpenCode session running a Builder, Auditor, or CI Fixer role.

**Local audit round**
: An Auditor review of a local implementation commit before that commit is pushed.

**Final audit**
: The authoritative Auditor review performed after all checks for the pushed commit pass.

**Durable transition**
: A workflow state change important for recovery or historical interpretation. Durable transitions are persisted immediately and normally committed to the sprint repository.

## 5. System Architecture

### 5.1 Components

#### Python Controller

The Python controller is authoritative for:

- Workflow state and state transitions.
- Configuration validation.
- Repository validation and locking.
- OpenCode server validation.
- Creation and monitoring of OpenCode sessions.
- Validation of agent results.
- Git commits and pushes.
- GitHub CI discovery and monitoring.
- Durable state, event, transcript, audit, and CI records.
- Pause, resume, stop, retry-limit, and interruption behavior.
- Machine-readable and human-readable status output.

#### OpenCode Server

The OpenCode server is the agent execution and visibility layer. It is not the workflow engine. It must:

- Already be running before the loop starts or resumes.
- Be supplied to the controller as a URL.
- Be rooted at the sprint repository.
- Create and retain fresh sessions for controller invocations.
- Expose session status, messages, structured results, and events through its HTTP API.
- Permit sessions to be viewed through ordinary OpenCode clients, including the web interface.

#### Neovim Plugin

The Lua plugin is a thin client. It:

- Resolves the sprint root and OpenCode server URL.
- Starts the controller as a detached Neovim job.
- Invokes controller status and control commands asynchronously.
- Renders progress and errors.
- Opens the active OpenCode session when a suitable web URL is available.

It must not implement workflow transitions, Git behavior, CI logic, or authoritative state.

#### mkchad

mkchad remains responsible for the containerized development environment and OpenCode server lifecycle. It provides workspace and server discovery to the Neovim plugin. The controller must not silently start a replacement OpenCode server.

#### GitHub

GitHub hosts the managed repository and supplies check runs and GitHub Actions results for pushed implementation commits.

### 5.2 Source Repository Separation

The controller source and plugin source are separate Git repositories. The plugin source repository may be included as a submodule of the controller source repository for coordinated development.

Neither source repository is the sprint-history repository used during a product sprint.

## 6. Sprint Repository Layout

The expected logical layout is:

```text
sprint-repo/
|-- AGENTS.md
|-- sprint_config.json
|-- .opencode/
|   `-- agents/
|       |-- builder.md
|       |-- auditor.md
|       `-- ci-fixer.md
|-- docs/
|   `-- <multisprint_short_name>/
|       |-- multisprint_spec.md
|       `-- <sprint_idx>/
|           |-- sprint_spec.md
|           `-- sprint_checklist.md
|-- repositories/
|   `-- <repository_name>/
|-- invocations/
|   `-- <multisprint_short_name>/<sprint_idx>/
|       `-- <sequence>-<role>/
|           |-- metadata.json
|           |-- prompt.md
|           |-- result.json
|           `-- transcript.json
`-- info/
    `-- <multisprint_short_name>/<sprint_idx>/
        |-- state.json
        |-- events.jsonl
        |-- lock.json
        |-- audits/
        |-- ci/
        `-- commit-messages/
```

The controller may create missing runtime directories under `invocations/` and `info/`. It must not invent missing specifications or silently select an ambiguous sprint.

## 7. Configuration

### 7.1 Location

The controller reads `sprint_config.json` from the sprint repository root. Relative paths are resolved from that root.

### 7.2 V1 Configuration Shape

The following illustrates the required concepts. The implementation may add schema-versioning or minor operational fields, but must not change the semantics without updating this specification.

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
    "builder": "<provider>/<medium-model>",
    "auditor": "<provider>/<strong-model>",
    "ci_fixer": "<provider>/<medium-model>"
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

### 7.3 Configuration Rules

- V1 requires exactly one `repositories` entry.
- The managed repository must be a Git submodule of the sprint repository.
- The configured branch must be checked out before start.
- Every configured document must exist and be a regular file.
- Agent and model identifiers must be explicit.
- All limits must be positive integers.
- `limits.invocation_timeout_seconds` bounds model execution and controller observation time but excludes intervals durably recorded as waiting for user input. User-question waits have no V1 timeout while the configured OpenCode server remains healthy.
- Unknown schema versions must fail validation.
- Invalid configuration must fail before any agent or Git mutation occurs.

## 8. Command-Line Interface

The executable name is `sprint-loop`.

### 8.1 Run

```bash
sprint-loop run --root <sprint-repository> --server-url <url>
```

`run` validates inputs, acquires ownership, starts a new configured sprint run, and remains in the foreground. A launcher such as the Neovim plugin may detach it. It must reject an existing non-terminal run and direct the user to `resume` instead.

It must not silently choose an OpenCode server or start one.

### 8.2 Status

```bash
sprint-loop status --root <sprint-repository>
sprint-loop status --root <sprint-repository> --json
```

Status must work while the controller is running and after it exits. JSON output is a stable integration interface for the Neovim plugin.

### 8.3 Pause

```bash
sprint-loop pause --root <sprint-repository>
```

Pause requests a safe stop at the next transition boundary. It must not interrupt an active Git commit or leave a partially written state file. If an OpenCode invocation is active, V1 allows it to finish, validates and persists its result, and completes any required implementation commit before pausing. If that invocation is waiting for user input, the pause request remains pending until the user answers or rejects the question and the invocation reaches the same normal handoff boundary. It does not launch the next invocation or publish new work. After checkpointing `paused`, the controller exits so `resume` can acquire ownership. The resulting state must be resumable.

### 8.4 Resume

```bash
sprint-loop resume --root <sprint-repository> --server-url <url>
```

Resume requires a newly supplied and validated server URL, even if it is unchanged. It verifies repository and persisted-state consistency before continuing.

### 8.5 Stop

```bash
sprint-loop stop --root <sprint-repository>
```

Stop requests a controlled terminal state. It waits for an indivisible Git operation or an ordinarily executing invocation to reach a safe durable boundary. If an invocation is waiting for user input, that interaction is a V1 stop boundary: the controller rejects the pending question, requests one bounded abort of the active session, records the interrupted invocation truthfully, preserves all partial repository work, and then checkpoints `stopped`. It must preserve all completed work and records. Stop does not reset, discard, stash, stage, commit, or rewrite implementation work.

### 8.6 Exit Behavior

CLI failures must return a non-zero status and a concise actionable message. Machine-readable commands must not mix diagnostic prose into JSON standard output.

## 9. Startup Validation

Before entering the loop, the controller must validate all of the following.

### 9.1 Sprint Repository

- The root exists and is a Git worktree root.
- `sprint_config.json` is present and valid.
- Required documents and agent definitions exist.
- The sprint repository has no staged, unstaged, or untracked files.
- No tracked sprint-repository path uses an index flag that hides worktree changes from status.
- No merge, rebase, cherry-pick, or revert is in progress.
- The configured submodule is initialized.
- The managed repository gitlink matches its checked-out HEAD.
- No active controller lock exists for the sprint.

### 9.2 Managed Repository

- The path is a Git repository and the expected submodule.
- The configured branch is checked out.
- The repository has no staged, unstaged, or untracked files.
- No tracked managed-repository path uses an index flag that hides worktree changes from status.
- No merge, rebase, cherry-pick, or revert is in progress.
- The configured remote exists.

Staging temporary user changes does not satisfy the clean-state requirement. The user must commit, stash including untracked files, or discard them before starting.

### 9.3 OpenCode Server

- The URL is an absolute HTTP or HTTPS URL.
- `GET /global/health` succeeds within a bounded timeout.
- The response reports `healthy: true` and a supported version.
- The server path/workspace resolves to the sprint repository root.
- The configured agents are available.
- Configured models/providers are available when that information can be validated before invocation.

Credentials must not be required in command-line arguments. They may be supplied through protected configuration or inherited environment variables such as OpenCode's server authentication variables.

Validation failures must identify the failed condition, expected value, and observed value when safe.

## 10. Repository Ownership and Git Contract

### 10.1 Agent Responsibilities

On successful completion, a Builder or CI Fixer must:

1. Make only task-related implementation changes.
2. Run appropriate local verification.
3. Stage every intended addition, modification, and deletion in the managed repository.
4. Leave no task-related change unstaged.
5. Write a proposed commit message to the controller-provided path under the sprint repository's invocation information.
6. Return a structured result.

Agents must not commit, push, rewrite history, change branches, or modify controller state.

### 10.2 Controller Responsibilities

The controller must:

1. Validate the structured result.
2. Inspect repository status and the staged diff.
3. Reject an empty staged change when the role reports implementation work completed.
4. Reject unexpected unstaged or untracked implementation changes.
5. Validate that the commit message exists and is non-empty.
6. Run `git commit -F <commit-message-path>` in the managed repository.
7. Capture the resulting implementation commit SHA.
8. Update the persisted commit set.
9. Update the sprint repository's submodule gitlink in the next checkpoint commit.
10. Push only at a workflow publication boundary.

The controller must never run a blanket `git add` to compensate for an incomplete agent handoff.

### 10.3 Commit and Push Cadence

Implementation commits are created locally after each successful Builder or CI Fixer invocation. They are not automatically pushed one at a time.

Builder commits are eligible for push after the configured local audit gate is clean. CI Fixer commits are eligible for immediate push after local verification because hosted CI is the evidence being repaired. One push may publish multiple local implementation commits, and CI is evaluated for the pushed tip.

Sprint repository checkpoint commits are created locally at meaningful durable transitions. They are pushed at implementation publication boundaries and at successful sprint completion, not after every checkpoint.

The controller must not create commits merely because a CI polling interval elapsed.

## 11. OpenCode Session Model

### 11.1 Fresh Sessions

Each Builder, Auditor, and CI Fixer invocation receives a newly created OpenCode session on the supplied server. Sessions are not continued across workflow invocations.

Session titles must use the recognizable format `[<multisprint>/<sprint>] <role> <sequence> <purpose>`. The sequence is zero-padded to at least four decimal digits, and the purpose is a concise phase-specific description. For example:

```text
[authentication/3] builder 0004 implementation
[authentication/3] auditor 0005 pre-CI audit round 2
[authentication/3] ci-fixer 0006 CI fix attempt 1
```

### 11.2 Invocation Inputs

Every invocation receives the relevant subset of:

- Role-specific instructions.
- Root sprint `AGENTS.md` instructions.
- Managed repository `AGENTS.md` instructions.
- Multisprint specification.
- Current sprint specification.
- Current sprint checklist.
- Current commit set.
- Current repository status expectations.
- Previous audit findings, if applicable.
- CI failure evidence, if applicable.
- Commit-message output path for mutating roles.
- Structured-result schema.

The controller must explicitly supply or reference required instructions. It must not rely solely on implicit parent-directory instruction discovery.

### 11.3 Invocation Monitoring

The controller may use OpenCode server events, session status polling, or both. It must enforce invocation timeout and cancellation behavior and persist the server session ID as soon as it is created. Production Builder, Auditor, and CI Fixer monitoring must also detect pending OpenCode questions for the exact active session. The Sprint 2 execution probe remains non-interactive and denies the question tool.

### 11.4 User Questions

Builder, Auditor, and CI Fixer sessions may ask the user a question only when a decision is necessary and cannot be inferred safely from the supplied specifications, findings, CI evidence, or repository instructions. They must not ask for routine confirmation, optional improvements, or preferences that do not block the assigned work.

When an allowed role asks a question:

1. The controller verifies through documented OpenCode events or pending-question APIs that the request belongs to the exact active session.
2. It records the active invocation as `waiting_for_user` without changing the surrounding workflow phase.
3. It persists bounded request identity, question count, and observation timestamp through `agent.question_asked`; full question text is not copied into state, events, status, or notifications.
4. It waits indefinitely while the supplied OpenCode server remains healthy. This interval does not consume `limits.invocation_timeout_seconds`.
5. The user answers or rejects the request through an ordinary OpenCode client. The controller never selects or submits an answer.
6. The controller records `agent.question_resolved`, clears the pending interaction, resumes model timeout accounting, and continues the same invocation and session.

Question and answer content may appear only in OpenCode's session records and the controller's bounded sanitized terminal transcript. A malformed request, a request associated with another session, contradictory question events, or ambiguous resolution must fail closed without being treated as user approval. The controller must ignore questions belonging to unrelated OpenCode sessions.

The indefinite logical wait must be implemented through bounded documented server operations or event observation, not one unbounded HTTP read. Server liveness checks and controlled cancellation remain active while waiting.

### 11.5 Invocation Records

Each invocation directory must record:

- `metadata.json`: identity, role, model, session ID, timestamps, input commits, and terminal result.
- `prompt.md`: the effective task prompt or a deterministic representation of its inputs.
- `result.json`: validated structured output.
- `transcript.json`: exported or reconstructed OpenCode messages and parts.

Records must be written atomically where practical. A failed or interrupted invocation still receives metadata describing its terminal condition.

## 12. Agent Roles

### 12.1 Builder

The Builder:

- Implements sprint specification and checklist work.
- Addresses final-audit or local-audit findings.
- Makes the smallest coherent implementation change that advances the sprint.
- Runs appropriate local checks.
- Stages intended changes and writes the commit message.
- Reports blocked conditions rather than inventing requirements.
- Uses the question lifecycle only for a necessary user decision that prevents safe implementation.

### 12.2 CI Fixer

The CI Fixer:

- Receives failing check names, conclusions, relevant logs, and the exact failed commit.
- Diagnoses the CI failure.
- Applies the smallest change needed to restore green CI.
- Avoids unrelated refactoring or sprint expansion.
- Runs relevant local checks when possible.
- Stages intended changes and writes the commit message.
- Uses the question lifecycle only when the available CI evidence requires a user decision.

### 12.3 Auditor

The Auditor:

- Uses the configured strong model.
- Reviews a clean, committed implementation SHA.
- Compares implementation and tests against the multisprint specification, sprint specification, and sprint checklist.
- Emits only actionable findings that prevent sprint completion.
- Excludes optional improvements, style preferences, and speculative refactoring.
- Assesses each checklist item with evidence.
- Makes no repository changes.
- Returns an empty findings list when the sprint may advance.
- Uses the question lifecycle only when a user decision is necessary to interpret an otherwise ambiguous requirement.

## 13. Structured Results

### 13.1 Mutating Agent Result

Builder and CI Fixer results must conform to a schema equivalent to:

```json
{
  "status": "completed",
  "summary": "Implemented bounded request retry handling.",
  "checks": [
    {
      "command": "pytest tests/unit",
      "result": "passed",
      "details": null
    }
  ],
  "commit_message_path": "info/authentication/3/commit-messages/0004-builder.txt",
  "blocking_reason": null
}
```

Allowed statuses are:

- `completed`
- `blocked`
- `failed`

A `blocked` result requires a non-empty `blocking_reason`. A `completed` result requires the expected staging and commit-message handoff.

### 13.2 Audit Result

Audit results must contain findings and checklist assessments:

```json
{
  "status": "completed",
  "summary": "One sprint requirement remains incomplete.",
  "findings": [
    {
      "id": "AUD-001",
      "severity": "high",
      "requirement": "Failed requests must be attempted up to three times.",
      "location": "src/client.py:84",
      "problem": "The retry loop permits only two total attempts.",
      "expected": "Permit the configured total of three attempts."
    }
  ],
  "checklist": [
    {
      "id": "AUTH-07",
      "status": "partial",
      "confidence": "high",
      "evidence": [
        "src/auth/token.py:44 validates access-token expiry.",
        "No test covers an expired refresh token."
      ],
      "remaining": "Add and satisfy expired refresh-token coverage."
    }
  ],
  "remaining_effort": "small"
}
```

Finding severities in V1 are:

- `blocking`: progress is impossible or unsafe without resolution.
- `high`: a material sprint requirement or correctness condition is unmet.
- `normal`: a concrete sprint-completion issue that still requires correction.

Checklist statuses are:

- `satisfied`
- `partial`
- `unsatisfied`
- `not_evaluated`

Confidence values are `high`, `medium`, or `low`. Remaining effort values are `small`, `medium`, `large`, or `unknown` and are advisory only.

Finding IDs identify findings within an audit report. The next Auditor conducts a fresh review rather than mutating previous findings. The controller retains prior reports for history and may compare them for no-progress detection.

## 14. Workflow State Machine

### 14.1 Primary Flow

```text
Initialize
  -> Validate Workspace and Server
  -> Implement
  -> Local Verification Handoff
  -> Commit Implementation
  -> Pre-CI Audit
       -> Clean: Publish
       -> Findings and rounds remain: Implement Findings
       -> Findings and limit reached: Blocked
  -> Push
  -> Wait for CI
       -> Failed: Fix CI -> Commit Fix -> Push -> Wait for CI
       -> Passed: Final Audit
  -> Final Audit
       -> Findings: Implement Findings
       -> Clean: Finished
```

Any Builder, Auditor, or CI Fixer invocation in this flow may temporarily enter the active-invocation substate `waiting_for_user`. Resolving the question resumes the same workflow state, invocation, and session; it does not consume an implementation cycle, audit round, or CI fix attempt by itself.

### 14.2 Durable States

V1 must represent at least:

- `initializing`
- `validating`
- `implementing`
- `committing`
- `pre_ci_auditing`
- `pushing`
- `waiting_for_ci`
- `fixing_ci`
- `final_auditing`
- `paused`
- `blocked`
- `stopping`
- `stopped`
- `failed`
- `finished`

Substates or reason fields may provide additional precision without multiplying top-level states.

### 14.3 Pre-CI Audit Policy

`pre_ci_audit.max_rounds` is a maximum audit budget, not a requirement to run every round.

- If the first local audit is clean, publish immediately.
- If findings are emitted and rounds remain, launch a Builder with those findings, commit its staged changes, and run another local audit.
- If findings remain after the configured limit, enter `blocked` rather than knowingly publishing defects.
- Every audit examines a clean, committed implementation SHA.

### 14.4 CI Fix Policy

A failed CI result launches a CI Fixer. After a successful Fixer handoff, the controller creates an implementation commit and pushes it without requiring the full pre-CI audit sequence. CI is then evaluated again for the new exact SHA.

The final audit after green CI covers CI Fixer changes.

### 14.5 Final Audit Policy

After all CI passes, the controller launches a final Auditor.

- No findings transitions to `finished`.
- Findings begin a new local Builder and pre-CI audit batch.
- A new implementation commit requires a new push and complete CI evaluation before another final audit.

### 14.6 Loop Limits

The controller must enforce configured limits. Exceeding a quality or retry limit enters `blocked` with a precise reason. It must not reinterpret a limit as permission to push known defects or declare success.

No-progress detection should at minimum identify repeated equivalent audit findings or repeated CI failures across the configured attempt budget.

## 15. GitHub CI Semantics

### 15.1 Commit Association

Only checks associated with the exact pushed implementation commit SHA count toward the workflow decision. Results for an earlier branch commit must be ignored.

### 15.2 Completion

The controller waits until every applicable discovered check is terminal. CI passes only when all applicable checks have acceptable conclusions.

The following conclusions fail CI:

- `failure`
- `timed_out`
- `cancelled`
- `action_required`
- `startup_failure`
- `stale`

Treatment of `skipped` and `neutral` is configuration-controlled. A non-terminal or unknown conclusion cannot be treated as success.

If no checks are discovered, V1 follows the configured `zero_checks` policy. The default and recommended behavior is to enter a clear error or blocked condition rather than immediately passing.

### 15.3 CI Evidence

On failure, the controller records:

- Commit SHA.
- Check suite and check run identifiers.
- Names, statuses, conclusions, and URLs.
- Workflow and job identifiers when available.
- Relevant failing logs or annotations.
- Collection timestamps.

The CI Fixer should receive focused failure evidence rather than an unbounded dump of every successful job.

## 16. Persistent State and Event History

### 16.1 State File

`state.json` is the current authoritative snapshot. It must include at least:

- Schema version.
- Run ID.
- Multisprint and sprint identity.
- Current state and reason.
- Controller process identity while active.
- OpenCode server URL and validated version.
- Active invocation, session ID, execution status, and bounded pending-question metadata, if any.
- Local and last-pushed commit sets.
- Current audit phase and round counts.
- CI attempt count and active check identifiers.
- Implementation cycle and CI fix counts.
- Latest checklist assessment.
- Pause or stop request state.
- Creation and update timestamps.
- Terminal result when applicable.

State updates must be atomic, for example by writing a sibling temporary file, flushing it, and replacing the prior file.

### 16.2 Event Log

`events.jsonl` is append-only. Every event must contain:

- Event schema version.
- Monotonic sequence number.
- Timestamp.
- Run ID.
- Event type.
- Current workflow state.
- Structured event payload.

Representative events include:

```text
run.started
state.entered
server.validated
agent.started
agent.question_asked
agent.question_resolved
agent.completed
agent.interrupted
git.committed
git.pushed
ci.discovered
ci.completed
audit.completed
run.paused
run.blocked
run.stopped
run.finished
```

Events are written immediately but need not each create a Git commit. Poll observations with no meaningful external change should not be recorded repeatedly.

Question events contain bounded request/session identity, question count, timestamps, and resolution category only. They must not contain question text, answer text, option labels, or other transcript content.

### 16.3 Checkpoint Commits

After a meaningful durable transition, the controller stages only its intended sprint-history artifacts and creates a local sprint repository commit. It must not use blanket staging.

Example commit subjects:

```text
loop(authentication-3): start builder invocation 0004
loop(authentication-3): enter CI wait for backend@abc1234
loop(authentication-3): finish sprint
```

Checkpoint commit failure is a workflow error because state history and repository history would otherwise diverge.

## 17. Locking and Concurrency

V1 allows one active loop per sprint repository.

The controller must use an operating-system lock in addition to descriptive `lock.json` metadata. A PID file alone is insufficient because of stale files and PID reuse.

The lock record should include:

- Run ID.
- PID.
- Process start identity when available.
- Host/container identity.
- Start timestamp.

Status may read state without acquiring exclusive ownership. Mutating control commands must coordinate with the active process and must not start a second state machine.

## 18. Pause, Stop, Failure, and Recovery

### 18.1 Pause

Pause is resumable. The controller records the prior state, reaches a safe boundary, checkpoints `paused`, and exits. A pause requested while an invocation is `waiting_for_user` remains pending until that question is answered or rejected and the invocation completes its normal handoff. Resume revalidates all external assumptions before acquiring a new active process lifetime.

### 18.2 Stop

Stop is user-requested termination. It preserves commits, transcripts, state, events, and partial implementation work. At a `waiting_for_user` boundary, stop rejects the pending question, requests one bounded session abort, records the interruption, and checkpoints `stopped` without altering the managed worktree. Resuming a stopped run is not required in V1; a deliberate override may be added later.

### 18.3 Server Loss

The controller periodically validates server liveness, including while waiting for CI. If the server remains unavailable beyond the configured grace period, it must:

1. Record `blocked` with reason `server_unavailable`.
2. Record the active invocation as interrupted when applicable.
3. Persist and checkpoint all known state.
4. Exit without launching a replacement server.

Resume requires a supplied server URL and complete server validation.

If an agent was active when the server was lost:

- If the managed repository is clean, the invocation may be rerun in a fresh session.
- If any staged, unstaged, or untracked implementation change remains, resume enters or preserves `blocked` with reason `interrupted_dirty_worktree`.
- V1 must not discard, stash, stage, or commit those partial changes automatically.

If the agent was waiting for user input, the controller must not assume that the in-memory OpenCode question survived server loss. It records the invocation as interrupted and follows the same clean- or dirty-worktree recovery rules.

### 18.4 External Repository Changes

If repository HEAD, branch, index, worktree, remote tracking state, or submodule gitlink changes unexpectedly, the controller must stop mutation and enter a blocked state. It must not reset or overwrite concurrent user work.

### 18.5 Internal Failure

Unexpected controller errors produce `failed` only after best-effort atomic state and event persistence. Error records must exclude secrets.

## 19. Progress and Completion Reporting

Status must report at least:

- Sprint identity.
- Current state and reason.
- Active role, invocation ID, and OpenCode session ID.
- Active invocation status (`running` or `waiting_for_user`) and bounded pending-question identity, count, and timestamp.
- Local implementation commit and last pushed commit.
- Pre-CI audit round and configured maximum.
- CI status and attempt count.
- Implementation-cycle and CI-fix counts.
- Latest checklist counts by status.
- Latest audit remaining-effort band.
- Last meaningful event and timestamp.
- Whether the process is running, paused, blocked, or terminal.

When an invocation is waiting, the additive status projection is equivalent to:

```json
{
  "active": {
    "role": "builder",
    "invocation_id": "0004-builder",
    "session_id": "ses_example",
    "status": "waiting_for_user",
    "interaction": {
      "request_id": "que_example",
      "question_count": 1,
      "asked_at": "2026-07-15T12:00:00Z"
    }
  }
}
```

`active.status` is `running` or `waiting_for_user` while an invocation exists. `active.interaction` is null while running and contains exactly the bounded fields above while waiting. For a persisted run with no active invocation, the active object retains null role, invocation, session, status, and interaction fields. The existing no-run projection continues to use `active: null`.

The controller computes completion from the latest Auditor checklist assessment. It should favor counts such as `14 satisfied, 2 partial, 1 unsatisfied` over an unsupported exact percentage.

If a percentage is exposed, it must be derived deterministically from documented weights and labeled as an estimate from the latest audit. The Auditor does not supply an arbitrary completion percentage or exact completion date.

## 20. Neovim Plugin Specification

### 20.1 Repository

The plugin is developed in the separate `opencode_sprint_loop.lua` repository.

### 20.2 Setup and Lua API

The plugin targets Neovim 0.12. `setup()` must be called before any plugin command or public action. It requires explicit sprint-root and server-URL values or callbacks, accepts an executable value or callback defaulting to `sprint-loop`, and accepts optional OpenCode web-URL and server-CA-certificate values or callbacks. URL callbacks may return synchronously or resolve through one completion callback so the generic plugin can consume mkchad without hard-coding it. A representative API is:

```lua
local function mkchad_url(done)
  local server = vim.g.opencode_opts and vim.g.opencode_opts.server
  if not server or type(server.url) ~= "function" then
    done(nil, "mkchad OpenCode URL resolver is unavailable")
    return
  end
  server.url(done)
end

require("opencode_sprint_loop").setup({
  executable = "sprint-loop",
  sprint_root = function()
    return vim.fn.getcwd()
  end,
  server_url = mkchad_url,
  web_url = mkchad_url,
  server_ca_cert = function()
    return vim.g.opencode_opts.server.ca_cert()
  end,
})
```

The exact mkchad adapter names are integration details, not assumptions the generic plugin may hard-code. This example reads existing server information only; it does not call mkchad's server ensure/start operation.

Resolving configuration must not start or replace an OpenCode server. When a server CA certificate is configured, the plugin supplies it only to controller child processes through an inherited environment override such as `SSL_CERT_FILE`; it does not place the path in argv or configure browser trust.

The module also exposes asynchronous `start()`, `progress()`, `pause()`, `resume()`, `stop()`, and `open_session()` methods. They back the corresponding commands, resolve relevant callbacks when invoked, report through the same UI, and do not expose a stable process-handle return contract.

### 20.3 Commands

The plugin exposes:

- `:SprintLoopStart`
- `:SprintLoopProgress`
- `:SprintLoopPause`
- `:SprintLoopResume`
- `:SprintLoopStop`
- `:SprintLoopOpenSession`

Commands must execute external processes asynchronously so Neovim remains responsive.

### 20.4 Detached Lifetime

Start launches `sprint-loop run` as a detached Neovim job. Neovim exit must not send a termination signal to the controller. The controller remains bounded by its own workflow, user controls, and OpenCode server liveness.

### 20.5 Progress UI

Progress calls `sprint-loop status --json` and renders a disposable read-only buffer in a centered floating window. It must display blocked, failure, and `waiting_for_user` conditions prominently and include the active OpenCode session identifier and pending-question count when present.

The plugin maintains an ephemeral asynchronous status watcher at a bounded non-busy interval while a controller process is active. Setup performs an initial status query so a reopened Neovim instance can discover an existing run; plugin start and resume actions also activate observation. For each new pending question request, it emits one deduplicated notification directing the user to `SprintLoopOpenSession`. The watcher does not fetch question text, submit answers, reject requests, persist authoritative state, or continue after no controller process is active.

### 20.6 Session Opening

When a web URL can be resolved, `SprintLoopOpenSession` reads current status, requires an active session ID, and opens the active session in the user's configured browser. It constructs the supported route as `<web-base>/<base64url(canonical-sprint-root)>/session/<encoded-session-id>`. Absence of a web URL or active session must produce an actionable message and must not affect the loop.

## 21. Security and Data Handling

- The OpenCode server must use authentication when exposed beyond a trusted local interface.
- Server passwords and tokens must not be persisted in state, events, prompts, transcripts, process arguments, or Git commits.
- GitHub authentication should use existing `gh` or environment-based credentials without copying tokens into controller files.
- Transcript capture must use sanitized export support when available.
- Pending question and answer text must not be copied into state, events, status, Neovim notifications, or process arguments; terminal transcript handling applies the normal sanitization and size bounds.
- The controller should redact common credential patterns from logs and stored error payloads.
- Invocation and CI output must have configurable or safe hard-coded size bounds in V1.
- Sprint repositories containing transcripts should be treated as sensitive and private by default.
- Agent-provided paths must be validated to remain within expected sprint runtime directories.

## 22. Observability

The controller must provide:

- Human-readable console logs.
- Stable JSON status.
- Append-only structured events.
- Persisted invocation metadata and results.
- Persisted CI evidence.
- Actionable terminal and blocked reasons.

Logs should include run, invocation, and commit identifiers where relevant. They must not require the transcript to understand workflow progression.

## 23. Compatibility and Dependencies

V1 targets the Linux-based mkchad container environment and a supported Python 3 release selected by the implementation plan.

The controller may use OpenCode's documented HTTP/OpenAPI interface directly. It must not depend on undocumented OpenCode database internals.

Git and the GitHub CLI may be used for repository and CI operations. External command output must be parsed from stable machine-readable formats where available rather than human-oriented text.

The plugin targets Neovim 0.12 and uses Lua APIs available in that release.

## 24. V1 Acceptance Criteria

V1 is accepted when all of the following are demonstrated:

1. A clean example sprint repository starts from Neovim using a supplied active OpenCode server URL.
2. An invalid, unhealthy, or wrong-workspace server causes a clear startup failure and no mutation.
3. A dirty sprint or managed repository causes a clear startup failure and no mutation.
4. The Builder runs in a fresh visible OpenCode session, stages changes, and provides a commit message.
5. The controller, not the Builder, creates the implementation commit.
6. At least one local audit/fix cycle can occur without a push or hosted CI run.
7. A clean local audit causes one publication of the current tip.
8. CI is evaluated only for the exact pushed SHA and waits for all applicable checks.
9. A failed check launches a fresh CI Fixer with relevant evidence, commits its staged fix, republishes, and waits for CI again.
10. Green CI launches a fresh final Auditor.
11. Final findings begin another local implementation batch; no findings finish the sprint.
12. `SprintLoopProgress` displays current phase, session, commits, CI state, and checklist assessment without blocking Neovim.
13. A necessary agent question produces durable `waiting_for_user` status and one Neovim notification, can be answered in the exact named OpenCode session, excludes user-wait time from the model timeout, and resumes the same invocation.
14. Closing Neovim does not terminate an active controller, including one waiting for user input.
15. Losing the OpenCode server produces a resumable blocked checkpoint and process exit.
16. Resume with a new valid server URL continues safely when the worktree is clean.
17. Interrupted dirty work is preserved and reported rather than automatically altered.
18. Pause waits for a pending question to resolve, while stop rejects and aborts at that interaction boundary without altering partial work.
19. Sprint state, events, invocation records, audit reports, CI evidence, and checkpoint commits provide a coherent history.
20. Configured loop limits block safely instead of publishing known audit failures or looping indefinitely.
21. No test fixture or recorded artifact contains real credentials.
22. Automated tests cover state transitions, persistence recovery, Git handoff validation, user-question interaction, CI conclusion aggregation, and CLI JSON contracts.

## 25. Future Extensions

Potential later work includes:

- Multiple managed repositories and coordinated commit sets.
- Explicit management of selected nested submodules.
- A stable OpenCode server registry and multiplexer.
- A mobile-friendly sprint dashboard.
- Event-driven workflow extensions for PR review, benchmarking, release automation, and deployment.
- Additional CI providers.
- Parallel independent workstreams.
- Automated interrupted-work recovery.
- Weighted checklist progress configured by sprint authors.

These extensions must consume the controller's public state, event, and runner interfaces rather than bypassing workflow ownership.
