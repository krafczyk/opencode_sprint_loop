# Sprint Loop Controller V1 Multi-Sprint Plan

## 1. Plan Purpose

This plan divides the V1 software specification into implementation sprints that produce testable, reviewable increments. Each sprint has a narrow responsibility and an explicit exit gate. Later sprints may depend on earlier public interfaces, but no sprint should require unfinished work from a future sprint to demonstrate its core result.

The plan covers two source repositories:

- `opencode_sprint_loop`: Python controller, specifications, and integration tests.
- `opencode_sprint_loop.lua`: Neovim plugin, included in the controller repository as a development submodule.

The sprint-history repository format produced by the tool is a runtime concept. It is not this controller source repository.

## 2. Delivery Principles

### 2.1 Scope Discipline

Every sprint must implement only the V1 behavior required for its exit gate. Multi-repository execution, a custom web dashboard, an OpenCode multiplexer, generic workflow plugins, and non-GitHub CI remain outside this plan.

### 2.2 Vertical Increments

Each sprint should end with a runnable demonstration rather than a collection of disconnected abstractions. Mocked external systems are acceptable until their dedicated integration sprint.

### 2.3 Stable Boundaries

The following boundaries should stabilize early:

- CLI command names and JSON status contract.
- Configuration and persisted-state schema versioning.
- `AgentRunner` interface.
- Git service interface.
- CI service interface.
- Event record envelope.
- Lua plugin-to-CLI contract.

Internal implementations may evolve while these boundaries remain compatible within V1 development.

### 2.4 Safe Mutation

No implementation increment may introduce a code path that resets, discards, force-pushes, or silently stages user work. Incomplete safety behavior should fail closed until its intended sprint implements it.

### 2.5 Testability

External interactions must be behind small interfaces so state-machine tests do not require a real model, OpenCode server, GitHub repository, or network connection.

## 3. Proposed Sprint Document Layout

As implementation begins, detailed sprint specifications and checklists can be added using the intended product convention:

```text
docs/
|-- v1_final_software_specification.md
|-- multi_sprint_plan.md
`-- controller-v1/
    |-- multisprint_spec.md
    |-- 1/
    |   |-- sprint_spec.md
    |   `-- sprint_checklist.md
    |-- 2/
    |   |-- sprint_spec.md
    |   `-- sprint_checklist.md
    `-- ...
```

The two documents in the repository root `docs/` directory remain authoritative if a later sprint document conflicts with them unless the V1 specification is deliberately revised.

## 4. Technology Direction

### 4.1 Controller

The controller will be a Python package with a console entry point named `sprint-loop`. The implementation should prefer explicit typed data models and small adapters over a framework-heavy service architecture.

Expected technical needs include:

- CLI argument parsing.
- JSON schema or typed model validation.
- Atomic filesystem writes.
- Process-safe locking.
- HTTP requests and server-sent event consumption or bounded polling.
- Async subprocess execution.
- Git and GitHub CLI adapters using machine-readable output.
- Deterministic state-machine tests.

The exact dependency choices should be recorded in Sprint 1. Dependencies must earn their inclusion and be pinned through the project's selected packaging approach.

### 4.2 Neovim Plugin

The plugin will be implemented in Lua using supported Neovim APIs. It will invoke the controller asynchronously and will not duplicate Python models or workflow logic beyond rendering the documented status JSON fields.

### 4.3 External Systems

Integration targets are:

- OpenCode's documented HTTP/OpenAPI server interface.
- Git command-line behavior.
- GitHub CLI or GitHub API check-run data.
- A Linux mkchad container.

## 5. Cross-Cutting Definition of Done

Every sprint is complete only when:

1. Its required behavior is implemented.
2. Automated tests for new success and failure paths pass.
3. Machine-readable interfaces are documented when changed.
4. Error messages identify actionable causes.
5. No real token, credential, private transcript, or generated runtime state is committed.
6. Existing tests remain green.
7. The sprint exit demonstration can be repeated from documented commands.
8. Deferred work is explicitly assigned to a later sprint or removed from V1.

## 6. Sprint Sequence

The planned sequence contains eight implementation sprints.

```text
Sprint 1: Controller Foundation
    |
Sprint 2: OpenCode Execution
    |\
    | Sprint 3: Neovim Client
    |/
Sprint 4: Builder and Git Handoff
    |
Sprint 5: Audit Gate and Progress
    |
Sprint 6: Publication, CI, and CI Fixer
    |
Sprint 7: Complete Loop and Recovery
    |
Sprint 8: Hardening and V1 Release
```

Sprint 3 can begin after Sprint 1's CLI and status JSON are stable. It can proceed in parallel with part of Sprint 2, but the default plan keeps delivery sequential to reduce coordination overhead.

## 7. Sprint 1: Controller Foundation

### 7.1 Goal

Create an installable controller with validated configuration, durable state, event logging, repository ownership, and a stable CLI/status contract. No agent or CI integration is required yet.

### 7.2 Deliverables

- Python project and package structure.
- `sprint-loop` console entry point.
- Commands:
  - `run`
  - `status`
  - `pause`
  - `resume`
  - `stop`
- Versioned `sprint_config.json` model.
- Versioned `state.json` model.
- Versioned JSONL event envelope.
- Sprint repository path discovery and validation.
- Managed repository configuration represented as a collection but restricted to exactly one entry.
- Atomic state writes.
- Append-only event writes with monotonic sequence numbers.
- Exclusive process lock and descriptive lock metadata.
- Human-readable and JSON status output.
- Initial state transition framework with guarded transitions.
- Test fixtures that create temporary sprint and managed Git repositories.

### 7.3 Required Behavior

`run` must validate configuration, specifications, repository shape, submodule initialization, branch, and cleanliness. With valid inputs it may enter a placeholder blocked state indicating that execution is not implemented. With invalid inputs it must make no repository mutation.

`status --json` must emit a documented stable envelope even when no run exists, a run is inactive, or state is blocked.

The lock must prevent two `run` processes from owning the same sprint root. Stale descriptive metadata must not permanently block a new process when no operating-system lock is held.

### 7.4 Testing

- Configuration success and field validation failures.
- Unknown schema version rejection.
- Missing and ambiguous file failures.
- Dirty sprint repository rejection.
- Dirty managed repository rejection, including staged and untracked files.
- Wrong branch and uninitialized submodule rejection.
- In-progress Git operation rejection.
- Atomic state replacement under injected write failure.
- Event sequence persistence.
- Concurrent lock acquisition failure.
- Human and JSON status snapshots.

### 7.5 Exit Demonstration

From a temporary valid sprint repository, start the controller, observe a persisted initialized/blocked state, query JSON status from a second process, and show that a concurrent owner is rejected. Repeat with a dirty managed repository and show a mutation-free actionable failure.

### 7.6 Explicit Deferrals

- OpenCode HTTP calls.
- Agent prompts and results.
- Git commits.
- GitHub CI.
- Neovim commands.

## 8. Sprint 2: OpenCode Execution Layer

### 8.1 Goal

Connect the controller to an explicitly supplied OpenCode server and execute one fresh, observable, structured agent invocation through a reusable runner interface.

### 8.2 Deliverables

- `AgentRunner` protocol/interface.
- `OpenCodeServerRunner` implementation.
- Required `--server-url` handling for `run` and `resume`.
- Server URL and authentication handling without process-argument credentials.
- Health and version validation.
- Server workspace validation against the sprint root.
- Configured agent availability validation.
- Fresh session creation and descriptive session titles.
- Prompt submission with selected role and model.
- JSON-schema structured output handling.
- Session status/event monitoring with timeout.
- Invocation metadata, prompt, result, and sanitized transcript persistence.
- Agent abort support for controller shutdown paths where safe.
- Fake runner for deterministic state-machine tests.

### 8.3 Required Behavior

The controller must fail clearly when no URL is provided, the server is unreachable, health is false, the version is unsupported, or the server workspace is incorrect. It must never start another OpenCode server.

Every invocation must create a new session. Reusing an old session ID is an error. The session ID must be persisted immediately after creation so an interruption can be diagnosed.

Structured-output validation failure must be represented as an invocation failure, not interpreted from free-form prose.

### 8.4 Testing

- URL parsing and unsupported scheme rejection.
- Health timeout, authentication failure, unhealthy response, and version mismatch.
- Wrong-workspace diagnostics.
- Missing configured agent diagnostics.
- Fresh session creation per invocation.
- Synchronous and asynchronous completion observation as supported by the selected API path.
- Timeout and explicit abort behavior.
- Structured-output validation and retry exhaustion.
- Transcript sanitization and output-size bounds.
- Server credentials absent from persisted artifacts and command output.

### 8.5 Exit Demonstration

Run a non-mutating test role against a real active OpenCode server rooted at an example sprint repository. Observe a newly titled session in OpenCode Web, a validated structured result, and complete invocation records. Then supply a server rooted at the wrong directory and show a clear failure.

### 8.6 Explicit Deferrals

- Product implementation prompts.
- Agent staging and commits.
- Audit rounds.
- Server-loss resume behavior beyond recording the invocation failure.

## 9. Sprint 3: Neovim Client V1

### 9.1 Goal

Provide a thin Neovim interface that launches the controller independently of Neovim lifetime and renders controller progress without implementing workflow logic.

### 9.2 Repository

Implementation occurs in the `opencode_sprint_loop.lua` repository. After plugin commits are pushed, the controller repository updates its submodule gitlink in a separate parent commit.

### 9.3 Deliverables

- Conventional Lua plugin layout.
- `setup()` with configurable controller executable, sprint-root resolver, server-URL resolver, and optional web-URL resolver.
- Asynchronous command execution and output capture.
- Detached launch of `sprint-loop run`.
- Commands:
  - `SprintLoopStart`
  - `SprintLoopProgress`
  - `SprintLoopPause`
  - `SprintLoopResume`
  - `SprintLoopStop`
  - `SprintLoopOpenSession`
- Progress floating window or split.
- Clear command-level notifications and error rendering.
- Minimal plugin help/documentation.
- Lua tests using the project's selected Neovim test approach.

### 9.4 Required Behavior

The plugin must resolve all callbacks at command execution time rather than only at setup, because server URLs and working directories can change.

`SprintLoopStart` must reject a missing server URL before launching the process. It passes the URL and sprint root explicitly.

`SprintLoopProgress` must call `status --json` asynchronously and render state, reason, active role/session, commits, audit round, CI state, checklist counts, and last event when present. Unknown additional JSON fields must be ignored for forward compatibility.

Closing Neovim must not terminate the launched controller process. The plugin does not claim that the process survived until it confirms through later status.

### 9.5 Testing

- Setup defaults and callback validation.
- Correct argv construction without shell interpolation.
- Missing executable, sprint root, and server URL errors.
- Non-blocking command execution.
- Status JSON rendering for running, blocked, failed, and finished states.
- Malformed JSON and non-zero CLI exit handling.
- Detached job option behavior.
- Web URL absence and browser-open failures.

### 9.6 Exit Demonstration

Start the Sprint 2 test invocation from Neovim, view progress, close Neovim, reopen it, and confirm that status can still be read. Open the active OpenCode session through the configured web URL.

### 9.7 Explicit Deferrals

- Embedded OpenCode webviews.
- A server multiplexer.
- Editing configuration or findings from the progress window.
- Plugin-owned persistence.

## 10. Sprint 4: Builder and Git Handoff

### 10.1 Goal

Run a real Builder against the sprint specification and safely turn its staged handoff into a local implementation commit and sprint checkpoint.

### 10.2 Deliverables

- Builder role prompt assembly.
- Explicit inclusion of sprint-level and managed-repository instructions.
- Multisprint specification, sprint specification, and checklist input assembly.
- Mutating-agent structured result schema.
- Controller-provided commit-message path.
- Managed repository status and index inspection.
- Staged-diff validation.
- Commit-message validation.
- Controller-owned `git commit -F` operation.
- Implementation commit SHA capture.
- Commit-set persistence.
- Sprint repository gitlink update.
- Targeted sprint-history staging and local checkpoint commits.
- Failure handling for incomplete or inconsistent agent handoff.
- Git adapter fake for state-machine tests.

### 10.3 Required Behavior

The Builder may modify and stage implementation files but must not commit or push. The controller must not stage omitted implementation files on its behalf.

The controller rejects:

- An empty index after reported implementation.
- Task-related unstaged modifications.
- Untracked implementation files not staged by the agent.
- A missing, empty, or out-of-bounds commit-message file.
- Branch or HEAD changes made by the agent.
- Evidence that the agent committed or pushed.

When handoff is valid, the controller commits exactly the staged tree and records the resulting SHA. It then records the changed submodule gitlink in a local sprint checkpoint without pushing either repository.

### 10.4 Testing

- Prompt input completeness and deterministic ordering.
- Agent result status handling for completed, blocked, and failed.
- Added, modified, renamed, and deleted staged files.
- Empty index and mixed staged/unstaged rejection.
- Untracked file rejection.
- Invalid commit-message path and content rejection.
- Exact staged tree committed.
- Commit failure preservation and diagnostic state.
- Submodule gitlink checkpoint behavior.
- No use of blanket staging or destructive Git commands.

### 10.5 Exit Demonstration

Run a small example sprint through the Builder. Show the visible OpenCode session, staged handoff, controller-authored local implementation commit using the agent's message, updated commit set, and local sprint checkpoint. Confirm that no remote push or CI run occurred.

### 10.6 Explicit Deferrals

- Audit decisions.
- Push operations.
- CI monitoring.
- Multi-repository commit ordering.

## 11. Sprint 5: Audit Gate and Completion Assessment

### 11.1 Goal

Add evidence-based Auditor sessions and complete the local Builder/audit batching policy before any push.

### 11.2 Deliverables

- Auditor role prompt assembly.
- Audit structured-result schema.
- Actionable finding validation.
- Checklist assessment validation.
- Audit report persistence.
- Pre-CI audit round tracking.
- Findings-to-Builder prompt handoff.
- Early publication readiness when the first audit is clean.
- Blocking behavior when findings remain after the configured audit budget.
- Repeated-finding/no-progress detection within the budget.
- Checklist counts and remaining-effort information in status JSON.
- Neovim progress rendering updates for audit and checklist data.

### 11.3 Required Behavior

Every Auditor invocation reviews a clean implementation commit and has no edit permission. It performs a fresh review; prior reports are historical inputs only when useful for recognizing unresolved issues.

The controller must validate that every finding contains a requirement, concrete problem, expected outcome, and location when applicable. An empty findings list means the local gate is clean.

The controller must not run a second audit merely to consume the configured maximum when the first audit is clean. It must not publish known findings when the maximum is exhausted.

### 11.4 Testing

- Audit schema success and invalid-finding rejection.
- Checklist status and confidence validation.
- First-round clean transition.
- Findings -> Builder -> commit -> next-audit transition.
- Maximum-round blocked transition.
- Stable retention of prior audit reports.
- Equivalent repeated-finding detection.
- Auditor repository mutation detection.
- Status checklist counts and stale-assessment labeling while implementation resumes.
- Plugin rendering of completion evidence.

### 11.5 Exit Demonstration

Demonstrate one sprint where the first local audit is clean and one where the Auditor reports a finding that the Builder fixes in a second local commit. Show that neither path pushes until the audit gate is clean, and that persistent status reports checklist evidence.

### 11.6 Explicit Deferrals

- Actual push after readiness.
- Hosted CI.
- Final post-CI audit distinction.

## 12. Sprint 6: Publication, GitHub CI, and CI Fixer

### 12.1 Goal

Publish an audit-clean local commit batch, evaluate all GitHub checks for the exact pushed tip, and repair failed CI through constrained CI Fixer sessions.

### 12.2 Deliverables

- Controller-owned push operation to the configured remote and branch.
- Exact pushed SHA verification.
- Sprint repository push at publication boundaries.
- `CIService` protocol/interface.
- GitHub implementation using stable JSON output from `gh` or documented API responses.
- Check discovery for the exact implementation SHA.
- Terminal conclusion aggregation.
- Configuration handling for skipped, neutral, and zero-check cases.
- CI polling with bounded intervals and meaningful-change events.
- CI evidence and focused failure-log persistence.
- CI Fixer prompt assembly and mutating-agent handoff.
- CI Fixer commit and immediate republish path.
- CI fix attempt limit.
- Fake CI service and deterministic conclusion fixtures.

### 12.3 Required Behavior

The controller pushes only after the pre-CI audit gate is clean. It records the remote-observed tip and monitors only that exact SHA.

CI remains pending while any applicable check is non-terminal. One unacceptable conclusion makes the aggregate result failed after sufficient evidence is collected. Results from superseded SHAs cannot advance the state machine.

On failure, the CI Fixer receives focused evidence and is constrained to the smallest relevant repair. A successful Fixer handoff is locally committed and pushed without a full pre-CI audit. The new SHA receives an entirely new CI evaluation.

### 12.4 Testing

- Push success, rejection, authentication failure, and remote divergence.
- Exact SHA association.
- Multiple workflows completing in different orders.
- Pending, success, skipped, neutral, failure, timeout, cancelled, action-required, startup-failure, stale, and unknown conclusions.
- Zero-check policy.
- Superseded run and old-SHA result rejection.
- Polling without duplicate no-change events or checkpoint commits.
- Focused failure evidence size limits.
- CI Fixer staged handoff and commit.
- CI fix attempt exhaustion.
- GitHub credentials absent from artifacts.

### 12.5 Exit Demonstration

Push an audit-clean example commit to a test GitHub repository. Demonstrate waiting for multiple checks. Cause a controlled CI failure, launch a visible CI Fixer, commit and push its correction, and observe all checks passing for the new exact SHA.

### 12.6 Explicit Deferrals

- Final post-CI audit completion.
- Full pause/resume behavior during every state.
- CI providers other than GitHub.

## 13. Sprint 7: Complete Loop, Controls, and Recovery

### 13.1 Goal

Connect green CI to the final audit, complete terminal behavior, and make pause, resume, stop, server-loss, and interruption handling safe across the full state machine.

### 13.2 Deliverables

- Final-audit state and prompt context.
- Clean final-audit transition to `finished`.
- Final findings transition to a new local Builder batch.
- Re-push, complete CI, and re-audit behavior after final findings.
- Pause handling that completes an active invocation handoff, checkpoints `paused`, and exits at a documented safe boundary.
- Resume revalidation for all resumable states.
- Controlled stop behavior that checkpoints `stopped` and exits without discarding completed work.
- Periodic OpenCode server liveness checks, including during CI wait.
- Server-unavailable grace period.
- `blocked/server_unavailable` checkpoint and exit.
- Resume with a new supplied server URL.
- Interrupted clean-worktree invocation rerun.
- `blocked/interrupted_dirty_worktree` preservation behavior.
- Unexpected external branch, HEAD, index, worktree, and gitlink change detection.
- Full iteration and no-progress limits.
- Plugin behavior for pause, resume, stop, blocked recovery, and active-session opening.

### 13.3 Required Behavior

No path reaches `finished` unless the exact current implementation commit has green aggregate CI and a clean final audit.

If final findings produce a new commit, previous green CI no longer applies. The new commit must pass publication, CI, and final audit again.

Server loss must never cause the controller to launch a replacement server. After the grace period, the controller records and checkpoints the blocked state and exits. Resume validates the new URL and all repository assumptions.

Partial work from an interrupted active invocation must never be automatically reset, stashed, staged, or committed in V1.

### 13.4 Testing

- Green CI -> clean final audit -> finished.
- Green CI -> findings -> Builder -> local audit -> push -> CI -> final audit.
- Pause requests in every non-terminal state.
- Resume from each supported paused or blocked state.
- Stop during idle, agent, and CI phases according to documented boundaries.
- Server loss during agent execution, local audit, and CI wait.
- Resume with unchanged URL and changed valid URL.
- Clean interrupted invocation rerun.
- Dirty interrupted worktree preservation.
- External commit, branch switch, user edit, and submodule pointer change detection.
- Implementation-cycle, audit-round, and CI-fix limit enforcement.
- No transition to success after stale external evidence.

### 13.5 Exit Demonstration

Run an end-to-end example sprint to completion. During a second run, close Neovim and confirm continuation. Then terminate the OpenCode server, observe the blocked checkpoint and controller exit, restart OpenCode on a different port, and resume through the plugin. Finally demonstrate that partial dirty work is preserved and blocks automatic recovery.

### 13.6 Explicit Deferrals

- Automatic repair of interrupted partial work.
- Cross-host controller migration.
- Remote browser dashboard.

## 14. Sprint 8: Hardening and V1 Release

### 14.1 Goal

Validate the complete system under realistic failure conditions, stabilize user-facing contracts, document installation and operation, and produce the first V1 release candidates for both repositories.

### 14.2 Deliverables

- Full state-transition table reviewed against the V1 specification.
- End-to-end test harness with fake OpenCode and GitHub services.
- Real integration smoke tests gated by environment configuration.
- Crash/restart fault-injection tests around state writes, Git commits, pushes, and invocation completion.
- Transcript and CI-output redaction review.
- Artifact size-limit tests.
- CLI help, installation, configuration, and troubleshooting documentation.
- Example sprint-history repository fixture or template.
- Neovim help file and setup examples for generic and mkchad usage.
- Neovim health check for executable and callback availability if practical.
- Versioning and compatibility policy.
- Changelogs and release process for controller and plugin repositories.
- Performance review ensuring status and Neovim interactions remain responsive.

### 14.3 Required Behavior

The complete V1 acceptance criteria must pass without manual state editing. Fault injection may lead to a safe blocked or failed state, but must not silently lose recorded completed work or corrupt the state file.

Documentation must clearly distinguish:

- Controller source repository.
- Plugin source repository.
- User-created sprint-history repository.
- Managed implementation repository.
- Local OpenCode server URL versus optional browser-facing web URL.

### 14.4 Testing

- Full V1 acceptance suite.
- State migration rejection for unknown versions.
- Process termination at every durable transition.
- Malformed and oversized OpenCode responses.
- Malformed and oversized GitHub logs.
- Authentication and credential-redaction checks.
- Long-running CI wait with stable state-file and Git history size.
- Plugin operation after controller restart and Neovim restart.
- Installation in a clean mkchad image.
- Example sprint completion from start to final checkpoint push.

### 14.5 Exit Demonstration

Install release candidates into a clean mkchad environment, initialize an example sprint repository, complete a sprint through the Neovim commands, inspect activity from OpenCode Web, and verify the resulting implementation history and sprint-history records. Repeat selected failure scenarios from the acceptance specification.

### 14.6 Release Gate

V1 may be tagged only when:

- Every V1 acceptance criterion passes or has a documented, approved exception.
- Controller and plugin compatibility versions are documented.
- Both repositories are clean and their release commits are pushed.
- The controller repository references the intended released plugin submodule commit.
- Installation and rollback instructions have been exercised.

## 15. Requirement-to-Sprint Map

| Requirement Area | Primary Sprint | Completion Sprint |
| --- | --- | --- |
| Configuration and repository validation | 1 | 8 |
| Durable state, events, and locking | 1 | 7 |
| OpenCode server validation | 2 | 7 |
| Fresh visible agent sessions | 2 | 8 |
| Structured agent results and transcripts | 2 | 8 |
| Neovim launch and status | 3 | 8 |
| Builder role and staged handoff | 4 | 8 |
| Controller-owned implementation commits | 4 | 8 |
| Sprint checkpoint commits | 4 | 8 |
| Pre-CI audit batching | 5 | 8 |
| Checklist completion assessment | 5 | 8 |
| Push boundaries | 6 | 8 |
| Exact-SHA aggregate GitHub CI | 6 | 8 |
| CI Fixer loop | 6 | 8 |
| Final audit and completion | 7 | 8 |
| Pause, resume, and stop | 7 | 8 |
| Server-loss recovery | 7 | 8 |
| Security and release documentation | 8 | 8 |

## 16. Test Strategy

### 16.1 Unit Tests

Unit tests should cover:

- Data-model validation.
- State transition guards.
- Audit and CI aggregation rules.
- Prompt input construction.
- Status projection.
- Redaction and bounded-output behavior.

### 16.2 Repository Integration Tests

Temporary local Git repositories should cover:

- Submodule initialization and gitlinks.
- Clean and dirty states.
- Staged handoffs.
- Commits and branch verification.
- Local bare remotes and push behavior.
- Divergence and rejected pushes.

These tests must configure repository-local Git identity rather than depend on a developer's global configuration.

### 16.3 Service Contract Tests

Fake OpenCode and GitHub HTTP/CLI adapters should provide deterministic fixtures for:

- Session lifecycle and event sequences.
- Structured outputs and errors.
- Server loss and timeout.
- Check discovery and conclusion transitions.
- Superseded commits.

### 16.4 Real Integration Tests

Real OpenCode and GitHub tests should be opt-in because they consume credentials, model usage, network time, and CI resources. Their existence must not make the default local test suite nondeterministic.

### 16.5 Neovim Tests

Plugin tests should verify command registration, callback resolution, argv construction, async behavior, JSON rendering, and detached process options. A small end-to-end test may use a fake `sprint-loop` executable that emits controlled status documents.

## 17. Documentation Strategy

Documentation evolves with implementation:

- Sprint 1 establishes configuration, CLI, and state references.
- Sprint 2 documents server requirements and authentication.
- Sprint 3 documents Neovim installation and setup.
- Sprint 4 documents repository and agent Git contracts.
- Sprint 5 documents audit findings and completion assessment.
- Sprint 6 documents GitHub permissions and CI semantics.
- Sprint 7 documents controls and recovery runbooks.
- Sprint 8 consolidates installation, operations, troubleshooting, and release material.

Behavioral documentation must describe actual implemented behavior. Aspirational features belong in a roadmap or future-work section.

## 18. Major Risks and Mitigations

### 18.1 OpenCode API Evolution

Risk: The server API or structured-output fields change.

Mitigation: Isolate the API behind `AgentRunner`, validate server version, use documented endpoints, preserve response fixtures, and fail clearly on unsupported versions.

### 18.2 Agent Handoff Inconsistency

Risk: An agent reports completion without staging all work or changes Git history itself.

Mitigation: Validate HEAD, branch, index, worktree, commit message, and result schema before every controller commit. Never repair the handoff by broad staging.

### 18.3 CI Ambiguity

Risk: Old, skipped, pending, or absent checks are mistaken for green CI.

Mitigation: Associate every decision with the exact pushed SHA, aggregate all applicable checks, configure neutral/skipped handling explicitly, and fail closed on zero or unknown checks.

### 18.4 Process and Server Lifetime Mismatch

Risk: Neovim exits, the server restarts on another port, or a controller process is orphaned.

Mitigation: Detached launch, operating-system lock, state-visible process identity, server heartbeat, resumable blocked exit, and a newly supplied URL on resume.

### 18.5 Partial Agent Changes

Risk: Server or process failure leaves a partially modified repository.

Mitigation: Preserve the worktree exactly, record interruption metadata, and require manual resolution in V1.

### 18.6 Transcript Data Exposure

Risk: Committed transcripts contain credentials or excessive sensitive source context.

Mitigation: Sanitized exports, redaction, size bounds, private sprint repositories, and explicit operational warnings.

### 18.7 Checkpoint Commit Volume

Risk: Polling or low-value events create noisy Git history.

Mitigation: Commit only meaningful durable transitions, append events without a commit per event, and avoid recording unchanged polling observations.

### 18.8 Plugin and Controller Version Skew

Risk: The plugin expects status fields or commands unavailable in the installed controller.

Mitigation: Version the status envelope, ignore unknown fields, validate required fields, document compatibility, and release the parent submodule pointer deliberately.

## 19. Scope Change Policy

A proposed change belongs in V1 only if it is required to satisfy an existing acceptance criterion, correct a safety defect, or make a required workflow operable. Other improvements should be recorded as future work.

Changes to durable schemas, CLI contracts, Git ownership, CI success semantics, or terminal-state rules require an explicit update to the V1 software specification before implementation.

The multi-repository and multiplexer designs may be considered when choosing interfaces, but their implementation must not delay the V1 single-repository loop.

## 20. V1 Completion Outcome

At the end of this plan, a user can:

1. Open a clean sprint-history repository in mkchad.
2. Start an authenticated OpenCode server rooted at that repository.
3. Launch the sprint loop from Neovim.
4. Observe every fresh agent session through OpenCode Web.
5. Query evidence-based progress from Neovim.
6. Allow local Builder and Auditor rounds to converge before publication.
7. Run all GitHub CI for the exact published commit.
8. Automatically repair bounded CI failures.
9. Require green CI and a clean final audit before completion.
10. Pause, resume, stop, or recover from server loss without silently losing or overwriting work.
11. Inspect a coherent Git-backed history of specifications, state transitions, invocations, audits, CI evidence, and implementation commit references.
