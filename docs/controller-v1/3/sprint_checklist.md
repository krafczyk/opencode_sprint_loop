# Controller V1 Sprint 3 Checklist: Neovim Client V1

## Usage

This checklist tracks implementation of `docs/controller-v1/3/sprint_spec.md`. It does not replace the sprint specification or authoritative V1 documents.

An item may be checked only when its implementation, tests, and required documentation are complete. If implementation changes a durable schema, CLI contract, plugin public API, Git ownership rule, terminal-state rule, external compatibility policy, or user-interaction behavior, update the governing specification before checking the item.

## 1. Scope and Inputs

- [x] **S3-SCOPE-001** Read the nearest `AGENTS.md`, both authoritative V1 documents, `docs/threat_model.md`, `docs/audit_policy.md`, and the complete Sprint 3 specification before implementation.
- [x] **S3-SCOPE-002** Confirm Sprint 2 remains complete and Sprint 3 is the active implementation sprint.
- [x] **S3-SCOPE-003** Inspect status in both the controller and plugin repositories and preserve unrelated work.
- [x] **S3-SCOPE-004** Keep Builder execution, durable question monitoring, Git handoff, audit rounds, publication, CI, functional controls, and recovery out of Sprint 3.
- [x] **S3-SCOPE-005** Preserve the controller as workflow authority and the plugin as a launcher, status observer, and presentation client only.
- [x] **S3-SCOPE-006** Apply the trusted-user prototype threat model without expanding Sprint 3 for excluded hostile local races.
- [x] **S3-SCOPE-007** Treat waiting-for-user status as a presentation compatibility contract; do not claim a real Sprint 3 agent can ask a question.
- [x] **S3-SCOPE-008** Record every accepted limitation and later-sprint dependency without presenting it as implemented behavior.

## 2. Live mkchad Safety

- [x] **S3-MKCHAD-001** Treat `~/.config/mkchad` as a live user environment and do not edit any file beneath it.
- [x] **S3-MKCHAD-002** Record live checkout status before integration work without staging, cleaning, or modifying its untracked files.
- [x] **S3-MKCHAD-003** Clone the current remote `mkchad` branch into a disposable directory outside the live configuration.
- [x] **S3-MKCHAD-004** Record the remote clone commit used for integration evidence and refresh the clone if the remote branch changes.
- [x] **S3-MKCHAD-005** Use isolated `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME` for every test that sources mkchad code.
- [x] **S3-MKCHAD-006** Use only mkchad's explicit test seams and disposable state; never discover or reuse the live managed server, TLS files, logs, locks, or credentials.
- [x] **S3-MKCHAD-007** Verify integration with callback-style `vim.g.opencode_opts.server.url(callback)` from the disposable clone.
- [ ] **S3-MKCHAD-008** Verify private-CA integration with the disposable clone's `vim.g.opencode_opts.server.ca_cert()` accessor.
- [x] **S3-MKCHAD-009** Prove the Sprint Loop plugin never calls mkchad's `server.ensure` or another server-start operation.
- [x] **S3-MKCHAD-010** Recheck live checkout Git status after testing and confirm it is unchanged, without inspecting live runtime state.

## 3. Repository and Submodule Workflow

- [x] **S3-REPO-001** Implement plugin code in the `opencode_sprint_loop.lua` repository, not as Python-embedded Lua.
- [x] **S3-REPO-002** Keep controller changes limited to additive status projection, tests, documentation, and the eventual plugin gitlink update.
- [x] **S3-REPO-003** Use a conventional Lua plugin layout with clear configuration, process, status, and UI boundaries.
- [x] **S3-REPO-004** Add no generated test output, status document, runtime state, browser artifact, credential, or transcript to either repository.
- [x] **S3-REPO-005** Commit and push verified plugin changes from inside the plugin repository before updating the parent gitlink.
- [x] **S3-REPO-006** Update the parent gitlink in a separate parent commit without mixing unrelated controller or plugin work.
- [x] **S3-REPO-007** Inspect final status and diff in both repositories before Sprint 3 completion.

## 4. Runtime and Architecture

- [x] **S3-ARCH-001** Require Neovim 0.12 and reject older versions before process or buffer mutation.
- [x] **S3-ARCH-002** Keep the plugin free of required runtime dependencies beyond Neovim and the configured controller executable.
- [x] **S3-ARCH-003** Keep process execution independent from status decoding and UI rendering.
- [x] **S3-ARCH-004** Keep status validation independent from Neovim window layout where practical.
- [x] **S3-ARCH-005** Keep internal process, timer, notifier, browser, and clock seams narrow enough for deterministic tests.
- [x] **S3-ARCH-006** Make no direct Git, GitHub, OpenCode question, controller state-file, event-log, invocation-record, or lock-file call from the plugin.
- [x] **S3-ARCH-007** Keep every user-triggered process and status action asynchronous on Neovim's main interaction path.
- [x] **S3-ARCH-008** Document public functions and non-obvious lifecycle boundaries with concise Lua annotations or help text.

## 5. Setup Contract

- [x] **S3-SETUP-001** Require a successful `setup()` before every public action and command.
- [x] **S3-SETUP-002** Require explicit `sprint_root` and `server_url` setup fields.
- [x] **S3-SETUP-003** Default `executable` to exact string `sprint-loop`.
- [x] **S3-SETUP-004** Support optional `web_url` and `server_ca_cert` fields.
- [x] **S3-SETUP-005** Accept strings and synchronous-return functions for root/executable/CA, and strings or synchronous/callback functions for server/web URLs.
- [x] **S3-SETUP-006** Reject unknown option names and unsupported primitive or collection values.
- [x] **S3-SETUP-007** Validate setup shape without prematurely resolving callbacks or contacting external systems.
- [x] **S3-SETUP-008** Make repeated setup replace configuration and invalidate prior watcher/timer callbacks without duplicate commands.
- [x] **S3-SETUP-009** Perform one initial asynchronous status observation after setup for the newly resolved root.
- [x] **S3-SETUP-010** Fail actions before setup with actionable `setup_required` behavior and no process, timer, browser, or buffer mutation.

## 6. Resolver Semantics

- [x] **S3-RESOLVE-001** Resolve only values needed by the current action.
- [x] **S3-RESOLVE-002** Resolve action values at invocation time so roots and server URLs may change after setup.
- [x] **S3-RESOLVE-003** Support synchronous-return functions for every option and callback style only for server/web URLs.
- [x] **S3-RESOLVE-004** Support callback-style URL resolvers that call `done(value, error)` exactly once.
- [x] **S3-RESOLVE-005** Enforce the documented five-second callback resolver timeout or update the specification before choosing another bound.
- [x] **S3-RESOLVE-006** Reject URL dual return-and-callback completion and duplicate callbacks, and reject callback misuse for non-URL options without side effects.
- [x] **S3-RESOLVE-007** Ignore late resolver completions from a replaced setup or watcher generation.
- [x] **S3-RESOLVE-008** Catch resolver exceptions and return concise errors without ordinary Lua tracebacks.
- [x] **S3-RESOLVE-009** Reject nil, empty, non-string, NUL-bearing, and control-character resolved values.
- [x] **S3-RESOLVE-010** Bind one watcher generation to the root resolved when that generation starts.
- [x] **S3-RESOLVE-011** Never resolve or display `server_url` for status-only progress when it is not needed.
- [x] **S3-RESOLVE-012** Never call a server launcher, ensure callback, registry, or discovery fallback while resolving URLs.

## 7. Public Lua API and Commands

- [x] **S3-API-001** Export `setup(options)` from `require("opencode_sprint_loop")`.
- [x] **S3-API-002** Export asynchronous `start()`.
- [x] **S3-API-003** Export asynchronous `progress()`.
- [x] **S3-API-004** Export asynchronous `pause()`.
- [x] **S3-API-005** Export asynchronous `resume()`.
- [x] **S3-API-006** Export asynchronous `stop()`.
- [x] **S3-API-007** Export asynchronous `open_session()`.
- [x] **S3-API-008** Avoid a stable process-handle, status-object, or workflow-result return contract from action methods.
- [x] **S3-CMD-001** Register `SprintLoopStart` exactly once.
- [x] **S3-CMD-002** Register `SprintLoopProgress` exactly once.
- [x] **S3-CMD-003** Register `SprintLoopPause` exactly once.
- [x] **S3-CMD-004** Register `SprintLoopResume` exactly once.
- [x] **S3-CMD-005** Register `SprintLoopStop` exactly once.
- [x] **S3-CMD-006** Register `SprintLoopOpenSession` exactly once.
- [x] **S3-CMD-007** Give commands no V1 arguments and route each command through the corresponding public Lua method.
- [x] **S3-CMD-008** Add no global key mappings.

## 8. CLI Argument Construction

- [x] **S3-ARGV-001** Construct start argv as `<executable> run --root <root> --server-url <url>`.
- [x] **S3-ARGV-002** Construct progress/watcher argv as `<executable> status --root <root> --json`.
- [x] **S3-ARGV-003** Construct pause argv as `<executable> pause --root <root>`.
- [x] **S3-ARGV-004** Construct resume argv as `<executable> resume --root <root> --server-url <url>`.
- [x] **S3-ARGV-005** Construct stop argv as `<executable> stop --root <root>`.
- [x] **S3-ARGV-006** Pass argv arrays directly to Neovim/libuv and never invoke `sh -c` or another shell.
- [x] **S3-ARGV-007** Preserve spaces, quotes, wildcard characters, substitutions, and separators as literal argument content.
- [x] **S3-ARGV-008** Keep server credentials out of argv and inherit supported authentication from the child environment.
- [x] **S3-ARGV-009** Do not preflight duplicate runs, repository state, or workflow transitions in Lua.

## 9. Private CA Child Environment

- [x] **S3-CA-001** Resolve `server_ca_cert` only for controller actions that may contact OpenCode.
- [x] **S3-CA-002** Require the resolved CA path to be absolute, readable, regular, and free of NUL/control characters.
- [x] **S3-CA-003** Set `SSL_CERT_FILE` only in the spawned controller child environment when the option is configured.
- [x] **S3-CA-004** Preserve the inherited environment unchanged when no CA option is configured.
- [x] **S3-CA-005** Keep the CA path out of argv, progress buffers, routine notifications, captured fixtures, and documentation examples.
- [x] **S3-CA-006** Do not copy CA contents, TLS keys, OpenCode passwords, or complete child environments.
- [x] **S3-CA-007** Document that browser trust is configured separately by the operator and is not changed by the plugin.

## 10. Asynchronous Process Handling

- [x] **S3-PROC-001** Return control to Neovim immediately after each successful process spawn.
- [x] **S3-PROC-002** Capture standard output and standard error through bounded handlers.
- [x] **S3-PROC-003** Report missing executable and spawn errors clearly.
- [x] **S3-PROC-004** Report non-zero controller exits with bounded diagnostics but do not infer a workflow transition from prose.
- [x] **S3-PROC-005** Avoid blocking `wait()` or synchronous system calls on interactive command paths.
- [x] **S3-PROC-006** Prevent stale callbacks from a replaced setup generation from opening buffers or notifying current state.
- [x] **S3-PROC-007** Clean up plugin-owned process pipes and handles without signalling detached controller ownership incorrectly.
- [x] **S3-PROC-008** Bound retained output and discard it after the result is rendered.

## 11. Detached Controller Lifetime

- [x] **S3-DETACH-001** Spawn `sprint-loop run` in a detached process group or equivalent Neovim 0.12 mode.
- [x] **S3-DETACH-002** Notify successful spawn without claiming the controller is durably running.
- [x] **S3-DETACH-003** Confirm process activity only through later controller status.
- [x] **S3-DETACH-004** Ensure `VimLeave` and plugin teardown do not send a termination signal to the controller.
- [x] **S3-DETACH-005** Add a process-level test proving a child survives the launching headless Neovim process.
- [ ] **S3-DETACH-006** Prove closing Neovim during the real Sprint 2 probe does not terminate the controller.

## 12. Additive Controller Status Projection

- [x] **S3-STATUS-001** Preserve every stable Sprint 1 and Sprint 2 JSON status field and meaning.
- [x] **S3-STATUS-002** Keep no-run `active` exactly null.
- [x] **S3-STATUS-003** Add null `active.status` and `active.interaction` for an inactive persisted run.
- [x] **S3-STATUS-004** Project active Sprint 2 invocation status as exact `running`.
- [x] **S3-STATUS-005** Project active Sprint 2 interaction as null.
- [x] **S3-STATUS-006** Keep status schema version `1` and document the fields as backward-compatible additions.
- [x] **S3-STATUS-007** Update human status only as needed to remain accurate and credential-free.
- [x] **S3-STATUS-008** Make no state-schema, event-schema, invocation-record, Git, or OpenCode lifecycle change for the additive fields.
- [x] **S3-STATUS-009** Add controller tests for no-run, inactive, and active projections.
- [x] **S3-STATUS-010** Keep status read-only, local, and independent of server availability.

## 13. Plugin Status Validation

- [x] **S3-JSON-001** Require exactly one bounded JSON object from `status --json` standard output.
- [x] **S3-JSON-002** Require integer schema version `1` without accepting booleans.
- [x] **S3-JSON-003** Require a non-empty controller version and every stable top-level field.
- [x] **S3-JSON-004** Validate no-run nullability and process-running invariants.
- [x] **S3-JSON-005** Validate inactive active-object nullability including status and interaction.
- [x] **S3-JSON-006** Validate active `running` status with null interaction.
- [x] **S3-JSON-007** Validate future-compatible `waiting_for_user` with exact interaction fields.
- [x] **S3-JSON-008** Require positive integer question count without accepting booleans.
- [x] **S3-JSON-009** Validate bounded request ID and displayable asked-at string.
- [x] **S3-JSON-010** Reject contradictory active status, session, and interaction combinations.
- [x] **S3-JSON-011** Reject duplicate keys, trailing values, non-finite values, invalid UTF-8, empty output, and oversized output.
- [x] **S3-JSON-012** Ignore unknown additional object fields for forward compatibility.
- [x] **S3-JSON-013** Never read `state.json`, events, invocation artifacts, Git, or OpenCode directly to repair malformed status.

## 14. Progress Floating Window

- [x] **S3-UI-001** Open progress in a centered floating window backed by a plugin-owned scratch buffer.
- [x] **S3-UI-002** Render sprint root, sprint identity, workflow state, and process-running status.
- [x] **S3-UI-003** Render reason code/message prominently for blocked and failed states.
- [x] **S3-UI-004** Render active role, invocation ID, session ID, status, and interaction summary.
- [x] **S3-UI-005** Render local and pushed commit maps in deterministic repository order.
- [x] **S3-UI-006** Render audit, CI, counters, checklist, last event, controller version, and update time.
- [x] **S3-UI-007** Render a clear no-run view.
- [x] **S3-UI-008** Use `buftype=nofile`, disable swap, and wipe on close.
- [x] **S3-UI-009** Make the completed buffer non-modifiable.
- [x] **S3-UI-010** Add buffer-local `q` and `Esc` close mappings only.
- [x] **S3-UI-011** Adapt dimensions to small and large editor windows.
- [x] **S3-UI-012** Reuse or replace the prior plugin view without leaking buffers/windows.
- [x] **S3-UI-013** Keep server URLs, CA paths, credentials, prompts, result summaries, transcripts, and question/answer text out of the buffer.

## 15. Background Status Watcher

- [x] **S3-WATCH-001** Maintain at most one watcher generation.
- [x] **S3-WATCH-002** Perform one setup-time status query and watch when it discovers an active controller.
- [x] **S3-WATCH-003** Start discovery after successful start and resume spawns.
- [x] **S3-WATCH-004** Preserve discovery while the launched process is alive even before durable state becomes visible.
- [x] **S3-WATCH-004A** Perform one final status query and stop when the launched command exits before an active run is observed.
- [x] **S3-WATCH-005** Use one documented fixed non-busy polling interval.
- [x] **S3-WATCH-006** Allow at most one status process in flight and never catch up with overlapping polls.
- [x] **S3-WATCH-007** Stop after an observed active controller becomes inactive.
- [x] **S3-WATCH-008** Cancel timers and invalidate callbacks on repeated setup and Neovim exit.
- [x] **S3-WATCH-009** Deduplicate waiting notifications by request ID within one Neovim process.
- [x] **S3-WATCH-010** Notify exactly once for repeated observations of one request.
- [x] **S3-WATCH-011** Notify again for a distinct request and once after a new Neovim process discovers an existing request.
- [x] **S3-WATCH-012** Include safe active role/invocation context and direct the user to `SprintLoopOpenSession`.
- [x] **S3-WATCH-013** Keep question text, options, answers, URLs, CA paths, and credentials out of notifications.
- [x] **S3-WATCH-014** Emit at most one warning per continuous watcher failure episode and reset suppression after success.
- [x] **S3-WATCH-015** Treat malformed status as an error, not no-run, stopped, or success.
- [x] **S3-WATCH-016** Make no direct OpenCode, question reply/reject, workflow mutation, or persistence call.
- [x] **S3-WATCH-017** Preserve serialized public progress/session reads across start, resume, and stop observation overlap; preserve stop observation until status confirms inactivity.

## 16. Active Session Browser URL

- [x] **S3-WEB-001** Query current status asynchronously before opening a session.
- [x] **S3-WEB-002** Require a persisted run and non-empty active session ID.
- [x] **S3-WEB-003** Resolve `web_url` only after an active session is known.
- [x] **S3-WEB-004** Accept a credential-free absolute HTTP or HTTPS web base, including a supported path prefix.
- [x] **S3-WEB-005** Reject user-info, query, fragment, empty host, control character, and unsupported scheme.
- [x] **S3-WEB-006** Encode canonical status `sprint_root` as RFC 4648 URL-safe base64 without padding.
- [x] **S3-WEB-007** Percent-encode session ID as one path segment.
- [x] **S3-WEB-008** Join trailing slash and path-prefix cases deterministically.
- [x] **S3-WEB-009** Open through Neovim 0.12's browser API without invoking a shell command.
- [x] **S3-WEB-010** Report missing web URL, missing active session, invalid base, and browser failure actionably.
- [x] **S3-WEB-011** Do not put Basic-auth credentials or CA contents in the browser URL.
- [x] **S3-WEB-012** Document manual browser trust for mkchad's private CA.

## 17. Session Title Convention

- [x] **S3-TITLE-001** Enforce/document `[<multisprint>/<sprint>] <role> <sequence> <purpose>` as the normative controller title format.
- [x] **S3-TITLE-002** Require sequence padding to at least four decimal digits.
- [x] **S3-TITLE-003** Use canonical workflow role and concise phase purpose.
- [x] **S3-TITLE-004** Keep titles descriptive only and never use them as identity evidence.
- [x] **S3-TITLE-005** Verify the Sprint 2 probe title matches `[<multisprint>/<sprint>] auditor 0001 execution probe`.
- [x] **S3-TITLE-006** Add no extra Sprint 3 session and do not rename an existing remote session.

## 18. Control Delegation

- [x] **S3-CTRL-001** Delegate pause to the controller and display its current response.
- [x] **S3-CTRL-002** Delegate resume with a newly resolved server URL and optional CA child environment.
- [x] **S3-CTRL-003** Delegate stop to the controller and display its current response.
- [x] **S3-CTRL-004** Preserve Sprint 2 `feature_not_implemented` behavior without simulated status changes.
- [x] **S3-CTRL-005** Do not kill, abort, retry, or mutate the controller in response to a control-command failure.
- [x] **S3-CTRL-006** Keep the real question lifecycle assigned to Sprint 4 and pause/resume/stop behavior at a waiting-for-user boundary assigned to Sprint 7.
- [x] **S3-CTRL-007** Keep the current watcher active across stop resolver/spawn/command failure and success while observed status remains active.

## 19. Errors, Bounds, and Security

- [x] **S3-ERR-001** Implement and document the Sprint 3 plugin error categories or tested equivalents.
- [x] **S3-ERR-002** Use standard Neovim notification levels consistently.
- [x] **S3-ERR-003** Keep expected setup, resolver, process, status, CA, and browser failures free of ordinary tracebacks.
- [x] **S3-ERR-004** Bound status stdout, controller stdout/stderr, resolver errors, and displayed strings.
- [x] **S3-ERR-005** Prefer bounded controller stderr without parsing it into workflow decisions.
- [x] **S3-ERR-006** Never echo a rejected credential-bearing URL verbatim.
- [x] **S3-SEC-001** Use argv arrays for every process.
- [x] **S3-SEC-002** Keep credentials, complete environments, CA contents, and private runtime paths out of persistence and committed fixtures.
- [x] **S3-SEC-003** Pass a configured CA path only through the child environment.
- [x] **S3-SEC-004** Treat all status values as display text and never execute them as commands, mappings, format strings, or help tags.
- [x] **S3-SEC-005** Use synthetic security-sensitive values in tests and examples.
- [x] **S3-SEC-006** Confirm the plugin owns no persistent file and never writes controller workflow data.
- [x] **S3-SEC-007** Keep controller and plugin recognizers semantically identical through an explicit ASCII credential grammar and exact positive/near-miss parity vectors.

## 20. Automated Verification

- [x] **S3-TEST-001** Add a documented headless Neovim 0.12 test command.
- [x] **S3-TEST-002** Keep default plugin tests independent of OpenCode, GitHub, network, model usage, browser availability, and credentials.
- [x] **S3-TEST-003** Test command registration and every public Lua method.
- [x] **S3-TEST-004** Test setup shape, independently missing root/server, per-option resolver contracts, malformed results, timeout, duplicate completion, and stale completion.
- [x] **S3-TEST-005** Test exact argv and no shell interpolation with hostile-looking literal values.
- [x] **S3-TEST-006** Test asynchronous behavior and bounded output using a fake executable.
- [x] **S3-TEST-007** Test process-level detached survival after headless Neovim exits.
- [x] **S3-TEST-008** Test every supported status state and malformed status category.
- [x] **S3-TEST-009** Test progress buffer content, options, mappings, dimensions, and lifecycle.
- [x] **S3-TEST-010** Test watcher activation, one-in-flight rule, deduplication, failure suppression, targeted replacement, public-action overlap, stop preservation, and shutdown.
- [x] **S3-TEST-011** Test URL-safe root encoding, session encoding, web-base validation, and browser outcomes.
- [x] **S3-TEST-012** Test CA path validation and exact child environment without capturing CA content.
- [x] **S3-TEST-013** Test accurate pause/resume/stop error delegation.
- [x] **S3-TEST-014** Add focused controller tests for additive status fields and unchanged state/event behavior.
- [x] **S3-TEST-015** Keep all existing controller tests green after intentional status snapshot updates.
- [ ] **S3-TEST-016** Run selected Lua formatting/linting checks documented by the plugin repository.
- [x] **S3-TEST-017** Run Python formatting, linting, strict typing, compilation, build, and clean-install checks required by the controller repository.
- [x] **S3-TEST-018** Run `git diff --check` in both repositories.
- [x] **S3-TEST-019** Test the Neovim minimum-version gate with a controlled older-version fixture without requiring an installed older Neovim.

## 21. Documentation

- [x] **S3-DOC-001** Expand the plugin README with installation, Neovim 0.12, setup, and command usage.
- [x] **S3-DOC-002** Add a Neovim help file documenting every public option, method, command, and error path.
- [x] **S3-DOC-003** Document required setup and explicit root/server configuration.
- [x] **S3-DOC-004** Document synchronous-only non-URL functions, synchronous/callback URL functions, setup-time initial root/executable resolution, and action-time re-resolution.
- [x] **S3-DOC-005** Provide generic and current mkchad adapter examples without hard-coding mkchad in plugin source.
- [x] **S3-DOC-006** Document that URL resolution must not call mkchad server ensure/start behavior.
- [x] **S3-DOC-007** Document optional `server_ca_cert`, child `SSL_CERT_FILE`, and separate browser CA trust.
- [x] **S3-DOC-008** Document detached launch and the distinction between spawn success and confirmed controller activity.
- [x] **S3-DOC-009** Document progress fields, close mappings, and no-run presentation.
- [x] **S3-DOC-010** Document watcher lifetime, deduplicated question notification, and lack of plugin question answering.
- [x] **S3-DOC-011** Document active-session URL construction and missing-session/browser failures.
- [x] **S3-DOC-012** Document current `feature_not_implemented` pause/resume/stop behavior.
- [x] **S3-DOC-013** Document default fake/headless tests and opt-in real OpenCode demonstration.
- [x] **S3-DOC-014** Warn that `~/.config/mkchad` is live and all development integration uses a disposable remote clone plus isolated XDG roots.
- [x] **S3-DOC-015** Update the parent README to identify Sprint 3 and its exact implemented limitations.
- [x] **S3-DOC-016** Keep Builder, real pending-question monitoring, commits, audit, CI, and recovery clearly unimplemented.

## 22. Threat and Security Review

### Repair-round evidence (2026-07-15)

The plugin repair commit `35bbb4f` was committed and pushed after the baseline
plugin commit `aae1d48` and before this parent gitlink update. The pushed parent
baseline was `8e67c1f`, whose gitlink referenced `aae1d48`. The complete offline
plugin command passed with `139` assertions, including the repository
fake executable and process-level detached-survival case. Focused controller
status tests passed (`2`), followed by the complete controller suite (`210`
passing, one opt-in real-server test skipped), compilation, Ruff lint and format
checks, strict mypy, build, and a disposable clean-wheel-install smoke.
`git diff --check` passed in both repositories. No Lua formatter or linter is
configured or installed in the plugin repository, so S3-TEST-016 remains
unchecked rather than fabricating evidence.

The live mkchad checkout was clean at `938c325` before and after integration
(the empty porcelain status hash remained
`91558ac42fbea3c027279059361fd48a390d28c557a3ae6bb5d0c76cc768e1e4`). A fresh
disposable clone of the current remote `mkchad` branch was also `938c325`.
`tests/mkchad_adapter.lua` sourced only that clone with isolated XDG config,
state, data, and cache roots; it exercised the actual callback-style URL
accessor and proved the Sprint Loop adapter did not call `server.ensure`. The
isolated clone had no managed-server state or CA path, so a non-null current
mkchad CA accessor demonstration remains unchecked. No live server, TLS state,
credentials, browser, provider, model, or real OpenCode execution probe was
discovered or reused.

### Repair-round-2 evidence (2026-07-15)

Auditor pass-2 findings 1 through 9 were repaired in pushed plugin commit
`4c9361b`, before this parent gitlink update, without accessing live mkchad state
or closing an external demonstration gate. Controller coverage now proves that an interrupted
durable active invocation projects `running` while `process_running` is false.
The plugin accepts that truthful combination, enforces state-conditional reason
semantics, arbitrates URL function resolvers for the complete five-second window,
rejects malformed web path prefixes, reports external command failures without
stderr disclosure, distinguishes truncated status output, and requires a
readable regular-file CA.

The repository fake and production `vim.system` adapter drive status success,
malformed output, non-zero credential/control-bearing stderr, oversized output,
delayed watcher activation/shutdown, interrupted-active session retrieval, and
real child observation of `SSL_CERT_FILE` without printing its path or content.
The complete plugin command passed with `184` assertions. The focused
interrupted-active controller test passed, followed by the complete controller
suite (`210` passing, one opt-in real-server test skipped), compilation, Ruff
lint and format checks, strict mypy, build, and a disposable clean-wheel-install
smoke. `git diff --check` passed in both repositories. No Lua formatter or
linter is configured, so S3-TEST-016 remains unchecked. The documented
safety-bounded real procedure keeps S3-DOC-013 checked, but S3-DETACH-006,
S3-MKCHAD-008, S3-DEMO-002 through S3-DEMO-010, S3-DONE-010, and the independent
audit gates remain unchecked.

### Repair-round-3 evidence (2026-07-15)

Auditor pass-3 numbered findings 1 through 7 were repaired in pushed plugin
commit `cb54112`, before this parent gitlink update, without accessing live
mkchad state or closing an external demonstration gate. Status validation now rejects recognizable
credentials in every rendered field and enforces the requested persisted V1
shape constraints while retaining truthful interrupted active invocation
evidence. Setup
notifies from its validated first observation before watcher startup. Non-zero
signals fail generically. Resolver timers are cancellable and lifecycle-owned,
and stale watcher resolution cannot spawn. Detached survival uses and removes a
unique test-owned directory. A separate controller assertion fixes the literal
production title expectation.

The complete plugin command passed with `239` assertions. The focused literal
controller test passed (`1`), followed by the isolated complete controller suite
(`211` tests, one opt-in real-server test skipped), compilation, Ruff lint and
format checks, strict mypy, build, and a fresh disposable wheel-install
help/version smoke. An earlier concurrent controller-suite/build invocation had
one copy error while `build` temporarily created its sdist staging directory;
the required suite was rerun without a concurrent source-tree build and passed.
`git diff --check` passed in both repositories, and final status showed only the
intended repair files plus the modified plugin submodule worktree. No Lua
formatter or linter is configured or installed, so S3-TEST-016 remains
unchecked. S3-MKCHAD-008, S3-DETACH-006, S3-DEMO-002 through S3-DEMO-010,
S3-DONE-010, S3-REVIEW-011, and S3-DONE-011 remain unchecked.

### Repair-round-4 evidence (2026-07-15)

Auditor pass-4 findings 1 through 4 were repaired in pushed plugin commit
`82bcde8`, before this parent gitlink update.
Unknown setup fields now produce only fixed `invalid_setup` without retaining or
displaying their names, with public-path tests for synthetic credential,
credential-bearing URL, control-bearing, and oversized keys. All plugin status
queries gained one serialized child slot. The round-4 implementation cancelled
retained read-only status children during setup, watcher, start/resume, stop, and
`VimLeavePre` replacement and did not target detached/controller process
handles. Auditor pass 5 later demonstrated that this wording hid two defects:
stop could permanently remove observation after a failed delegation, and
start/resume/stop could silently discard public progress/session requests.
Those round-4 completion claims are withdrawn and replaced by repair-round-5
targeted-cancellation evidence.

The round-4 Lua status recognizer aligned the listed ASCII provider-token
prefixes, suffix alphabets, and minimum lengths with
`src/opencode_sprint_loop/security.py`, but it did not prove semantic parity:
Python Unicode case folding and whitespace still diverged from Lua. Likewise,
the public setup matrix exercised then-accepted generic callback behavior rather
than the authoritative per-option resolver contract. Those broader round-4
claims are withdrawn; repair round 5 supplies explicit ASCII parity vectors and
public-path per-option resolver evidence.

The focused development run passed with `302` assertions; after the final
global public-status serialization assertion, the complete plugin command
passed with `303` assertions. Two focused controller credential-recognizer tests
also passed.
The complete controller suite passed (`211` tests, one opt-in real-server test
skipped), followed by compilation, Ruff lint and format checks, strict mypy,
package build, and a fresh disposable wheel-install help/version smoke.
`git diff --check` passed in the plugin repository before this evidence update;
final checks in both repositories are recorded below. No Lua formatter or
linter is configured or installed, so S3-TEST-016 remains unchecked.
S3-MKCHAD-008, S3-DETACH-006, S3-DEMO-002 through S3-DEMO-010, S3-DONE-010,
S3-REVIEW-011, and S3-DONE-011 remain unchecked; no external mkchad private-CA,
real probe, browser, independent-audit, or overall-completion gate was closed.

### Repair-round-5 evidence (2026-07-15)

Auditor pass-5 findings 1 through 5 were repaired in pushed plugin commit
`ec83f4b`, before this parent gitlink update, without accessing live mkchad state
or closing an external gate. Controller and plugin credential recognition now share explicit
ASCII case folding, whitespace, and token syntax. Matching test vectors cover
authorization/named/URI/private-key forms, every supported provider prefix,
synthetic positives, short/alphabet near misses, NBSP, long-s, and Kelvin-sign
controls. Conventional synthetic credentials still reject.

Stop now leaves active observation intact across root resolution, process spawn,
signal/non-zero completion, and successful-but-still-active outcomes. Start and
resume replace only setup/watcher reads, while serialized public progress and
session requests complete. The per-option resolver implementation now invokes
root/executable/CA functions synchronously without `done`; only URL functions
receive callback support and the five-second duplicate/dual-completion
arbitration. Public tests cover independently missing root, synchronous URL
returns, malformed/nil results, callback misuse without process/environment
effects, and exact absence of a no-CA environment override.

The focused controller credential tests passed (`2`). The final complete plugin
command passed with `444` assertions. The successful isolated full controller
rerun passed `212` tests with one opt-in real-server test skipped; an earlier
120-second attempt timed out during its build/install integration test and is
not claimed as verification. Compilation, Ruff lint and format checks, strict
mypy, package build, and fresh disposable wheel-install help/version smokes
passed. `git diff --check` passed in both repositories; final status contained
only the intended controller/docs/tests and plugin gitlink update. No Lua formatter/linter
configuration exists, so S3-TEST-016 remains unchecked. S3-MKCHAD-008,
S3-DETACH-006, S3-DEMO-002 through S3-DEMO-010, S3-REVIEW-011,
S3-DONE-001, S3-DONE-002, S3-DONE-005, S3-DONE-010, and S3-DONE-011 remain unchecked.

### Repair-round-6 evidence (2026-07-15)

Auditor pass-6 findings PASS6-001 through PASS6-012 were repaired in pushed
plugin commit `b5036ee`, before this parent gitlink update, without accessing
live mkchad state or closing an external gate. Detached `run` now uses an unreferenced luv
process with stdout/stderr connected to `/dev/null`; the process-level child
writes both streams after Neovim exits before recording completion. Complete
resolved server/web URLs use the shared ASCII recognizer before argv/browser use.
Credential parity now covers nested authorization candidates and `?#fragment`.
Final plugin and controller human lines cannot compose split credential fields.

Resolver deliveries remain cancellable through scheduled consumption, every
plugin predicate fails during exit, browser handler completion is observed
asynchronously, CA readability/type checks use asynchronous libuv
open/fstat/close callbacks, and public status tests cover resolver and
start/resume/stop overlap lifecycles independently. No-run renders process and
controller-version evidence, pre-CI rounds cannot exceed their maximum, and
zero `run`/`resume` process exit has a distinct bounded notice that makes no
workflow-terminal claim. PASS6-013 URL-authority compatibility remains P2 with
disposition `defer`, assigned to Sprint 8 hardening; no compatibility widening
was implemented.

Focused controller tests passed (`3`). The final complete plugin command passed
with `534` assertions. The successful isolated controller rerun passed `213`
tests with one opt-in real-server test skipped. An earlier 300-second full-suite
attempt timed out after partial output that included one failure marker but no
final report; it is not claimed as verification.
Compilation, Ruff lint/format checks, strict mypy, package build, and a fresh
disposable wheel-install help/version smoke passed. The first install smoke
completed its checks but hit a zsh read-only bookkeeping variable afterward; a
fresh corrected invocation passed and removed its disposable root.
`git diff --check` passed in both repositories. No synchronous `fs_stat` or
`filereadable` call remains under plugin Lua. No Lua formatter/linter is
configured, so S3-TEST-016 and S3-DONE-005 remain unchecked. S3-MKCHAD-008,
S3-DETACH-006, S3-DEMO-002 through S3-DEMO-010, S3-REVIEW-011, S3-DONE-001,
S3-DONE-002, S3-DONE-010, and S3-DONE-011 remain unchecked.

### Repair-round-7 evidence (2026-07-15)

Auditor pass-7 findings AUD-S3-P7-001 through AUD-S3-P7-005 were repaired in
pushed plugin commit `009791a`, before this parent gitlink update, without
accessing live mkchad state or closing an external gate. The findings
temporarily reversed the prior evidence for S3-ARCH-007, S3-PROC-005,
S3-CA-002, S3-JSON-002, S3-JSON-003, S3-JSON-008, S3-WEB-009,
S3-TEST-003, S3-TEST-008, S3-TEST-011, S3-TEST-012, S3-DOC-001, and
S3-DOC-002. Those checked items were rechecked only after the round-7 public
paths and complete plugin suite passed.

Browser SystemObj completion now uses only bounded `wait(0)` probes, continues
polling when a closing handle has not retained its result yet, and closes its
timer on success, failure, five-second timeout, setup replacement, or Neovim
exit. CA validation asynchronously stats and rejects non-regular paths before
open, then verifies regular descriptor readability and closes it; a real FIFO
fixture reaches bounded `invalid_server_ca_cert` without launching a controller.
Status decoding classifies the schema immediately after lexical/top-level-object
validation, so a reduced future shape reports `unsupported_status_schema` while
boolean and other non-number versions still reject. Public tests now exercise
configured delegation for all six commands, a removed required V1 field,
boolean schema and question count, and session opening for an inactive persisted
run. Manual installation documents concrete help-tag generation, the dangling
help link is removed, and a disposable `:helptags` smoke proves the
`SprintLoop` tag is generated.

The complete plugin command passed with `566` assertions. The complete
controller suite passed `213` tests with one opt-in real-server test skipped.
Compilation, Ruff lint/format checks, strict mypy, package build, and a fresh
disposable wheel-install help/version smoke passed. `git diff --check` passed in
both repositories, and final status contained only the intended plugin repair,
checklist, and parent gitlink changes.
PASS6-013 remains P2 with disposition `defer`, assigned to Sprint 8; no
URL-authority compatibility widening was implemented. The round-7 deferral of
AUD-S3-P7-006 is superseded by the repair-round-8 resolution below.
S3-MKCHAD-008, S3-DETACH-006, S3-DEMO-002 through S3-DEMO-010,
S3-TEST-016, S3-REVIEW-011, S3-DONE-001, S3-DONE-002, S3-DONE-005,
S3-DONE-010, and S3-DONE-011 remain unchecked. No external OpenCode,
private-CA mkchad, browser, Lua-tooling decision, independent-audit, or
aggregate-completion gate was claimed.

### Repair-round-8 evidence (2026-07-15)

Auditor pass-8 findings AUD-S3-P8-001 through AUD-S3-P8-005 were selected as
P1/`fix_now` and repaired in pushed plugin commit `f9ab9ee`, before this parent gitlink update. They temporarily
reversed the prior evidence for S3-ARCH-007, S3-PROC-001, S3-PROC-005 through
S3-PROC-007, S3-DETACH-001 through S3-DETACH-005, S3-WEB-009,
S3-WEB-010, S3-TEST-007, S3-TEST-008, S3-TEST-011, S3-DOC-001, and
S3-DOC-002. Those checked items were rechecked only after the focused paths and
complete plugin suite passed.

Detached `run` now gates both the pending `/dev/null` open and scheduled spawn
against the originating setup generation and `VimLeavePre`. Replacement closes
the descriptor without a stale spawn, `on_spawn`, watcher mutation, or launch
notice; a successfully spawned controller is not retained as a cancellation
target and is never signalled. Deterministic delayed-`fs_open` tests cover setup
replacement, exit, the queued-spawn boundary, resource closure, and the
post-spawn no-signal invariant. This also resolves stale pre-spawn finding
AUD-S3-P7-006: its prior P2/`defer` record is superseded by the user-selected
`fix_now` repair, with status resolved by AUD-S3-P8-001 and the same tests.

Neovim 0.12.0 and the installed 0.12.4 implementation were reviewed before
replacing browser observation. `SystemObj:wait(timeout)` force-kills after any
elapsed timeout, including zero, while `vim.ui.open()` returns that SystemObj
without an exit callback. Production no longer calls `wait`; it polls the
non-destructive result retained by Neovim after process exit and inherited pipe
closure. Real SystemObj tests cover zero/non-zero completion, a descendant that
retains output pipes after the handler exits, observation timeout, and setup
cancellation. Timeout and cancellation leave the delayed handler to complete
with code zero and signal zero rather than poisoning or signalling it.

Manual native-package instructions now create the `pack/*/start` parent before
clone and retain the concrete `:helptags` command. The help file defines exact
tags for all six commands. Its smoke builds a tag set and checks every local
`|SprintLoop*|` reference plus all six command tags by exact key, not substring.
Status decode/render coverage is table-driven across all 15 accepted workflow
states with valid process, active-invocation, interaction, and reason presence.

The final complete plugin command passed with `654` assertions. The complete
controller suite passed `213` tests with one opt-in real-server test skipped.
Controller compilation, Ruff lint and format checks, strict mypy, package build,
and a fresh no-index wheel-install help/version smoke passed. A disposable
native-package copy generated help tags, loaded the package, and opened the base
and all six command help tags successfully; the first smoke invocation exceeded
Neovim's startup `+command` count and was corrected to one command chain before
passing. Final diff/status checks are recorded by S3-DONE-006 and S3-DONE-007
below. No Lua formatter/linter is configured, so S3-TEST-016 and S3-DONE-005
remain unchecked.

PASS6-013 remains P2 with disposition `defer` for Sprint 8 URL-authority
compatibility; no compatibility widening was implemented. S3-MKCHAD-008,
S3-DETACH-006, S3-DEMO-002 through S3-DEMO-010,
S3-REVIEW-011, S3-DONE-001, S3-DONE-002, S3-DONE-005, S3-DONE-010,
S3-DONE-011 remain unchecked. No
external OpenCode/private-CA mkchad demonstration, Lua-tooling decision,
independent audit, or aggregate completion gate was claimed.

### Repair-round-9 evidence (2026-07-15)

Auditor pass-9 findings AUD-S3-P9-001 and AUD-S3-P9-002 were selected as
P1/`fix_now` and repaired in pushed plugin commit `9a1f446`, before this parent
gitlink update, without accessing live mkchad state or closing an
external gate. They temporarily reversed the prior evidence for S3-SETUP-008,
S3-RESOLVE-010, S3-JSON-010, S3-UI-002, S3-WATCH-001, S3-WATCH-003,
S3-WATCH-004, S3-WATCH-004A, S3-WATCH-007 through S3-WATCH-012,
S3-TEST-008 through S3-TEST-010, S3-DOC-001, and S3-DOC-002. Those checked
items were rechecked only after the focused overlap/terminal paths and complete
plugin suite passed.

Start/resume discovery now records every successfully spawned launch by unique
identity, setup generation, and resolved root. Same-root watcher replacement
retains all live launch ownership, each process completion releases only its own
identity even after replacement, and final no-run shutdown occurs only after no
same-root launch remains. Tests cover two rapid launches with a rejected newer
launch, later active and `waiting_for_user` status from the older launch, both
completion orders, setup replacement, and independent roots/generations without
stale mutation. Terminal `stopped`, `failed`, and `finished` status accepts
`process_running` true and false through decode, render, and public progress;
all six projections retain a null active invocation and state-appropriate reason
semantics.

The complete plugin command passed with `707` assertions. The first complete
controller run had one five-second readiness timeout in
`test_separate_process_ownership_lock_rejects_run`; that unchanged test passed
immediately in a focused rerun, and the complete isolated rerun then passed all
`213` tests with one opt-in real-server test skipped. Controller compilation,
Ruff lint/format checks, strict mypy, package build, and a fresh no-index wheel
install with help/version smokes passed. The plugin suite also exercised the
repository fake, detached lifetime, and native help-tag generation. Final
`git diff --check` and repository-status evidence is recorded by S3-DONE-006
and S3-DONE-007.

PASS6-013 remains P2 with disposition `defer` for Sprint 8; no URL-authority
compatibility widening was implemented. No Lua formatter/linter is configured,
so S3-TEST-016 and S3-DONE-005 remain unchecked. S3-MKCHAD-008,
S3-DETACH-006, S3-DEMO-002 through S3-DEMO-010, S3-REVIEW-011,
S3-DONE-001, S3-DONE-002, S3-DONE-010, and S3-DONE-011 remain unchecked. No
external OpenCode/private-CA mkchad demonstration, Lua-tooling decision,
independent audit, or aggregate completion gate was claimed.

- [x] **S3-REVIEW-001** Audit implementation against `docs/threat_model.md`, `docs/audit_policy.md`, and Sprint 3's plugin-specific failure model.
- [x] **S3-REVIEW-002** Prioritize ordinary malformed setup, process failure, malformed status, timer races, credential exposure, and live-environment mistakes.
- [x] **S3-REVIEW-003** Confirm no shell interpolation path exists.
- [x] **S3-REVIEW-004** Confirm no plugin workflow decision relies on controller prose, title text, or unknown JSON fields.
- [x] **S3-REVIEW-005** Confirm no server start/substitution path exists in generic or mkchad integration.
- [x] **S3-REVIEW-006** Confirm no credentials, CA contents, question text, answers, transcripts, or complete environments appear in tracked artifacts or UI.
- [x] **S3-REVIEW-007** Confirm background polling cannot overlap, spam notifications, or persist authoritative state.
- [x] **S3-REVIEW-008** Confirm closing Neovim cannot terminate the detached controller through plugin-owned teardown.
- [x] **S3-REVIEW-009** Confirm the live mkchad checkout and runtime environment were untouched.
- [x] **S3-REVIEW-010** Record residual browser trust, desktop-notification, detached-process, and plugin/controller skew limitations.
- [ ] **S3-REVIEW-011** Obtain a fresh independent audit with no unresolved P0/P1 findings under the current threat model.

## 23. Scope Review

- [x] **S3-SCOPEREVIEW-001** Confirm no product Builder prompt or mutating-agent handoff was implemented.
- [x] **S3-SCOPEREVIEW-002** Confirm no durable `waiting_for_user`, question event, OpenCode question API call, answer, or rejection was implemented.
- [x] **S3-SCOPEREVIEW-003** Confirm no implementation commit, checkpoint commit, push, audit, CI, or GitHub behavior was added.
- [x] **S3-SCOPEREVIEW-004** Confirm pause, resume, and stop remain controller-delegated and non-functional at the workflow layer.
- [x] **S3-SCOPEREVIEW-005** Confirm no server launcher, registry, replacement server, multiplexer, or embedded webview was added.
- [x] **S3-SCOPEREVIEW-006** Confirm no plugin-owned persistence, configuration editor, findings editor, or automatic retry was added.
- [x] **S3-SCOPEREVIEW-007** Confirm no multi-repository or non-GitHub future behavior was introduced.
- [x] **S3-SCOPEREVIEW-008** Compare public and additive status contracts with both authoritative V1 documents and update them deliberately for any approved difference.

## 24. Exit Demonstration

- [x] **S3-DEMO-001** Load the plugin under Neovim 0.12 with required setup and current mkchad callback/CA adapters.
- [ ] **S3-DEMO-002** Use a disposable clean sprint-history fixture and externally started supported OpenCode server.
- [ ] **S3-DEMO-003** Supply Basic authentication only through inherited environment and private CA only through child `SSL_CERT_FILE`.
- [ ] **S3-DEMO-004** Start the Sprint 2 execution probe through `SprintLoopStart` without blocking Neovim.
- [ ] **S3-DEMO-005** Show active progress including role, invocation, session, `running`, null interaction, commits, audit, CI, checklist, and last event.
- [ ] **S3-DEMO-006** Observe the normative execution-probe title in an ordinary OpenCode client.
- [ ] **S3-DEMO-007** Open the exact encoded session URL through `SprintLoopOpenSession` after browser CA trust is prepared separately.
- [ ] **S3-DEMO-008** Close Neovim while the controller is active and prove the controller continues.
- [ ] **S3-DEMO-009** Reopen Neovim, rerun setup, and rediscover active status without launching a second controller.
- [ ] **S3-DEMO-010** Observe the eventual `blocked/execution_not_implemented` result accurately.
- [x] **S3-DEMO-011** Use a controlled waiting fixture and show one notification across repeated polls.
- [x] **S3-DEMO-012** Demonstrate malformed status, missing web URL, inactive session, browser failure, and non-zero CLI diagnostics.
- [x] **S3-DEMO-013** Demonstrate pause, resume, and stop delegation returning accurate current controller errors without simulated transitions.
- [x] **S3-DEMO-014** Run complete offline plugin tests and affected controller tests.
- [x] **S3-DEMO-015** Show the disposable current-remote mkchad clone and isolated XDG roots used for integration.
- [x] **S3-DEMO-016** Confirm the live mkchad checkout, runtime state, server processes, TLS material, and credentials were not changed or reused.

## 25. Completion Gate

- [ ] **S3-DONE-001** Every applicable checklist item above is checked.
- [ ] **S3-DONE-002** Every Sprint 3 acceptance criterion in `sprint_spec.md` is demonstrably satisfied.
- [x] **S3-DONE-003** Focused plugin and controller tests pass during development.
- [x] **S3-DONE-004** The complete default plugin and controller suites pass without external network or credentials.
- [ ] **S3-DONE-005** Required Lua and Python formatting, linting, typing, compilation, build, and clean-install checks pass.
- [x] **S3-DONE-006** `git diff --check` passes in both repositories.
- [x] **S3-DONE-007** Final controller and plugin repository statuses contain only intended Sprint 3 changes.
- [x] **S3-DONE-008** No credentials, generated runtime state, browser artifacts, live mkchad data, or temporary fixtures are tracked.
- [x] **S3-DONE-009** Documentation describes actual Sprint 3 behavior and does not claim Sprint 4 or Sprint 7 functionality.
- [ ] **S3-DONE-010** The exit demonstration has been performed and its commands are reproducible from documentation.
- [ ] **S3-DONE-011** A fresh independent audit reports no unresolved P0/P1 findings under the current threat model.
- [x] **S3-DONE-012** Plugin changes are committed and pushed before the parent submodule pointer commit.
- [x] **S3-DONE-013** The live `~/.config/mkchad` environment remains unchanged and development used only a disposable current-remote clone with isolated XDG roots.
