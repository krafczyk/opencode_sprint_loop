# Controller V1 Sprint 3 Checklist: Neovim Client V1

## Usage

This checklist tracks implementation of `docs/controller-v1/3/sprint_spec.md`. It does not replace the sprint specification or authoritative V1 documents.

An item may be checked only when its implementation, tests, and required documentation are complete. If implementation changes a durable schema, CLI contract, plugin public API, Git ownership rule, terminal-state rule, external compatibility policy, or user-interaction behavior, update the governing specification before checking the item.

## 1. Scope and Inputs

- [ ] **S3-SCOPE-001** Read the nearest `AGENTS.md`, both authoritative V1 documents, `docs/threat_model.md`, `docs/audit_policy.md`, and the complete Sprint 3 specification before implementation.
- [ ] **S3-SCOPE-002** Confirm Sprint 2 remains complete and Sprint 3 is the active implementation sprint.
- [ ] **S3-SCOPE-003** Inspect status in both the controller and plugin repositories and preserve unrelated work.
- [ ] **S3-SCOPE-004** Keep Builder execution, durable question monitoring, Git handoff, audit rounds, publication, CI, functional controls, and recovery out of Sprint 3.
- [ ] **S3-SCOPE-005** Preserve the controller as workflow authority and the plugin as a launcher, status observer, and presentation client only.
- [ ] **S3-SCOPE-006** Apply the trusted-user prototype threat model without expanding Sprint 3 for excluded hostile local races.
- [ ] **S3-SCOPE-007** Treat waiting-for-user status as a presentation compatibility contract; do not claim a real Sprint 3 agent can ask a question.
- [ ] **S3-SCOPE-008** Record every accepted limitation and later-sprint dependency without presenting it as implemented behavior.

## 2. Live mkchad Safety

- [ ] **S3-MKCHAD-001** Treat `~/.config/mkchad` as a live user environment and do not edit any file beneath it.
- [ ] **S3-MKCHAD-002** Record live checkout status before integration work without staging, cleaning, or modifying its untracked files.
- [ ] **S3-MKCHAD-003** Clone the current remote `mkchad` branch into a disposable directory outside the live configuration.
- [ ] **S3-MKCHAD-004** Record the remote clone commit used for integration evidence and refresh the clone if the remote branch changes.
- [ ] **S3-MKCHAD-005** Use isolated `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME` for every test that sources mkchad code.
- [ ] **S3-MKCHAD-006** Use only mkchad's explicit test seams and disposable state; never discover or reuse the live managed server, TLS files, logs, locks, or credentials.
- [ ] **S3-MKCHAD-007** Verify integration with callback-style `vim.g.opencode_opts.server.url(callback)` from the disposable clone.
- [ ] **S3-MKCHAD-008** Verify private-CA integration with the disposable clone's `vim.g.opencode_opts.server.ca_cert()` accessor.
- [ ] **S3-MKCHAD-009** Prove the Sprint Loop plugin never calls mkchad's `server.ensure` or another server-start operation.
- [ ] **S3-MKCHAD-010** Recheck live checkout Git status after testing and confirm it is unchanged, without inspecting live runtime state.

## 3. Repository and Submodule Workflow

- [ ] **S3-REPO-001** Implement plugin code in the `opencode_sprint_loop.lua` repository, not as Python-embedded Lua.
- [ ] **S3-REPO-002** Keep controller changes limited to additive status projection, tests, documentation, and the eventual plugin gitlink update.
- [ ] **S3-REPO-003** Use a conventional Lua plugin layout with clear configuration, process, status, and UI boundaries.
- [ ] **S3-REPO-004** Add no generated test output, status document, runtime state, browser artifact, credential, or transcript to either repository.
- [ ] **S3-REPO-005** Commit and push verified plugin changes from inside the plugin repository before updating the parent gitlink.
- [ ] **S3-REPO-006** Update the parent gitlink in a separate parent commit without mixing unrelated controller or plugin work.
- [ ] **S3-REPO-007** Inspect final status and diff in both repositories before Sprint 3 completion.

## 4. Runtime and Architecture

- [ ] **S3-ARCH-001** Require Neovim 0.12 and reject older versions before process or buffer mutation.
- [ ] **S3-ARCH-002** Keep the plugin free of required runtime dependencies beyond Neovim and the configured controller executable.
- [ ] **S3-ARCH-003** Keep process execution independent from status decoding and UI rendering.
- [ ] **S3-ARCH-004** Keep status validation independent from Neovim window layout where practical.
- [ ] **S3-ARCH-005** Keep internal process, timer, notifier, browser, and clock seams narrow enough for deterministic tests.
- [ ] **S3-ARCH-006** Make no direct Git, GitHub, OpenCode question, controller state-file, event-log, invocation-record, or lock-file call from the plugin.
- [ ] **S3-ARCH-007** Keep every user-triggered process and status action asynchronous on Neovim's main interaction path.
- [ ] **S3-ARCH-008** Document public functions and non-obvious lifecycle boundaries with concise Lua annotations or help text.

## 5. Setup Contract

- [ ] **S3-SETUP-001** Require a successful `setup()` before every public action and command.
- [ ] **S3-SETUP-002** Require explicit `sprint_root` and `server_url` setup fields.
- [ ] **S3-SETUP-003** Default `executable` to exact string `sprint-loop`.
- [ ] **S3-SETUP-004** Support optional `web_url` and `server_ca_cert` fields.
- [ ] **S3-SETUP-005** Accept documented string and resolver forms for each option.
- [ ] **S3-SETUP-006** Reject unknown option names and unsupported primitive or collection values.
- [ ] **S3-SETUP-007** Validate setup shape without prematurely resolving callbacks or contacting external systems.
- [ ] **S3-SETUP-008** Make repeated setup replace configuration and invalidate prior watcher/timer callbacks without duplicate commands.
- [ ] **S3-SETUP-009** Perform one initial asynchronous status observation after setup for the newly resolved root.
- [ ] **S3-SETUP-010** Fail actions before setup with actionable `setup_required` behavior and no process, timer, browser, or buffer mutation.

## 6. Resolver Semantics

- [ ] **S3-RESOLVE-001** Resolve only values needed by the current action.
- [ ] **S3-RESOLVE-002** Resolve action values at invocation time so roots and server URLs may change after setup.
- [ ] **S3-RESOLVE-003** Support synchronous URL resolvers that return one non-empty string.
- [ ] **S3-RESOLVE-004** Support callback-style URL resolvers that call `done(value, error)` exactly once.
- [ ] **S3-RESOLVE-005** Enforce the documented five-second callback resolver timeout or update the specification before choosing another bound.
- [ ] **S3-RESOLVE-006** Reject dual return-and-callback completion and duplicate callbacks deterministically.
- [ ] **S3-RESOLVE-007** Ignore late resolver completions from a replaced setup or watcher generation.
- [ ] **S3-RESOLVE-008** Catch resolver exceptions and return concise errors without ordinary Lua tracebacks.
- [ ] **S3-RESOLVE-009** Reject nil, empty, non-string, NUL-bearing, and control-character resolved values.
- [ ] **S3-RESOLVE-010** Bind one watcher generation to the root resolved when that generation starts.
- [ ] **S3-RESOLVE-011** Never resolve or display `server_url` for status-only progress when it is not needed.
- [ ] **S3-RESOLVE-012** Never call a server launcher, ensure callback, registry, or discovery fallback while resolving URLs.

## 7. Public Lua API and Commands

- [ ] **S3-API-001** Export `setup(options)` from `require("opencode_sprint_loop")`.
- [ ] **S3-API-002** Export asynchronous `start()`.
- [ ] **S3-API-003** Export asynchronous `progress()`.
- [ ] **S3-API-004** Export asynchronous `pause()`.
- [ ] **S3-API-005** Export asynchronous `resume()`.
- [ ] **S3-API-006** Export asynchronous `stop()`.
- [ ] **S3-API-007** Export asynchronous `open_session()`.
- [ ] **S3-API-008** Avoid a stable process-handle, status-object, or workflow-result return contract from action methods.
- [ ] **S3-CMD-001** Register `SprintLoopStart` exactly once.
- [ ] **S3-CMD-002** Register `SprintLoopProgress` exactly once.
- [ ] **S3-CMD-003** Register `SprintLoopPause` exactly once.
- [ ] **S3-CMD-004** Register `SprintLoopResume` exactly once.
- [ ] **S3-CMD-005** Register `SprintLoopStop` exactly once.
- [ ] **S3-CMD-006** Register `SprintLoopOpenSession` exactly once.
- [ ] **S3-CMD-007** Give commands no V1 arguments and route each command through the corresponding public Lua method.
- [ ] **S3-CMD-008** Add no global key mappings.

## 8. CLI Argument Construction

- [ ] **S3-ARGV-001** Construct start argv as `<executable> run --root <root> --server-url <url>`.
- [ ] **S3-ARGV-002** Construct progress/watcher argv as `<executable> status --root <root> --json`.
- [ ] **S3-ARGV-003** Construct pause argv as `<executable> pause --root <root>`.
- [ ] **S3-ARGV-004** Construct resume argv as `<executable> resume --root <root> --server-url <url>`.
- [ ] **S3-ARGV-005** Construct stop argv as `<executable> stop --root <root>`.
- [ ] **S3-ARGV-006** Pass argv arrays directly to Neovim/libuv and never invoke `sh -c` or another shell.
- [ ] **S3-ARGV-007** Preserve spaces, quotes, wildcard characters, substitutions, and separators as literal argument content.
- [ ] **S3-ARGV-008** Keep server credentials out of argv and inherit supported authentication from the child environment.
- [ ] **S3-ARGV-009** Do not preflight duplicate runs, repository state, or workflow transitions in Lua.

## 9. Private CA Child Environment

- [ ] **S3-CA-001** Resolve `server_ca_cert` only for controller actions that may contact OpenCode.
- [ ] **S3-CA-002** Require the resolved CA path to be absolute, readable, regular, and free of NUL/control characters.
- [ ] **S3-CA-003** Set `SSL_CERT_FILE` only in the spawned controller child environment when the option is configured.
- [ ] **S3-CA-004** Preserve the inherited environment unchanged when no CA option is configured.
- [ ] **S3-CA-005** Keep the CA path out of argv, progress buffers, routine notifications, captured fixtures, and documentation examples.
- [ ] **S3-CA-006** Do not copy CA contents, TLS keys, OpenCode passwords, or complete child environments.
- [ ] **S3-CA-007** Document that browser trust is configured separately by the operator and is not changed by the plugin.

## 10. Asynchronous Process Handling

- [ ] **S3-PROC-001** Return control to Neovim immediately after each successful process spawn.
- [ ] **S3-PROC-002** Capture standard output and standard error through bounded handlers.
- [ ] **S3-PROC-003** Report missing executable and spawn errors clearly.
- [ ] **S3-PROC-004** Report non-zero controller exits with bounded diagnostics but do not infer a workflow transition from prose.
- [ ] **S3-PROC-005** Avoid blocking `wait()` or synchronous system calls on interactive command paths.
- [ ] **S3-PROC-006** Prevent stale callbacks from a replaced setup generation from opening buffers or notifying current state.
- [ ] **S3-PROC-007** Clean up plugin-owned process pipes and handles without signalling detached controller ownership incorrectly.
- [ ] **S3-PROC-008** Bound retained output and discard it after the result is rendered.

## 11. Detached Controller Lifetime

- [ ] **S3-DETACH-001** Spawn `sprint-loop run` in a detached process group or equivalent Neovim 0.12 mode.
- [ ] **S3-DETACH-002** Notify successful spawn without claiming the controller is durably running.
- [ ] **S3-DETACH-003** Confirm process activity only through later controller status.
- [ ] **S3-DETACH-004** Ensure `VimLeave` and plugin teardown do not send a termination signal to the controller.
- [ ] **S3-DETACH-005** Add a process-level test proving a child survives the launching headless Neovim process.
- [ ] **S3-DETACH-006** Prove closing Neovim during the real Sprint 2 probe does not terminate the controller.

## 12. Additive Controller Status Projection

- [ ] **S3-STATUS-001** Preserve every stable Sprint 1 and Sprint 2 JSON status field and meaning.
- [ ] **S3-STATUS-002** Keep no-run `active` exactly null.
- [ ] **S3-STATUS-003** Add null `active.status` and `active.interaction` for an inactive persisted run.
- [ ] **S3-STATUS-004** Project active Sprint 2 invocation status as exact `running`.
- [ ] **S3-STATUS-005** Project active Sprint 2 interaction as null.
- [ ] **S3-STATUS-006** Keep status schema version `1` and document the fields as backward-compatible additions.
- [ ] **S3-STATUS-007** Update human status only as needed to remain accurate and credential-free.
- [ ] **S3-STATUS-008** Make no state-schema, event-schema, invocation-record, Git, or OpenCode lifecycle change for the additive fields.
- [ ] **S3-STATUS-009** Add controller tests for no-run, inactive, and active projections.
- [ ] **S3-STATUS-010** Keep status read-only, local, and independent of server availability.

## 13. Plugin Status Validation

- [ ] **S3-JSON-001** Require exactly one bounded JSON object from `status --json` standard output.
- [ ] **S3-JSON-002** Require integer schema version `1` without accepting booleans.
- [ ] **S3-JSON-003** Require a non-empty controller version and every stable top-level field.
- [ ] **S3-JSON-004** Validate no-run nullability and process-running invariants.
- [ ] **S3-JSON-005** Validate inactive active-object nullability including status and interaction.
- [ ] **S3-JSON-006** Validate active `running` status with null interaction.
- [ ] **S3-JSON-007** Validate future-compatible `waiting_for_user` with exact interaction fields.
- [ ] **S3-JSON-008** Require positive integer question count without accepting booleans.
- [ ] **S3-JSON-009** Validate bounded request ID and displayable asked-at string.
- [ ] **S3-JSON-010** Reject contradictory active status, session, and interaction combinations.
- [ ] **S3-JSON-011** Reject duplicate keys, trailing values, non-finite values, invalid UTF-8, empty output, and oversized output.
- [ ] **S3-JSON-012** Ignore unknown additional object fields for forward compatibility.
- [ ] **S3-JSON-013** Never read `state.json`, events, invocation artifacts, Git, or OpenCode directly to repair malformed status.

## 14. Progress Floating Window

- [ ] **S3-UI-001** Open progress in a centered floating window backed by a plugin-owned scratch buffer.
- [ ] **S3-UI-002** Render sprint root, sprint identity, workflow state, and process-running status.
- [ ] **S3-UI-003** Render reason code/message prominently for blocked and failed states.
- [ ] **S3-UI-004** Render active role, invocation ID, session ID, status, and interaction summary.
- [ ] **S3-UI-005** Render local and pushed commit maps in deterministic repository order.
- [ ] **S3-UI-006** Render audit, CI, counters, checklist, last event, controller version, and update time.
- [ ] **S3-UI-007** Render a clear no-run view.
- [ ] **S3-UI-008** Use `buftype=nofile`, disable swap, and wipe on close.
- [ ] **S3-UI-009** Make the completed buffer non-modifiable.
- [ ] **S3-UI-010** Add buffer-local `q` and `Esc` close mappings only.
- [ ] **S3-UI-011** Adapt dimensions to small and large editor windows.
- [ ] **S3-UI-012** Reuse or replace the prior plugin view without leaking buffers/windows.
- [ ] **S3-UI-013** Keep server URLs, CA paths, credentials, prompts, result summaries, transcripts, and question/answer text out of the buffer.

## 15. Background Status Watcher

- [ ] **S3-WATCH-001** Maintain at most one watcher generation.
- [ ] **S3-WATCH-002** Perform one setup-time status query and watch when it discovers an active controller.
- [ ] **S3-WATCH-003** Start discovery after successful start and resume spawns.
- [ ] **S3-WATCH-004** Preserve discovery while the launched process is alive even before durable state becomes visible.
- [ ] **S3-WATCH-004A** Perform one final status query and stop when the launched command exits before an active run is observed.
- [ ] **S3-WATCH-005** Use one documented fixed non-busy polling interval.
- [ ] **S3-WATCH-006** Allow at most one status process in flight and never catch up with overlapping polls.
- [ ] **S3-WATCH-007** Stop after an observed active controller becomes inactive.
- [ ] **S3-WATCH-008** Cancel timers and invalidate callbacks on repeated setup and Neovim exit.
- [ ] **S3-WATCH-009** Deduplicate waiting notifications by request ID within one Neovim process.
- [ ] **S3-WATCH-010** Notify exactly once for repeated observations of one request.
- [ ] **S3-WATCH-011** Notify again for a distinct request and once after a new Neovim process discovers an existing request.
- [ ] **S3-WATCH-012** Include safe active role/invocation context and direct the user to `SprintLoopOpenSession`.
- [ ] **S3-WATCH-013** Keep question text, options, answers, URLs, CA paths, and credentials out of notifications.
- [ ] **S3-WATCH-014** Emit at most one warning per continuous watcher failure episode and reset suppression after success.
- [ ] **S3-WATCH-015** Treat malformed status as an error, not no-run, stopped, or success.
- [ ] **S3-WATCH-016** Make no direct OpenCode, question reply/reject, workflow mutation, or persistence call.

## 16. Active Session Browser URL

- [ ] **S3-WEB-001** Query current status asynchronously before opening a session.
- [ ] **S3-WEB-002** Require a persisted run and non-empty active session ID.
- [ ] **S3-WEB-003** Resolve `web_url` only after an active session is known.
- [ ] **S3-WEB-004** Accept a credential-free absolute HTTP or HTTPS web base, including a supported path prefix.
- [ ] **S3-WEB-005** Reject user-info, query, fragment, empty host, control character, and unsupported scheme.
- [ ] **S3-WEB-006** Encode canonical status `sprint_root` as RFC 4648 URL-safe base64 without padding.
- [ ] **S3-WEB-007** Percent-encode session ID as one path segment.
- [ ] **S3-WEB-008** Join trailing slash and path-prefix cases deterministically.
- [ ] **S3-WEB-009** Open through Neovim 0.12's browser API without invoking a shell command.
- [ ] **S3-WEB-010** Report missing web URL, missing active session, invalid base, and browser failure actionably.
- [ ] **S3-WEB-011** Do not put Basic-auth credentials or CA contents in the browser URL.
- [ ] **S3-WEB-012** Document manual browser trust for mkchad's private CA.

## 17. Session Title Convention

- [ ] **S3-TITLE-001** Enforce/document `[<multisprint>/<sprint>] <role> <sequence> <purpose>` as the normative controller title format.
- [ ] **S3-TITLE-002** Require sequence padding to at least four decimal digits.
- [ ] **S3-TITLE-003** Use canonical workflow role and concise phase purpose.
- [ ] **S3-TITLE-004** Keep titles descriptive only and never use them as identity evidence.
- [ ] **S3-TITLE-005** Verify the Sprint 2 probe title matches `[<multisprint>/<sprint>] auditor 0001 execution probe`.
- [ ] **S3-TITLE-006** Add no extra Sprint 3 session and do not rename an existing remote session.

## 18. Control Delegation

- [ ] **S3-CTRL-001** Delegate pause to the controller and display its current response.
- [ ] **S3-CTRL-002** Delegate resume with a newly resolved server URL and optional CA child environment.
- [ ] **S3-CTRL-003** Delegate stop to the controller and display its current response.
- [ ] **S3-CTRL-004** Preserve Sprint 2 `feature_not_implemented` behavior without simulated status changes.
- [ ] **S3-CTRL-005** Do not kill, abort, retry, or mutate the controller in response to a control-command failure.
- [ ] **S3-CTRL-006** Keep the real question lifecycle assigned to Sprint 4 and pause/resume/stop behavior at a waiting-for-user boundary assigned to Sprint 7.

## 19. Errors, Bounds, and Security

- [ ] **S3-ERR-001** Implement and document the Sprint 3 plugin error categories or tested equivalents.
- [ ] **S3-ERR-002** Use standard Neovim notification levels consistently.
- [ ] **S3-ERR-003** Keep expected setup, resolver, process, status, CA, and browser failures free of ordinary tracebacks.
- [ ] **S3-ERR-004** Bound status stdout, controller stdout/stderr, resolver errors, and displayed strings.
- [ ] **S3-ERR-005** Prefer bounded controller stderr without parsing it into workflow decisions.
- [ ] **S3-ERR-006** Never echo a rejected credential-bearing URL verbatim.
- [ ] **S3-SEC-001** Use argv arrays for every process.
- [ ] **S3-SEC-002** Keep credentials, complete environments, CA contents, and private runtime paths out of persistence and committed fixtures.
- [ ] **S3-SEC-003** Pass a configured CA path only through the child environment.
- [ ] **S3-SEC-004** Treat all status values as display text and never execute them as commands, mappings, format strings, or help tags.
- [ ] **S3-SEC-005** Use synthetic security-sensitive values in tests and examples.
- [ ] **S3-SEC-006** Confirm the plugin owns no persistent file and never writes controller workflow data.

## 20. Automated Verification

- [ ] **S3-TEST-001** Add a documented headless Neovim 0.12 test command.
- [ ] **S3-TEST-002** Keep default plugin tests independent of OpenCode, GitHub, network, model usage, browser availability, and credentials.
- [ ] **S3-TEST-003** Test command registration and every public Lua method.
- [ ] **S3-TEST-004** Test setup shape, repeated setup, synchronous resolvers, callback resolvers, timeout, duplicate completion, and stale completion.
- [ ] **S3-TEST-005** Test exact argv and no shell interpolation with hostile-looking literal values.
- [ ] **S3-TEST-006** Test asynchronous behavior and bounded output using a fake executable.
- [ ] **S3-TEST-007** Test process-level detached survival after headless Neovim exits.
- [ ] **S3-TEST-008** Test every supported status state and malformed status category.
- [ ] **S3-TEST-009** Test progress buffer content, options, mappings, dimensions, and lifecycle.
- [ ] **S3-TEST-010** Test watcher activation, one-in-flight rule, deduplication, failure suppression, replacement, and shutdown.
- [ ] **S3-TEST-011** Test URL-safe root encoding, session encoding, web-base validation, and browser outcomes.
- [ ] **S3-TEST-012** Test CA path validation and exact child environment without capturing CA content.
- [ ] **S3-TEST-013** Test accurate pause/resume/stop error delegation.
- [ ] **S3-TEST-014** Add focused controller tests for additive status fields and unchanged state/event behavior.
- [ ] **S3-TEST-015** Keep all existing controller tests green after intentional status snapshot updates.
- [ ] **S3-TEST-016** Run selected Lua formatting/linting checks documented by the plugin repository.
- [ ] **S3-TEST-017** Run Python formatting, linting, strict typing, compilation, build, and clean-install checks required by the controller repository.
- [ ] **S3-TEST-018** Run `git diff --check` in both repositories.
- [ ] **S3-TEST-019** Test the Neovim minimum-version gate with a controlled older-version fixture without requiring an installed older Neovim.

## 21. Documentation

- [ ] **S3-DOC-001** Expand the plugin README with installation, Neovim 0.12, setup, and command usage.
- [ ] **S3-DOC-002** Add a Neovim help file documenting every public option, method, command, and error path.
- [ ] **S3-DOC-003** Document required setup and explicit root/server configuration.
- [ ] **S3-DOC-004** Document synchronous and callback-style URL resolver contracts.
- [ ] **S3-DOC-005** Provide generic and current mkchad adapter examples without hard-coding mkchad in plugin source.
- [ ] **S3-DOC-006** Document that URL resolution must not call mkchad server ensure/start behavior.
- [ ] **S3-DOC-007** Document optional `server_ca_cert`, child `SSL_CERT_FILE`, and separate browser CA trust.
- [ ] **S3-DOC-008** Document detached launch and the distinction between spawn success and confirmed controller activity.
- [ ] **S3-DOC-009** Document progress fields, close mappings, and no-run presentation.
- [ ] **S3-DOC-010** Document watcher lifetime, deduplicated question notification, and lack of plugin question answering.
- [ ] **S3-DOC-011** Document active-session URL construction and missing-session/browser failures.
- [ ] **S3-DOC-012** Document current `feature_not_implemented` pause/resume/stop behavior.
- [ ] **S3-DOC-013** Document default fake/headless tests and opt-in real OpenCode demonstration.
- [ ] **S3-DOC-014** Warn that `~/.config/mkchad` is live and all development integration uses a disposable remote clone plus isolated XDG roots.
- [ ] **S3-DOC-015** Update the parent README to identify Sprint 3 and its exact implemented limitations.
- [ ] **S3-DOC-016** Keep Builder, real pending-question monitoring, commits, audit, CI, and recovery clearly unimplemented.

## 22. Threat and Security Review

- [ ] **S3-REVIEW-001** Audit implementation against `docs/threat_model.md`, `docs/audit_policy.md`, and Sprint 3's plugin-specific failure model.
- [ ] **S3-REVIEW-002** Prioritize ordinary malformed setup, process failure, malformed status, timer races, credential exposure, and live-environment mistakes.
- [ ] **S3-REVIEW-003** Confirm no shell interpolation path exists.
- [ ] **S3-REVIEW-004** Confirm no plugin workflow decision relies on controller prose, title text, or unknown JSON fields.
- [ ] **S3-REVIEW-005** Confirm no server start/substitution path exists in generic or mkchad integration.
- [ ] **S3-REVIEW-006** Confirm no credentials, CA contents, question text, answers, transcripts, or complete environments appear in tracked artifacts or UI.
- [ ] **S3-REVIEW-007** Confirm background polling cannot overlap, spam notifications, or persist authoritative state.
- [ ] **S3-REVIEW-008** Confirm closing Neovim cannot terminate the detached controller through plugin-owned teardown.
- [ ] **S3-REVIEW-009** Confirm the live mkchad checkout and runtime environment were untouched.
- [ ] **S3-REVIEW-010** Record residual browser trust, desktop-notification, detached-process, and plugin/controller skew limitations.
- [ ] **S3-REVIEW-011** Obtain a fresh independent audit with no unresolved P0/P1 findings under the current threat model.

## 23. Scope Review

- [ ] **S3-SCOPEREVIEW-001** Confirm no product Builder prompt or mutating-agent handoff was implemented.
- [ ] **S3-SCOPEREVIEW-002** Confirm no durable `waiting_for_user`, question event, OpenCode question API call, answer, or rejection was implemented.
- [ ] **S3-SCOPEREVIEW-003** Confirm no implementation commit, checkpoint commit, push, audit, CI, or GitHub behavior was added.
- [ ] **S3-SCOPEREVIEW-004** Confirm pause, resume, and stop remain controller-delegated and non-functional at the workflow layer.
- [ ] **S3-SCOPEREVIEW-005** Confirm no server launcher, registry, replacement server, multiplexer, or embedded webview was added.
- [ ] **S3-SCOPEREVIEW-006** Confirm no plugin-owned persistence, configuration editor, findings editor, or automatic retry was added.
- [ ] **S3-SCOPEREVIEW-007** Confirm no multi-repository or non-GitHub future behavior was introduced.
- [ ] **S3-SCOPEREVIEW-008** Compare public and additive status contracts with both authoritative V1 documents and update them deliberately for any approved difference.

## 24. Exit Demonstration

- [ ] **S3-DEMO-001** Load the plugin under Neovim 0.12 with required setup and current mkchad callback/CA adapters.
- [ ] **S3-DEMO-002** Use a disposable clean sprint-history fixture and externally started supported OpenCode server.
- [ ] **S3-DEMO-003** Supply Basic authentication only through inherited environment and private CA only through child `SSL_CERT_FILE`.
- [ ] **S3-DEMO-004** Start the Sprint 2 execution probe through `SprintLoopStart` without blocking Neovim.
- [ ] **S3-DEMO-005** Show active progress including role, invocation, session, `running`, null interaction, commits, audit, CI, checklist, and last event.
- [ ] **S3-DEMO-006** Observe the normative execution-probe title in an ordinary OpenCode client.
- [ ] **S3-DEMO-007** Open the exact encoded session URL through `SprintLoopOpenSession` after browser CA trust is prepared separately.
- [ ] **S3-DEMO-008** Close Neovim while the controller is active and prove the controller continues.
- [ ] **S3-DEMO-009** Reopen Neovim, rerun setup, and rediscover active status without launching a second controller.
- [ ] **S3-DEMO-010** Observe the eventual `blocked/execution_not_implemented` result accurately.
- [ ] **S3-DEMO-011** Use a controlled waiting fixture and show one notification across repeated polls.
- [ ] **S3-DEMO-012** Demonstrate malformed status, missing web URL, inactive session, browser failure, and non-zero CLI diagnostics.
- [ ] **S3-DEMO-013** Demonstrate pause, resume, and stop delegation returning accurate current controller errors without simulated transitions.
- [ ] **S3-DEMO-014** Run complete offline plugin tests and affected controller tests.
- [ ] **S3-DEMO-015** Show the disposable current-remote mkchad clone and isolated XDG roots used for integration.
- [ ] **S3-DEMO-016** Confirm the live mkchad checkout, runtime state, server processes, TLS material, and credentials were not changed or reused.

## 25. Completion Gate

- [ ] **S3-DONE-001** Every applicable checklist item above is checked.
- [ ] **S3-DONE-002** Every Sprint 3 acceptance criterion in `sprint_spec.md` is demonstrably satisfied.
- [ ] **S3-DONE-003** Focused plugin and controller tests pass during development.
- [ ] **S3-DONE-004** The complete default plugin and controller suites pass without external network or credentials.
- [ ] **S3-DONE-005** Required Lua and Python formatting, linting, typing, compilation, build, and clean-install checks pass.
- [ ] **S3-DONE-006** `git diff --check` passes in both repositories.
- [ ] **S3-DONE-007** Final controller and plugin repository statuses contain only intended Sprint 3 changes.
- [ ] **S3-DONE-008** No credentials, generated runtime state, browser artifacts, live mkchad data, or temporary fixtures are tracked.
- [ ] **S3-DONE-009** Documentation describes actual Sprint 3 behavior and does not claim Sprint 4 or Sprint 7 functionality.
- [ ] **S3-DONE-010** The exit demonstration has been performed and its commands are reproducible from documentation.
- [ ] **S3-DONE-011** A fresh independent audit reports no unresolved P0/P1 findings under the current threat model.
- [ ] **S3-DONE-012** Plugin changes are committed and pushed before the parent submodule pointer commit.
- [ ] **S3-DONE-013** The live `~/.config/mkchad` environment remains unchanged and development used only a disposable current-remote clone with isolated XDG roots.
