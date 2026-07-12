# Controller V1 Sprint 1 Checklist: Controller Foundation

## Usage

This checklist tracks implementation of `docs/controller-v1/1/sprint_spec.md`. It does not replace the sprint specification or the authoritative V1 documents.

An item may be checked only when its implementation, tests, and required documentation are complete. If implementation changes a durable contract, update the specification before checking the item.

## 1. Scope and Setup

- [ ] **S1-SCOPE-001** Read `AGENTS.md`, `docs/v1_final_software_specification.md`, `docs/multi_sprint_plan.md`, and the Sprint 1 specification before implementation.
- [ ] **S1-SCOPE-002** Confirm the working tree is clean or identify unrelated existing changes that must remain untouched.
- [ ] **S1-SCOPE-003** Record the selected Python build backend, runtime dependencies, and development dependencies in `pyproject.toml`; justify every runtime dependency and pin all dependencies through the selected reproducible development/build approach.
- [ ] **S1-SCOPE-004** Confirm Python 3.11 is the minimum supported runtime.
- [ ] **S1-SCOPE-005** Keep OpenCode networking, GitHub CI, agent roles, Git commits/pushes, Neovim code, and multi-repository execution out of Sprint 1.
- [ ] **S1-SCOPE-006** Keep platform-specific ownership behavior scoped to Linux in the mkchad environment.

## 2. Package Foundation

- [ ] **S1-PKG-001** Add a valid `pyproject.toml` with project name, version, Python requirement, build backend, dependencies, and development test configuration.
- [ ] **S1-PKG-002** Register the `sprint-loop` console entry point.
- [ ] **S1-PKG-003** Create the Python package under `src/opencode_sprint_loop/`.
- [ ] **S1-PKG-004** Expose a package version used by `sprint-loop --version` and status output.
- [ ] **S1-PKG-005** Add a test package with separate unit and integration-oriented areas or equivalent clear organization.
- [ ] **S1-PKG-006** Ensure a clean editable or wheel installation exposes `sprint-loop` without relying on the repository working directory.
- [ ] **S1-PKG-007** Configure the default test command so it runs without network access, credentials, OpenCode, GitHub, or global Git identity.
- [ ] **S1-PKG-008** Add formatting, linting, and type-check configuration selected for the project, or document why a category is intentionally deferred.
- [ ] **S1-PKG-009** Add concise docstrings to public Python APIs, including errors and side effects.

## 3. CLI Surface

- [ ] **S1-CLI-001** Implement `sprint-loop --help` with all five V1 commands.
- [ ] **S1-CLI-002** Implement `sprint-loop --version`.
- [ ] **S1-CLI-003** Implement parsing for `run --root <path> --server-url <url>`.
- [ ] **S1-CLI-004** Implement parsing for `status --root <path>`.
- [ ] **S1-CLI-005** Implement parsing for `status --root <path> --json`.
- [ ] **S1-CLI-006** Implement parsing for `pause --root <path>`.
- [ ] **S1-CLI-007** Implement parsing for `resume --root <path> --server-url <url>`.
- [ ] **S1-CLI-008** Implement parsing for `stop --root <path>`.
- [ ] **S1-CLI-009** Make missing and malformed arguments return non-zero with concise standard-error diagnostics.
- [ ] **S1-CLI-010** Ensure JSON commands write exactly one JSON document to standard output with no diagnostic prose mixed into it.
- [ ] **S1-CLI-011** Ensure expected configuration, repository, state, and ownership errors do not normally print Python tracebacks.
- [ ] **S1-CLI-012** Implement mutation-free `feature_not_implemented` responses for `pause`, `resume`, and `stop`.
- [ ] **S1-CLI-013** Require `--server-url` for `run` while performing no Sprint 1 network operation.
- [ ] **S1-CLI-014** Treat `--server-url` as opaque non-empty input without parsing it in Sprint 1.
- [ ] **S1-CLI-015** Do not log or persist the opaque server URL value.

## 4. Error Model

- [ ] **S1-ERR-001** Define structured controller exceptions or results carrying a stable reason code and safe human message.
- [ ] **S1-ERR-002** Implement every required Sprint 1 reason code from the sprint specification or document an equivalent stable mapping.
- [ ] **S1-ERR-003** Include relevant configuration field paths in validation errors.
- [ ] **S1-ERR-004** Include canonical repository paths and expected conditions in Git preflight errors where safe.
- [ ] **S1-ERR-005** Include a corrective suggestion for dirty repositories, wrong branches, uninitialized submodules, active ownership, and unsupported schemas.
- [ ] **S1-ERR-006** Prevent credentials, environment dumps, URL user-info, query values, and fragments from appearing in errors.
- [ ] **S1-ERR-007** Map expected failures to non-zero CLI exits consistently.
- [ ] **S1-ERR-008** Distinguish the intentional `execution_not_implemented` block from `internal_error`.

## 5. JSON Loading and Configuration Model

- [ ] **S1-CFG-001** Load `sprint_config.json` as bounded UTF-8 input.
- [ ] **S1-CFG-002** Reject malformed JSON.
- [ ] **S1-CFG-003** Reject duplicate JSON object keys at every nesting level.
- [ ] **S1-CFG-004** Reject a non-object top-level value.
- [ ] **S1-CFG-005** Require `schema_version` and accept only integer `1`.
- [ ] **S1-CFG-006** Reject JSON booleans where integer values are required.
- [ ] **S1-CFG-007** Reject unknown top-level and nested fields.
- [ ] **S1-CFG-008** Validate `multisprint` against the documented identifier and length rules.
- [ ] **S1-CFG-009** Validate `sprint` as a positive integer.
- [ ] **S1-CFG-010** Require `repositories` to contain exactly one repository object.
- [ ] **S1-CFG-011** Validate repository name, relative path, branch, and remote fields.
- [ ] **S1-CFG-012** Reject repository paths containing `..` or resolving outside the sprint root.
- [ ] **S1-CFG-013** Reject a repository path resolving to the sprint root itself.
- [ ] **S1-CFG-014** Validate all three required document path fields.
- [ ] **S1-CFG-015** Require document paths to remain within the sprint root and resolve to distinct, non-empty regular files.
- [ ] **S1-CFG-016** Validate all three agent identifiers.
- [ ] **S1-CFG-017** Require one local definition for each valid agent identifier in `.opencode/agents/`.
- [ ] **S1-CFG-018** Validate all model identifiers in non-empty `provider/model` form without contacting providers.
- [ ] **S1-CFG-019** Validate `pre_ci_audit.enabled` as a boolean.
- [ ] **S1-CFG-019A** Preserve either `pre_ci_audit.enabled` value without inventing its later execution semantics.
- [ ] **S1-CFG-020** Validate `pre_ci_audit.max_rounds` as a positive integer.
- [ ] **S1-CFG-021** Validate every configured loop and timeout limit as a positive integer.
- [ ] **S1-CFG-022** Accept only `github` for `ci.provider`.
- [ ] **S1-CFG-023** Validate CI poll interval and boolean fields.
- [ ] **S1-CFG-024** Validate `ci.zero_checks` as a non-empty lower-case identifier, preserve it without interpretation, and document `error` as recommended.
- [ ] **S1-CFG-025** Return typed immutable or controlled configuration data to downstream code.

## 6. Path Resolution

- [ ] **S1-PATH-001** Canonicalize `--root` without accepting a nonexistent path.
- [ ] **S1-PATH-002** Resolve every configured relative path from the canonical sprint root.
- [ ] **S1-PATH-003** Detect symlink traversal that escapes the sprint root.
- [ ] **S1-PATH-004** Derive `info/<multisprint>/<sprint>/` only from validated identity values.
- [ ] **S1-PATH-005** Keep run-ownership and persistence advisory locks in local non-worktree locations derived from the sprint repository.
- [ ] **S1-PATH-006** Avoid creating runtime paths during configuration parsing, path validation, status with no run, or any failed preflight.

## 7. Git Command Adapter

- [ ] **S1-GIT-001** Add a narrow Git inspection adapter with explicit working directory and argument-array invocation.
- [ ] **S1-GIT-002** Never invoke Git through a shell command string.
- [ ] **S1-GIT-003** Capture standard output and standard error separately.
- [ ] **S1-GIT-004** Use machine-readable Git output, including porcelain v2 and NUL delimiters where applicable.
- [ ] **S1-GIT-005** Set deterministic locale handling for unavoidable textual diagnostics.
- [ ] **S1-GIT-006** Represent command failure without leaking unrelated environment data.
- [ ] **S1-GIT-007** Ensure the Sprint 1 Git adapter exposes no reset, clean, checkout, switch, stash, add, commit, or push operation.

## 8. Sprint Repository Preflight

- [ ] **S1-ROOT-001** Reject a missing or non-directory root.
- [ ] **S1-ROOT-002** Reject a root that is not a Git worktree.
- [ ] **S1-ROOT-003** Reject a child directory when its canonical path is not the worktree top level.
- [ ] **S1-ROOT-004** Reject a bare repository.
- [ ] **S1-ROOT-005** Require sprint repository HEAD to resolve to a commit.
- [ ] **S1-ROOT-006** Require root `AGENTS.md`.
- [ ] **S1-ROOT-007** Require valid configuration, documents, and local agent definitions.
- [ ] **S1-ROOT-008** Reject staged sprint repository changes.
- [ ] **S1-ROOT-009** Reject unstaged sprint repository changes.
- [ ] **S1-ROOT-010** Reject all untracked sprint repository files and directories.
- [ ] **S1-ROOT-011** Reject an active merge.
- [ ] **S1-ROOT-012** Reject either supported form of active rebase metadata.
- [ ] **S1-ROOT-013** Reject an active cherry-pick.
- [ ] **S1-ROOT-014** Reject an active revert.
- [ ] **S1-ROOT-015** Reject an active bisect.

## 9. Managed Submodule Preflight

- [ ] **S1-SUB-001** Require the configured path to be tracked with gitlink mode `160000`.
- [ ] **S1-SUB-002** Require `.gitmodules` registration at the same configured path.
- [ ] **S1-SUB-003** Reject an uninitialized submodule.
- [ ] **S1-SUB-004** Require the managed repository worktree root to equal the configured canonical path.
- [ ] **S1-SUB-005** Reject a bare managed repository.
- [ ] **S1-SUB-006** Require managed repository HEAD to resolve to a commit.
- [ ] **S1-SUB-007** Require managed HEAD to equal the sprint repository index gitlink SHA.
- [ ] **S1-SUB-008** Require the configured symbolic branch exactly.
- [ ] **S1-SUB-009** Reject detached HEAD.
- [ ] **S1-SUB-010** Require the configured remote to exist.
- [ ] **S1-SUB-011** Reject staged managed repository changes.
- [ ] **S1-SUB-012** Reject unstaged managed repository changes.
- [ ] **S1-SUB-013** Reject all untracked managed repository files and directories.
- [ ] **S1-SUB-014** Reject active merge, rebase, cherry-pick, revert, and bisect operations.
- [ ] **S1-SUB-015** Reject dirty nested submodule state surfaced by managed repository status.
- [ ] **S1-SUB-016** Do not initialize, update, sync, or otherwise modify submodules automatically.

## 10. Validation No-Mutation Guarantee

- [ ] **S1-SAFE-001** Run every configuration and Git preflight check before creating `info/`.
- [ ] **S1-SAFE-002** Prove representative invalid configuration failures leave no runtime files.
- [ ] **S1-SAFE-003** Prove representative sprint repository failures leave no runtime files.
- [ ] **S1-SAFE-004** Prove representative managed repository failures leave no runtime files.
- [ ] **S1-SAFE-005** Compare sprint and managed HEADs before and after failure.
- [ ] **S1-SAFE-006** Compare branches before and after failure.
- [ ] **S1-SAFE-007** Compare index identities or staged diffs before and after failure.
- [ ] **S1-SAFE-008** Compare porcelain status before and after failure.
- [ ] **S1-SAFE-009** Verify no commit, stash, reset, clean, checkout, switch, add, or push command is invoked.
- [ ] **S1-SAFE-010** Check for existing state and event artifacts before worktree cleanliness so valid controller-owned runtime files produce `run_already_exists` and malformed/incomplete artifacts produce persistence errors rather than `dirty_sprint_repository`.
- [ ] **S1-SAFE-011** Repeat existing-state and concurrency-sensitive repository checks after acquiring ownership and before runtime mutation.
- [ ] **S1-SAFE-012** Prove a post-lock revalidation failure creates no runtime files and releases ownership.

## 11. State Model

- [ ] **S1-STATE-001** Implement the complete versioned Sprint 1 state shape from the sprint specification.
- [ ] **S1-STATE-002** Validate state schema version and reject unknown versions.
- [ ] **S1-STATE-003** Generate one UUID run ID per new run.
- [ ] **S1-STATE-004** Preserve validated multisprint and sprint identity.
- [ ] **S1-STATE-005** Represent state and structured reason separately.
- [ ] **S1-STATE-006** Require reasons for blocked, failed, and stopped states.
- [ ] **S1-STATE-007** Represent process identity and activity.
- [ ] **S1-STATE-008** Persist null server URL and version fields in Sprint 1.
- [ ] **S1-STATE-009** Reserve active invocation as null in Sprint 1.
- [ ] **S1-STATE-010** Initialize local and pushed commit maps with the configured repository key and null SHA.
- [ ] **S1-STATE-011** Initialize audit fields and configured pre-CI maximum.
- [ ] **S1-STATE-012** Initialize CI, counters, checklist, and control fields exactly as documented.
- [ ] **S1-STATE-012A** Reserve empty checklist items and null resume-state fields.
- [ ] **S1-STATE-013** Track last event sequence, creation time, update time, and terminal result.
- [ ] **S1-STATE-014** Reject missing required fields and invalid types instead of filling silent defaults when reading persisted state.
- [ ] **S1-STATE-015** Round-trip representative state through serialization and validation.
- [ ] **S1-STATE-016** Validate the complete V1 state-name vocabulary and documented terminal classifications.
- [ ] **S1-STATE-017** Validate terminal-result nullability for Sprint 1 non-terminal states.

## 12. Atomic State Persistence

- [ ] **S1-ATOM-001** Serialize and validate complete state before replacing the current file.
- [ ] **S1-ATOM-002** Write through a sibling temporary file on the same filesystem.
- [ ] **S1-ATOM-003** Use restrictive file permissions that do not broaden final state access.
- [ ] **S1-ATOM-004** Flush and `fsync` the temporary state file.
- [ ] **S1-ATOM-005** Replace `state.json` atomically.
- [ ] **S1-ATOM-006** `fsync` the containing directory where supported.
- [ ] **S1-ATOM-007** Clean up handled abandoned temporary files when safe.
- [ ] **S1-ATOM-008** Inject failures before write, during write, before replace, and after replace.
- [ ] **S1-ATOM-009** Verify readers observe either the complete previous state or complete next state, never truncated JSON.

## 13. Event Log

- [ ] **S1-EVENT-001** Implement the versioned event envelope.
- [ ] **S1-EVENT-002** Write UTF-8 JSONL with one complete object and newline per event.
- [ ] **S1-EVENT-003** Start sequences at one and increment by exactly one.
- [ ] **S1-EVENT-004** Preserve existing event bytes unchanged when appending.
- [ ] **S1-EVENT-005** Flush and `fsync` successful appends.
- [ ] **S1-EVENT-006** Validate event schema, sequence, timestamp, run ID, type, state, and object payload.
- [ ] **S1-EVENT-007** Reject unknown schemas, malformed lines, duplicate sequences, gaps, and run-ID mismatches.
- [ ] **S1-EVENT-008** Determine the next sequence from validated persisted data.
- [ ] **S1-EVENT-009** Record `run.started`, validating `state.entered`, and `run.blocked` for a successful placeholder run.
- [ ] **S1-EVENT-010** Include prior state and reason information in transition payloads where applicable.
- [ ] **S1-EVENT-011** Prevent credential-bearing values from entering event payloads.
- [ ] **S1-EVENT-012** Reject event logs containing a different run ID in Sprint 1.
- [ ] **S1-EVENT-013** Detect short writes and malformed partial final lines without truncating or rewriting the event log.

## 14. State/Event Consistency

- [ ] **S1-CONSIST-001** Append and sync each transition event before replacing state.
- [ ] **S1-CONSIST-002** Require `state.last_event_sequence` to resolve to an existing matching event.
- [ ] **S1-CONSIST-003** Verify the referenced event run ID and resulting state match state.
- [ ] **S1-CONSIST-004** Treat an event log behind state as corruption.
- [ ] **S1-CONSIST-005** Treat an event log ahead of state as `inconsistent_persistence`.
- [ ] **S1-CONSIST-006** Do not automatically replay ahead events in Sprint 1.
- [ ] **S1-CONSIST-007** Add fault-injection coverage for interruption between event sync and state replacement.
- [ ] **S1-CONSIST-008** Hold the exclusive persistence lock across event append and state replacement.
- [ ] **S1-CONSIST-009** Hold the shared persistence lock across status reads of events and state so normal transitions cannot appear inconsistent.
- [ ] **S1-CONSIST-010** Acquire the persistence lock before creating first-run runtime paths and hold it through the first complete event/state pair.

## 15. Transition Guard

- [ ] **S1-TRANS-001** Centralize state transitions outside CLI handlers.
- [ ] **S1-TRANS-002** Define constrained workflow state and event type values.
- [ ] **S1-TRANS-003** Permit no-run to `initializing` with `run.started`.
- [ ] **S1-TRANS-004** Permit `initializing` to `validating` with `state.entered`.
- [ ] **S1-TRANS-005** Permit `validating` to `blocked` with `run.blocked` and required reason.
- [ ] **S1-TRANS-006** Reject unknown states and events.
- [ ] **S1-TRANS-007** Reject disallowed source/destination combinations.
- [ ] **S1-TRANS-008** Update timestamps and event sequence through the transition operation.
- [ ] **S1-TRANS-009** Persist the event and resulting state through the documented consistency ordering.
- [ ] **S1-TRANS-010** Cover the best-effort failed transition when an internal error occurs after persistence is available.

## 16. Locking and Ownership

- [ ] **S1-LOCK-001** Implement an OS advisory exclusive lock as ownership source of truth.
- [ ] **S1-LOCK-002** Store the OS lock outside the sprint repository worktree.
- [ ] **S1-LOCK-003** Acquire ownership only after read-only preflight succeeds.
- [ ] **S1-LOCK-004** Write versioned `lock.json` only after ownership acquisition.
- [ ] **S1-LOCK-005** Record run ID, PID, process-start identity when available, hostname, and UTC start time.
- [ ] **S1-LOCK-006** Reject a concurrent `run` while the OS lock is held.
- [ ] **S1-LOCK-007** Keep `status` available while another process owns the run lock.
- [ ] **S1-LOCK-008** Do not treat `lock.json` existence as active ownership.
- [ ] **S1-LOCK-009** Permit stale metadata replacement only after obtaining the OS lock.
- [ ] **S1-LOCK-010** Release the OS lock on normal placeholder exit.
- [ ] **S1-LOCK-011** Release the OS lock on handled failures.
- [ ] **S1-LOCK-012** Avoid PID-only liveness decisions when Linux process-start identity is available.
- [ ] **S1-LOCK-013** Serialize process-start identity as the documented opaque Linux boot/process identity string or null when unavailable.
- [ ] **S1-LOCK-014** Implement the separate shared/exclusive persistence lock.
- [ ] **S1-LOCK-015** Prove malformed descriptive lock metadata does not prevent acquisition when the OS ownership lock is available.

## 17. Status Projection

- [ ] **S1-STATUS-001** Implement no-run human status without creating worktree or runtime files; permit only the local persistence lock under Git metadata.
- [ ] **S1-STATUS-002** Implement no-run JSON status with every documented nullable field.
- [ ] **S1-STATUS-003** Implement persisted-run human status.
- [ ] **S1-STATUS-004** Implement persisted-run JSON status using the stable documented envelope.
- [ ] **S1-STATUS-005** Include schema version, controller version, and canonical sprint root.
- [ ] **S1-STATUS-006** Include run existence, computed process activity, run ID, and sprint identity.
- [ ] **S1-STATUS-007** Include state and safe reason.
- [ ] **S1-STATUS-008** Include active invocation placeholders.
- [ ] **S1-STATUS-009** Include local and pushed commit maps.
- [ ] **S1-STATUS-010** Include audit, CI, counters, and checklist projections.
- [ ] **S1-STATUS-011** Include the last event and update timestamp.
- [ ] **S1-STATUS-012** Compute process activity from current evidence instead of persisted boolean or lock metadata alone.
- [ ] **S1-STATUS-013** Reject malformed, unsupported, or inconsistent persisted data with actionable errors.
- [ ] **S1-STATUS-014** Test JSON as parsed data rather than relying on key order or formatting.
- [ ] **S1-STATUS-015** Reject a missing, non-directory, non-Git, child-directory, or bare status root without creating worktree or runtime files.
- [ ] **S1-STATUS-016** Require structurally valid configuration to locate sprint state while not requiring a clean worktree.
- [ ] **S1-STATUS-017** Wait only for the short persistence critical section and remain readable while the run-ownership lock is held.

## 18. Sprint 1 Run Flow

- [ ] **S1-RUN-001** Parse root and server URL without performing network access.
- [ ] **S1-RUN-002** Complete configuration and repository preflight before runtime mutation.
- [ ] **S1-RUN-003** Reject any existing Sprint 1 run before worktree cleanliness validation.
- [ ] **S1-RUN-004** Acquire exclusive ownership.
- [ ] **S1-RUN-004A** Repeat existing-state and concurrency-sensitive repository validation after ownership acquisition.
- [ ] **S1-RUN-005** Create required runtime directories after ownership.
- [ ] **S1-RUN-006** Generate run and process metadata.
- [ ] **S1-RUN-007** Persist `initializing`, `validating`, and placeholder blocked transitions.
- [ ] **S1-RUN-008** Use reason code `execution_not_implemented` for the placeholder block.
- [ ] **S1-RUN-009** Mark persisted process activity false before normal process exit.
- [ ] **S1-RUN-009A** Let status compute actual process activity from ownership evidence even when final lifecycle intent is already inactive.
- [ ] **S1-RUN-010** Release ownership.
- [ ] **S1-RUN-011** Exit non-zero without presenting the placeholder as an internal crash.
- [ ] **S1-RUN-012** Invoke no OpenCode, GitHub, Git mutation, or Neovim behavior.

## 19. Test Fixtures

- [ ] **S1-FIX-001** Create temporary repositories outside the source worktree.
- [ ] **S1-FIX-002** Configure synthetic Git identity locally in each committing fixture repository.
- [ ] **S1-FIX-003** Create a local bare managed remote.
- [ ] **S1-FIX-004** Create a managed repository with an initial commit, configured branch, and remote.
- [ ] **S1-FIX-005** Create a sprint repository with an initial commit.
- [ ] **S1-FIX-006** Add the managed repository as a real Git submodule.
- [ ] **S1-FIX-007** Create required AGENTS, agent definition, specification, checklist, and configuration files.
- [ ] **S1-FIX-008** Commit a clean valid fixture baseline.
- [ ] **S1-FIX-009** Add helpers for staged, unstaged, and untracked changes in either repository.
- [ ] **S1-FIX-010** Add helpers for wrong branch, detached HEAD, and missing remote.
- [ ] **S1-FIX-011** Add helpers for uninitialized submodule and gitlink/HEAD mismatch.
- [ ] **S1-FIX-012** Add helpers for merge, rebase, cherry-pick, revert, and bisect state.
- [ ] **S1-FIX-013** Add helpers for malformed configuration, state, event, and lock records.
- [ ] **S1-FIX-014** Keep all fixtures offline and independent of the user's home Git configuration.

## 20. Automated Verification

- [ ] **S1-TEST-001** Add unit coverage for every configuration rule.
- [ ] **S1-TEST-002** Add unit coverage for duplicate-key JSON detection.
- [ ] **S1-TEST-003** Add unit coverage for path containment and symlink escape.
- [ ] **S1-TEST-004** Add unit coverage for state and event model validation.
- [ ] **S1-TEST-005** Add unit coverage for every allowed and representative rejected transition.
- [ ] **S1-TEST-006** Add atomic-write fault-injection coverage.
- [ ] **S1-TEST-007** Add append-only event and consistency fault coverage.
- [ ] **S1-TEST-008** Add repository integration coverage for every sprint root failure class.
- [ ] **S1-TEST-009** Add repository integration coverage for every managed submodule failure class.
- [ ] **S1-TEST-010** Add mutation-invariant assertions to invalid preflight tests.
- [ ] **S1-TEST-011** Add concurrent ownership coverage using separate processes, not only threads.
- [ ] **S1-TEST-012** Add stale and malformed metadata plus process-identity coverage.
- [ ] **S1-TEST-013** Add CLI tests for help, version, usage errors, and deferred controls.
- [ ] **S1-TEST-013A** Prove each deferred control command leaves state, events, lock metadata, worktrees, indexes, branches, and HEADs unchanged.
- [ ] **S1-TEST-014** Add CLI tests for no-run human and JSON status.
- [ ] **S1-TEST-015** Add CLI tests for placeholder-run human and JSON status.
- [ ] **S1-TEST-016** Add CLI tests proving JSON standard output contains no diagnostics.
- [ ] **S1-TEST-017** Add CLI tests proving invalid runs create no runtime paths.
- [ ] **S1-TEST-018** Add a complete valid-run test asserting all three events and final state.
- [ ] **S1-TEST-018A** Add a test proving any existing persisted run is rejected before worktree cleanliness and remains unchanged.
- [ ] **S1-TEST-018B** Add a two-process race test proving post-lock revalidation prevents a second run from writing.
- [ ] **S1-TEST-018C** Add a concurrent status test proving the persistence lock prevents transient event/state inconsistency during a normal transition.
- [ ] **S1-TEST-018C1** Add a concurrent no-run/first-transition status test proving status observes either a complete no-run view or a complete initialized-run view.
- [ ] **S1-TEST-018D** Add event short-write and partial-final-line tests with no automatic truncation.
- [ ] **S1-TEST-018E** Test that the opaque server URL is neither parsed, logged, nor persisted.
- [ ] **S1-TEST-018F** Test process activity while ownership is held and after release, including final persisted inactive intent.
- [ ] **S1-TEST-018G** Test the 1 MiB bounds for configuration, state, and individual event lines.
- [ ] **S1-TEST-019** Run the full default suite in an environment without OpenCode or GitHub credentials.
- [ ] **S1-TEST-020** Run configured formatting, linting, and type checks.
- [ ] **S1-TEST-021** Build the source distribution and wheel successfully.
- [ ] **S1-TEST-022** Install the built wheel in a clean environment and run help, version, and status smoke tests.

## 21. Documentation

- [ ] **S1-DOC-001** Document the controller's Sprint 1 purpose and limitations in the root README.
- [ ] **S1-DOC-002** Document Python version and installation instructions.
- [ ] **S1-DOC-003** Document development environment and verification commands.
- [ ] **S1-DOC-004** Document every Sprint 1 CLI command and argument.
- [ ] **S1-DOC-005** Clearly label pause, resume, and stop as reserved but non-functional in Sprint 1.
- [ ] **S1-DOC-006** Clearly state that `run` requires but does not contact `--server-url` in Sprint 1.
- [ ] **S1-DOC-006A** State that Sprint 1 treats the server URL as opaque and neither logs nor persists it.
- [ ] **S1-DOC-007** Document the required sprint repository layout.
- [ ] **S1-DOC-008** Provide a complete valid configuration example.
- [ ] **S1-DOC-009** Document configuration field validation and exactly-one-repository restriction.
- [ ] **S1-DOC-010** Document the clean repository and initialized submodule requirement.
- [ ] **S1-DOC-011** Document state, events, and the placeholder blocked result.
- [ ] **S1-DOC-011A** Document that uncommitted controller-owned runtime files are expected after the Sprint 1 placeholder run because checkpoint commits begin in Sprint 4.
- [ ] **S1-DOC-012** Document human and JSON status usage.
- [ ] **S1-DOC-013** Keep examples aligned with tested command behavior.
- [ ] **S1-DOC-014** Do not present OpenCode sessions, commits, CI, Neovim commands, or recovery as implemented.

## 22. Security Review

- [ ] **S1-SEC-001** Confirm no credential or token fields exist in configuration, state, events, lock metadata, fixtures, or examples.
- [ ] **S1-SEC-002** Confirm the complete opaque server URL is not parsed, persisted, or logged in Sprint 1 and documentation warns against embedding credentials.
- [ ] **S1-SEC-003** Confirm subprocesses receive argument arrays and no user-controlled shell string.
- [ ] **S1-SEC-004** Confirm persisted errors do not include the complete process environment.
- [ ] **S1-SEC-005** Confirm configuration, state, and individual event-line reads enforce the documented 1 MiB bounds.
- [ ] **S1-SEC-006** Confirm temporary state permissions do not broaden state visibility.
- [ ] **S1-SEC-007** Search committed fixtures and documentation for accidental real credentials before completion.

## 23. Scope Review

- [ ] **S1-REVIEW-001** Confirm no OpenCode HTTP client or server discovery was added.
- [ ] **S1-REVIEW-002** Confirm no agent runner, prompt, transcript, or structured agent-result implementation was added.
- [ ] **S1-REVIEW-003** Confirm no Git mutation API was added to the controller.
- [ ] **S1-REVIEW-004** Confirm no GitHub or CI integration was added.
- [ ] **S1-REVIEW-005** Confirm no Lua plugin code or parent submodule pointer change was required.
- [ ] **S1-REVIEW-006** Confirm no multi-repository runtime behavior or speculative provider abstraction was added.
- [ ] **S1-REVIEW-007** Confirm deferred functionality fails clearly rather than silently succeeding.
- [ ] **S1-REVIEW-008** Compare implemented public contracts with the V1 specification and update documentation deliberately for any approved difference.

## 24. Exit Demonstration

- [ ] **S1-DEMO-001** Install the built package in a clean Python 3.11+ environment.
- [ ] **S1-DEMO-002** Show `sprint-loop --help` and `sprint-loop --version`.
- [ ] **S1-DEMO-003** Show human and JSON status against a clean no-run sprint fixture and verify no worktree or runtime files were created; allow only the local persistence lock under Git metadata.
- [ ] **S1-DEMO-004** Show the fixture's real initialized managed Git submodule and clean statuses.
- [ ] **S1-DEMO-005** Run `sprint-loop run` with a synthetic server URL.
- [ ] **S1-DEMO-006** Show the intentional non-zero placeholder result without a traceback.
- [ ] **S1-DEMO-007** Inspect state and verify `blocked/execution_not_implemented`.
- [ ] **S1-DEMO-008** Inspect exactly ordered `run.started`, validating `state.entered`, and `run.blocked` events.
- [ ] **S1-DEMO-009** Show human and JSON status after process exit.
- [ ] **S1-DEMO-010** Demonstrate concurrent OS lock rejection with status still readable.
- [ ] **S1-DEMO-011** Make the managed repository dirty and show a no-mutation preflight failure.
- [ ] **S1-DEMO-012** Use an unknown configuration schema and show a no-mutation failure.
- [ ] **S1-DEMO-013** Show that no OpenCode server or GitHub credential was needed.

## 25. Completion Gate

- [ ] **S1-DONE-001** Every applicable checklist item above is checked.
- [ ] **S1-DONE-002** All Sprint 1 acceptance criteria in `sprint_spec.md` are demonstrably satisfied.
- [ ] **S1-DONE-003** The narrow test suites pass during development.
- [ ] **S1-DONE-004** The complete default test suite passes.
- [ ] **S1-DONE-005** Formatting, linting, type checking, package build, and clean-install smoke tests pass.
- [ ] **S1-DONE-006** `git diff --check` passes.
- [ ] **S1-DONE-007** Final repository status contains only intended Sprint 1 changes.
- [ ] **S1-DONE-008** No credentials, generated runtime state, build artifacts, or temporary repositories are tracked.
- [ ] **S1-DONE-009** Documentation describes actual Sprint 1 behavior and does not claim deferred features.
- [ ] **S1-DONE-010** The exit demonstration has been performed and its commands are reproducible from documentation.
