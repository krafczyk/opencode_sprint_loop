# Controller V1 Sprint 3 Specification: Neovim Client V1

## Document Status

This document defines the implementation scope and acceptance contract for Sprint 3 of the Sprint Loop Controller V1 plan.

The following documents are authoritative over this sprint specification:

- `docs/v1_final_software_specification.md`
- `docs/multi_sprint_plan.md`

The operating assumptions and audit priorities in `docs/threat_model.md` and `docs/audit_policy.md` apply to this sprint. They do not override an explicit requirement in either authoritative document.

If this document appears to conflict with an authoritative document, implement the authoritative behavior and correct this document in the same change.

## 1. Sprint Goal

Provide a thin Neovim 0.12 client that launches the controller independently of Neovim lifetime, renders the stable controller status contract, opens the exact active OpenCode session, and notifies the user when future controller status reports that an agent is waiting for input.

Sprint 3 adds:

- The first implementation of the `opencode_sprint_loop.lua` plugin.
- A required, documented Lua `setup()` API and six asynchronous action methods.
- Six user commands backed by the public Lua methods.
- Detached controller launch and asynchronous CLI delegation without shell interpolation.
- A disposable progress buffer in a centered floating window.
- Exact active-session browser URL construction.
- A lightweight in-memory status watcher with deduplicated interaction notifications.
- Backward-compatible additive controller status fields for active invocation status and pending interaction metadata.
- A formal, recognizable OpenCode session-title convention.
- Deterministic headless Neovim tests and a fake `sprint-loop` executable.

Sprint 3 does not implement the Builder workflow, controller-side pending-question discovery, durable `waiting_for_user` transitions, question answering in Neovim, Git handoff, audits, publication, CI, or functional pause/resume/stop behavior.

## 2. Sprint Outcome

For a configured Neovim 0.12 instance and an installed Sprint 3-compatible controller, a user can:

1. Call `setup()` with explicit sprint-root and server-URL configuration.
2. Start `sprint-loop run` as a detached process without blocking Neovim.
3. Query `sprint-loop status --json` asynchronously.
4. View the complete supported status projection in a temporary floating buffer.
5. Close Neovim without sending a termination signal to the controller.
6. Reopen Neovim and discover an already-running controller through the setup-time status query.
7. Open the exact active OpenCode session through the configured browser-facing web base.
8. Receive one notification per distinct pending question request when a controlled status source reports `waiting_for_user`.
9. Invoke pause, resume, and stop through the stable CLI command surface and see their current controller responses without the plugin simulating workflow behavior.

The real Sprint 2 execution probe remains non-interactive and still ends intentionally at:

```text
blocked / execution_not_implemented
```

The waiting-for-user demonstration in Sprint 3 uses a deterministic status fixture. Sprint 4 owns the first real question-capable Builder invocation and the corresponding durable controller lifecycle.

## 3. Repository Ownership

### 3.1 Plugin Repository

Primary implementation occurs in the `opencode_sprint_loop.lua` repository included as a submodule of the controller source repository.

Expected logical layout:

```text
opencode_sprint_loop.lua/
|-- lua/
|   `-- opencode_sprint_loop/
|       |-- init.lua
|       |-- config.lua
|       |-- process.lua
|       |-- status.lua
|       `-- ui.lua
|-- plugin/
|   `-- opencode_sprint_loop.lua
|-- doc/
|   `-- opencode_sprint_loop.txt
|-- tests/
|-- AGENTS.md
`-- README.md
```

Equivalent module decomposition is allowed, but configuration, process execution, status validation/rendering, and presentation must remain comparably separated.

### 3.2 Controller Repository

The controller repository owns:

- This sprint specification and checklist.
- Additive `status --json` fields consumed by the plugin.
- Tests and documentation for the additive status contract.
- The formal session-title contract and its existing execution-probe verification.
- The parent repository submodule pointer after plugin changes are committed in the plugin repository.

Plugin changes must be committed in the plugin repository before the parent gitlink is updated. The parent commit must not absorb unrelated plugin or controller changes.

### 3.3 Live mkchad Environment

`~/.config/mkchad` is the user's live Neovim and OpenCode environment. Sprint 3 must not edit that checkout or run development tests against its XDG configuration, state, cache, data, managed server processes, TLS material, or credentials.

Before implementing or verifying mkchad integration, clone the current remote `mkchad` branch into a disposable location outside the live configuration, for example:

```bash
git clone --branch mkchad --single-branch \
  git@github.com:krafczyk/mkchad /tmp/opencode/mkchad-sprint3-current
```

Refresh or recreate the clone when remote integration behavior may have changed. Tests that source mkchad code must use isolated `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME` values and mkchad's explicit test seams. They must not discover, start, stop, reload, attach to, or reuse the live managed OpenCode server.

At Sprint 3 drafting time, the fresh remote `mkchad` branch was inspected at commit `938c325`. It exposes the existing managed server URL through callback-style `vim.g.opencode_opts.server.url(callback)` and the CA path through `vim.g.opencode_opts.server.ca_cert()`. These names are reference integration details, not APIs the generic plugin may hard-code. The plugin must not call `vim.g.opencode_opts.server.ensure`, because that operation may start the server and the controller requires an explicitly supplied already-running server.

## 4. Threat-Model Application

### 4.1 In-Scope Failures

Sprint 3 must handle ordinary, non-adversarial failures including:

- Missing or invalid setup options.
- Resolver callbacks that throw, return nil, or return empty or non-string values.
- Missing controller executables and process-spawn failures.
- Paths and URLs containing spaces or shell metacharacters.
- Non-zero controller exits and bounded diagnostics.
- Empty, malformed, oversized, unsupported, or inconsistent status JSON.
- Status reads while the controller is transitioning or has exited.
- No-run, inactive-run, active-run, blocked, failed, stopped, and finished projections.
- A future-compatible `waiting_for_user` projection with malformed interaction metadata.
- Repeated watcher observations of the same pending question.
- Temporary background status failures and controller exit while a poll is in flight.
- Missing or malformed browser-facing web URLs.
- Missing active sessions and browser-open failures.
- Neovim exit while a detached controller remains active.
- Plugin/controller version skew at the documented status boundary.
- Conventional credentials accidentally embedded in configured URLs or diagnostics.
- Accidental use of the live `~/.config/mkchad` checkout, XDG state, managed server, or TLS material during development tests.

### 4.2 Excluded Adversarial Scenarios

Sprint 3 is not required to defend against:

- A compromised Neovim process, OpenCode server, controller executable, browser opener, kernel, or filesystem.
- A hostile local process racing every callback, process, timer, or buffer operation.
- Deliberate replacement of the configured executable or browser after validation.
- Malicious status JSON crafted specifically to exploit Neovim internals beyond bounded parsing and structural validation.
- Hostile browser applications or URL handlers.

These exclusions do not permit shell interpolation, credential exposure, workflow mutation in the plugin, unbounded retained output, or false claims that a detached controller remains alive.

## 5. Runtime and Dependencies

### 5.1 Neovim Baseline

The plugin requires Neovim 0.12. It may use APIs available in Neovim 0.12 without compatibility shims for older releases.

Setup must fail clearly when loaded under an older Neovim version. The minimum-version check must not start a process, create a buffer, or mutate controller state.

### 5.2 Dependencies

The plugin has no required runtime dependency beyond Neovim 0.12 and the configured `sprint-loop` executable.

Default tests must run headlessly without a live OpenCode server, model provider, browser, GitHub account, network connection, or credentials. Prefer Neovim's built-in Lua facilities and a small repository-owned test harness over a framework dependency. If a test dependency becomes necessary, justify and document it before use.

An expected default test command is equivalent to:

```bash
nvim --headless --noplugin -u tests/minimal_init.lua -l tests/run.lua
```

The exact file names may vary, but the command must be documented and deterministic.

## 6. Public Lua API

### 6.1 Module

The stable module name is:

```lua
local sprint_loop = require("opencode_sprint_loop")
```

It exposes:

```lua
sprint_loop.setup(options)
sprint_loop.start()
sprint_loop.progress()
sprint_loop.pause()
sprint_loop.resume()
sprint_loop.stop()
sprint_loop.open_session()
```

Action methods are asynchronous presentation operations. They do not return a stable process handle, status object, or workflow result. Callers observe completion through notifications, the progress UI, and controller status.

Calling an action before successful setup must produce an actionable `setup_required` error and must not start a process or create the progress buffer.

### 6.2 Setup Shape

A representative setup is:

```lua
require("opencode_sprint_loop").setup({
  executable = "sprint-loop",
  sprint_root = function()
    return vim.fn.getcwd()
  end,
  server_url = function(done)
    local server = vim.g.opencode_opts and vim.g.opencode_opts.server
    if not server or type(server.url) ~= "function" then
      done(nil, "mkchad OpenCode URL resolver is unavailable")
      return
    end
    server.url(function(url)
      done(url, url and nil or "start OpenCode with :OpenCodeStart")
    end)
  end,
  web_url = function(done)
    vim.g.opencode_opts.server.url(done)
  end,
  server_ca_cert = function()
    return vim.g.opencode_opts.server.ca_cert()
  end,
})
```

The mkchad names shown here reflect the disposable reference clone. They are configuration details and must not be hard-coded by the generic plugin. In particular, the adapter reads only the existing URL and never calls mkchad's server `ensure` function.

Setup fields:

| Field | Required | Accepted value | Meaning |
| --- | --- | --- | --- |
| `sprint_root` | Yes | Non-empty string or resolver returning one | Sprint-history repository passed to `--root`. |
| `server_url` | Yes | Non-empty string or synchronous/callback resolver | Credential-free OpenCode server origin used by start and resume. |
| `executable` | No | Non-empty string or resolver returning one | Controller executable; defaults to `sprint-loop`. |
| `web_url` | No | Non-empty string or synchronous/callback resolver | Browser-facing OpenCode web base used only to open sessions. |
| `server_ca_cert` | No | Non-empty string or resolver returning one | CA certificate supplied to controller child processes for private HTTPS trust. |

Unknown setup fields and unsupported value types fail setup rather than being ignored. Setup validates option shape, not the current callback result or external resource.

Calling setup again replaces the prior configuration, cancels prior plugin timers and in-flight watcher generation, avoids duplicate command registration, and performs a new initial status observation for the newly resolved sprint root.

### 6.3 Resolver Semantics

- Resolve only fields relevant to the requested action.
- Resolve values when the action or watcher generation begins, not only during setup.
- Permit server and web URL resolvers either to return a string synchronously or to accept one completion callback and invoke it exactly once as `done(value, error)`.
- Bound asynchronous URL resolution to a documented fixed timeout; Sprint 3 uses five seconds unless real integration evidence requires a different bounded value.
- Reject a resolver that both returns a value and invokes its completion callback, invokes the callback more than once, or completes after its generation was replaced.
- Catch callback errors and render a concise diagnostic without a Lua traceback in normal use.
- Require resolved values to be non-empty strings without NUL or control characters.
- Never include the resolved server URL in routine notifications or progress buffers.
- Bind each active watcher generation to the sprint root resolved when that generation starts; a later setup or explicit action may replace it with a watcher for a different root.

The controller remains authoritative for canonical root validation and complete server-URL validation. The plugin performs only the local checks needed to avoid malformed argv and credential exposure.

## 7. User Commands

The plugin exposes exactly these V1 commands:

```text
:SprintLoopStart
:SprintLoopProgress
:SprintLoopPause
:SprintLoopResume
:SprintLoopStop
:SprintLoopOpenSession
```

Each command delegates to the same-named public Lua behavior:

| Command | Lua method |
| --- | --- |
| `SprintLoopStart` | `start()` |
| `SprintLoopProgress` | `progress()` |
| `SprintLoopPause` | `pause()` |
| `SprintLoopResume` | `resume()` |
| `SprintLoopStop` | `stop()` |
| `SprintLoopOpenSession` | `open_session()` |

Commands take no arguments in Sprint 3. They must remain responsive and must not call a blocking process wait, network request, or filesystem scan on Neovim's main interaction path.

## 8. Controller Process Contract

### 8.1 Argument Construction

The plugin invokes these exact semantic argument arrays:

```text
<executable> run --root <root> --server-url <server-url>
<executable> status --root <root> --json
<executable> pause --root <root>
<executable> resume --root <root> --server-url <server-url>
<executable> stop --root <root>
```

Arguments must be passed as an array directly to a Neovim or libuv process API. The plugin must not concatenate a shell command, invoke `sh -c`, quote values manually, or reinterpret spaces and shell metacharacters.

The child inherits the environment needed for controller and OpenCode authentication. The plugin must not copy environment variables into status buffers, notifications, or debug output.

When `server_ca_cert` resolves successfully, the plugin validates it as an absolute readable regular-file path and sets `SSL_CERT_FILE` only in the child environment used for controller commands that may contact OpenCode. The CA path is not placed in argv or routine notifications. Without this option, the child inherits the existing environment unchanged. Browser CA trust is not configured by the plugin.

### 8.2 Asynchronous Commands

Status and control commands run asynchronously and capture bounded standard output and standard error. Spawn failure, non-zero exit, malformed output, and callback failure must be reported through concise notifications.

The plugin must not parse human controller diagnostics to make workflow decisions. It may display a bounded sanitized diagnostic and direct the user to `SprintLoopProgress`.

### 8.3 Detached Start

`start()` launches `sprint-loop run` in a detached process group or equivalent Neovim 0.12 detached mode.

Required behavior:

- Return control to Neovim immediately after successful spawn.
- Do not send a termination signal merely because Neovim exits.
- Do not claim the controller survived until a later status query confirms `process_running: true`.
- Report immediate spawn failure clearly.
- If Neovim remains open and the controller exits, report its bounded result without interpreting a non-zero status as a workflow transition.
- Delegate duplicate-run and repository validation to the controller; do not implement a second workflow guard in Lua.

The detached-lifetime test must use a child process that survives the launching headless Neovim process and records independently observable completion. A mocked option assertion alone is insufficient.

### 8.4 Current Control Behavior

Sprint 3 exposes pause, resume, and stop but does not implement their controller semantics. Against the Sprint 2 controller they return `feature_not_implemented` and make no state or Git mutation.

The plugin displays that response accurately. It must not alter status, kill the controller, retry the command, or claim that a pause, resume, or stop occurred.

## 9. Additive Controller Status Contract

### 9.1 Scope

Sprint 3 adds fields to the stable schema-version-one status projection without removing or changing any Sprint 1 or Sprint 2 field.

The relevant no-run status excerpt remains:

```json
{
  "run_exists": false,
  "process_running": false,
  "active": null
}
```

For a persisted run with no active invocation, the active excerpt is:

```json
{
  "active": {
    "role": null,
    "invocation_id": null,
    "session_id": null,
    "status": null,
    "interaction": null
  }
}
```

For the active Sprint 2 probe, the active excerpt is:

```json
{
  "active": {
    "role": "auditor",
    "invocation_id": "0001-auditor",
    "session_id": "ses_example",
    "status": "running",
    "interaction": null
  }
}
```

The Sprint 3 plugin must also accept the future-compatible waiting projection defined by the V1 specification:

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

### 9.2 Controller Implementation Boundary

Sprint 3 controller code projects:

- `status: "running"` from the existing durable active invocation.
- `interaction: null` because the Sprint 2 probe cannot ask questions.
- Null status and interaction fields when no invocation is active.

Sprint 3 does not add `interaction` to `state.json`, add question events, permit the question tool, call OpenCode's question API, or produce a real `waiting_for_user` state. Those changes begin in Sprint 4.

The status schema remains version `1`; these are backward-compatible additive fields. Controller tests must prove all older required fields and meanings remain unchanged.

### 9.3 Plugin Validation

The plugin treats `status --json` as the sole source of workflow truth. It must:

- Require exactly one JSON object on standard output.
- Require status schema version `1` and a non-empty controller version.
- Validate every required stable top-level field before rendering.
- Validate conditional nested objects for no-run, inactive, running, and waiting projections.
- Require `question_count` to be a positive integer and reject booleans.
- Require `asked_at` to be a non-empty timestamp string suitable for display.
- Bound status output before JSON decoding and bound individual displayed strings.
- Ignore unknown additional object fields for forward compatibility.
- Reject missing required fields, contradictory nullability, and unsupported schema versions.
- Avoid rendering raw Lua table representations or tracebacks for malformed data.

The plugin does not read `state.json`, `events.jsonl`, invocation records, Git status, OpenCode APIs, or controller lock files directly.

## 10. Progress UI

`progress()` asynchronously obtains current JSON status and opens a disposable read-only scratch buffer in a centered floating window.

The view renders, when present:

- Sprint root and sprint identity.
- Workflow state and process-running status.
- Blocked or failure reason code and message.
- Active role, invocation ID, session ID, and active status.
- A prominent waiting-for-user line with question count and observation time.
- Local and pushed commit maps in deterministic repository-name order.
- Audit phase, current pre-CI round, maximum rounds, and remaining effort.
- CI status, attempt, and commit SHA.
- Implementation-cycle and CI-fix counters.
- Checklist counts and assessment time.
- Last event sequence, type, and timestamp.
- Controller version and state update time.

No-run status must produce a clear no-run view rather than an empty or error buffer.

UI rules:

- Use `buftype=nofile`, disable swap, and wipe the buffer when closed.
- Keep the buffer non-modifiable after rendering.
- Adapt width and height to the current editor dimensions.
- Provide buffer-local `q` and `Esc` close mappings.
- Add no global key mappings.
- Reinvoking progress replaces or refreshes the plugin-owned progress view without accumulating stale windows.
- Highlight blocked, failed, and waiting states prominently using standard highlight groups where practical.
- Never place server URLs, credentials, prompt content, result summaries, transcripts, question text, or answer text in the buffer.

The progress buffer is presentation only. Closing or editing around it must not alter controller state.

## 11. Background Status Watcher

### 11.1 Activation and Lifetime

The plugin maintains at most one watcher generation.

- Successful setup performs one asynchronous status query for the current resolved root.
- If setup finds `process_running: true`, it starts periodic observation for that root.
- A successful start or resume spawn activates a watcher generation bound to that action's resolved root.
- During start or resume discovery, the watcher may continue while the launched command is alive even if durable status is not yet available.
- If the launched command exits before an active run is observed, the watcher performs one final status query and then stops rather than polling indefinitely.
- Once an active run has been observed, the watcher stops after status reports no running controller.
- Reconfiguration cancels the prior timer and invalidates callbacks from the prior generation.
- Neovim exit closes plugin timers and handles without signalling the detached controller.

Polling uses a fixed bounded non-busy interval selected and documented by the implementation. It allows at most one status process in flight per watcher generation and does not create a controller checkpoint, event, or network request.

### 11.2 Interaction Notification

When status reports `active.status: "waiting_for_user"` with valid interaction metadata, the watcher:

1. Deduplicates by pending question request ID within the current Neovim process.
2. Emits one notification stating that the sprint loop needs user input.
3. Includes the active role or invocation ID when safe.
4. Directs the user to `:SprintLoopOpenSession`.

It must not include question text, options, answers, server URLs, or credentials. Repeated polls of the same request emit no additional notification. A newly started Neovim process may notify once for an already-pending request discovered by its setup-time query.

### 11.3 Watcher Failures

- Background polling failures must not mutate the loop or stop the controller.
- Report at most one warning per continuous failure episode; suppress repeated identical failures until a successful observation resets the episode.
- Malformed or unsupported status must not be treated as no-run or success.
- Ignore stale callback results from a replaced watcher generation.
- Never overlap polls merely to catch up after a delayed status command.

The watcher is not a general dashboard, workflow engine, CI poller, or persistence mechanism.

## 12. Active Session Opening

`open_session()` performs one asynchronous status query, then:

1. Requires `run_exists: true`.
2. Requires a non-empty active session ID.
3. Resolves `web_url` only after an active session is known.
4. Validates a credential-free absolute HTTP or HTTPS browser base.
5. Builds the active-session route.
6. Opens it through Neovim 0.12's browser-opening API.

The route is:

```text
<web-base>/<base64url(canonical-sprint-root)>/session/<encoded-session-id>
```

Encoding rules:

- Use the canonical `sprint_root` returned by status, not an independently guessed path.
- Encode the UTF-8 root bytes using RFC 4648 URL-safe base64 without padding.
- Percent-encode the session ID as one URL path segment.
- Normalize joining so a trailing slash on the web base does not create an empty path segment.
- Reject URL user-info, query strings, fragments, control characters, and unsupported schemes.
- Do not place OpenCode Basic-auth credentials in the URL.

The configured web base may contain a deployment path prefix if it otherwise passes validation; append the encoded route beneath that prefix.

Missing configuration, missing active session, malformed status, malformed web base, and browser-open failure produce actionable notifications and do not affect the controller.

## 13. Session Title Contract

Every controller-created Builder, Auditor, and CI Fixer session uses:

```text
[<multisprint>/<sprint>] <role> <sequence> <purpose>
```

Rules:

- Sequence is zero-padded to at least four decimal digits.
- Role is the canonical workflow role, not a provider or model name.
- Purpose is concise and identifies the invocation phase.
- Titles are descriptive and are not used as identity evidence.

The existing Sprint 2 execution probe already satisfies the contract:

```text
[foundation/1] auditor 0001 execution probe
```

Sprint 3 adds or adjusts tests and documentation needed to make this convention normative. It does not create an additional OpenCode session or rename an existing server session.

## 14. Notifications and Error Handling

Notifications must distinguish informational launch, warning, and error outcomes using standard Neovim levels.

Required error categories include:

```text
setup_required
invalid_setup
resolver_failed
invalid_resolved_value
invalid_server_ca_cert
process_spawn_failed
controller_command_failed
status_output_too_large
invalid_status_json
unsupported_status_schema
inconsistent_status
web_url_unavailable
invalid_web_url
active_session_unavailable
browser_open_failed
unsupported_neovim
```

Equivalent names are allowed if documentation and tests remain consistent. These are plugin diagnostics, not new controller reason codes.

Error rules:

- Keep messages concise and actionable.
- Bound captured output before retaining or displaying it.
- Prefer the controller's bounded standard-error diagnostic on non-zero exit, but do not parse it into workflow state.
- Avoid normal Lua tracebacks for expected setup, process, JSON, or browser failures.
- Never echo a rejected server or web URL verbatim when it may contain user-info, query data, or fragments.
- Do not open the progress buffer with partial or unvalidated status.

## 15. Security and Data Handling

- Construct every process invocation from an argument array.
- Never invoke a shell with root paths, executable values, URLs, session IDs, or controller output.
- Never persist plugin configuration, watcher state, status documents, or diagnostics.
- Never include server credentials in argv, browser URLs, notifications, buffers, tests, or documentation.
- Inherit OpenCode authentication through the controller process environment without inspecting or copying it.
- Pass an explicitly configured private CA only through the controller child environment, never argv or browser URLs.
- Bound process output and status JSON before decoding or display.
- Treat status strings as text, not executable commands, buffer commands, help tags, or format strings.
- Use synthetic URLs, paths, session IDs, questions, and credentials in tests.
- Never source or mutate live mkchad configuration or runtime state during development verification.
- Leave question and answer rendering to OpenCode clients.

## 16. Required Automated Tests

### 16.1 Setup and Public API

- Neovim 0.12 succeeds and an older-version fixture fails clearly.
- Setup requires `sprint_root` and `server_url` fields.
- String and function values work for every supported option.
- Synchronous and callback-style URL resolvers complete exactly once and enforce the resolver timeout.
- The executable defaults to `sprint-loop`.
- Missing, empty, wrong-type, control-character, and throwing resolver cases fail.
- Unknown setup keys fail.
- Repeated setup replaces configuration, commands, timers, and watcher generation without duplication.
- Every public method and command fails safely before setup.
- Public methods and commands delegate to identical behavior.

### 16.2 Process Execution

- Every argv list matches the documented CLI contract exactly.
- Spaces and shell metacharacters remain literal arguments.
- No shell process or shell command string is used.
- Commands return control to Neovim without blocking.
- Missing executable and spawn failure produce actionable errors.
- Non-zero exit and bounded stderr are rendered without workflow interpretation.
- Oversized stdout and stderr are bounded.
- Detached start survives the launching headless Neovim process.
- Neovim exit does not send a controller termination signal.
- A configured server CA certificate reaches the controller only through the child `SSL_CERT_FILE` environment and never argv.

### 16.3 Status Validation

- Complete no-run, inactive, running, waiting, paused, blocked, failed, stopped, and finished fixtures validate and render.
- Existing Sprint 1 and Sprint 2 fields remain required and retain their meaning.
- Sprint 3 controller output includes active status and interaction fields.
- Unknown additional fields are ignored.
- Unknown schema versions, missing fields, wrong types, invalid nullability, invalid counters, and malformed interaction fields fail.
- Duplicate JSON keys, trailing JSON values, non-finite values, invalid UTF-8, empty output, and oversized output fail.
- Status validation never performs a network request or reads controller persistence directly.

### 16.4 Progress UI

- Every documented status field renders deterministically.
- Repository commit maps use stable ordering.
- Reasons and waiting state are prominent.
- No-run has an explicit view.
- Buffer options prevent editing, swap, and persistence.
- `q` and `Esc` mappings are buffer-local.
- Repeated progress calls do not leak buffers or windows.
- Small editor dimensions produce a valid bounded float.
- Sensitive and excluded fields never render.

### 16.5 Watcher

- Setup discovers an already-running controller.
- Start and resume activate discovery without losing a pre-state startup interval.
- At most one watcher and one status process are active at a time.
- One pending request produces exactly one notification across repeated polls.
- A new request produces a new notification.
- A new Neovim process may notify once for an existing request.
- Resolution, controller exit, reconfiguration, and Neovim exit stop or replace observation correctly.
- Continuous status failures produce no notification storm and recover after success.
- Stale callbacks from a prior generation cannot notify or replace current state.
- The watcher never calls OpenCode, answers a question, or mutates workflow data.

### 16.6 Session Opening

- No-run and inactive-run cases fail without invoking a browser.
- Missing web URL fails only when session opening needs it.
- Canonical sprint root uses URL-safe unpadded base64.
- Session ID uses one-segment percent encoding.
- Trailing slash and supported path-prefix cases join deterministically.
- User-info, query, fragment, control character, empty host, and unsupported scheme fail.
- Browser success and failure are reported correctly.
- Browser URL contains no server credential.
- Browser opening does not claim to install or configure mkchad's private CA.

### 16.7 Controller Compatibility

- No-run status remains unchanged apart from the existing `active: null` projection.
- Inactive persisted status adds null status and interaction fields.
- Active Sprint 2 status reports `running` and null interaction.
- Human status remains accurate and does not expose new sensitive data.
- Existing status tests remain green after intentional snapshot updates.
- The execution-probe title matches the normative convention.
- State, event, invocation, Git, and OpenCode behavior remain unchanged.

### 16.8 Integration and Documentation

- A fake executable drives status success, malformed output, non-zero exit, delayed response, and detached-lifetime scenarios.
- Headless Neovim tests run without network, credentials, OpenCode, GitHub, or a browser.
- mkchad integration tests use a fresh disposable remote-branch clone and isolated XDG roots; the live checkout and managed server remain unchanged.
- Plugin README and help examples match the public API and commands.
- Parent controller documentation describes the additive status fields and current Sprint 3 limitations.
- `git diff --check` passes in both repositories.

## 17. Documentation Requirements

The implementation change must document:

- Neovim 0.12 requirement.
- Plugin installation and runtime-path setup.
- Required `setup()` call and every setup option.
- Public Lua methods and six commands.
- Dynamic callback resolution.
- Detached lifetime and the distinction between launch and confirmed running status.
- Progress buffer fields and close mappings.
- Background watcher behavior and deduplicated waiting notification.
- Active-session browser URL behavior.
- Credential-free server and web URL requirements.
- Current `feature_not_implemented` control responses.
- The non-interactive Sprint 2 probe and fixture-only waiting demonstration.
- Default headless tests and opt-in real-server demonstration.
- Generic and mkchad configuration examples without hard-coded mkchad APIs in plugin code.
- Private-CA subprocess configuration and the requirement to trust the mkchad CA separately in the browser.
- The prohibition on editing or testing against the live `~/.config/mkchad` environment.
- Plugin/controller compatibility expectations and troubleshooting.

The parent README must identify Sprint 3 accurately once implementation begins. It must not claim Builder, real question monitoring, commits, audits, CI, or recovery controls are implemented.

## 18. Acceptance Criteria

Sprint 3 is accepted when:

1. Neovim 0.12 loads the plugin with no runtime dependency beyond the controller executable.
2. Required setup and all public Lua methods are documented and tested.
3. All six V1 commands register and execute asynchronously.
4. Start constructs the exact argv and launches a controller process that survives Neovim exit.
5. Progress validates schema-version-one status and renders the complete supported projection in a disposable centered float.
6. Blocked, failed, and waiting conditions are prominent; no-run status is explicit.
7. The controller status projection adds active status and interaction fields without changing prior field meanings or durable state.
8. Setup and active actions drive one bounded non-overlapping watcher.
9. A controlled waiting fixture emits one notification per request and directs the user to the active session command.
10. The plugin never reads controller persistence or OpenCode question APIs and never answers a question.
11. Active-session opening builds the documented encoded route and invokes the browser safely.
12. The execution-probe session title follows the normative naming convention.
13. Pause, resume, and stop delegate faithfully and do not simulate unimplemented behavior.
14. Expected setup, process, JSON, watcher, and browser failures are actionable and do not produce ordinary tracebacks.
15. No process path uses shell interpolation or exposes credentials.
16. Callback-style mkchad URL resolution and private-CA child environment configuration work without calling mkchad's server ensure/start operation.
17. Default tests are deterministic, offline, credential-free, and browser-free.
18. Closing and reopening Neovim does not terminate the controller and can rediscover active status.
19. Plugin and parent documentation accurately distinguish implemented behavior from Sprint 4 and Sprint 7 work.
20. Required plugin and controller tests, formatting checks, and `git diff --check` pass.
21. Plugin changes are committed in the plugin repository before the parent submodule pointer is updated.

## 19. Exit Demonstration

The sprint review must demonstrate:

1. Install or load the plugin in Neovim 0.12 and call setup with explicit root and server resolvers.
2. Use `SprintLoopStart` to launch an installed controller against a disposable clean Sprint 2 fixture and an externally started supported OpenCode server.
3. Show that Neovim remains responsive while the execution probe runs.
4. Use `SprintLoopProgress` to show the active Auditor, invocation ID, session ID, `running` status, null interaction, and other stable fields.
5. Observe the OpenCode session title `[<multisprint>/<sprint>] auditor 0001 execution probe` in an ordinary OpenCode client.
6. Use `SprintLoopOpenSession` and verify the browser target contains the encoded canonical sprint root and exact session ID.
7. Close Neovim while the controller is active, reopen it, rerun setup, and confirm status is rediscovered without a second controller launch.
8. Observe the eventual Sprint 2 `blocked/execution_not_implemented` result accurately.
9. Run a controlled fake-status scenario that reports `waiting_for_user`; observe one notification across repeated polls and open the fixture session target.
10. Demonstrate malformed status, missing web URL, missing active session, browser failure, and non-zero controller command handling.
11. Invoke pause, resume, and stop against the current controller and show accurate `feature_not_implemented` diagnostics with no simulated transition.
12. Run the complete offline headless Neovim suite and the affected controller status tests.
13. Show that mkchad integration used a fresh disposable clone and isolated XDG roots, that URL resolution did not start the server, and that the live `~/.config/mkchad` checkout and managed runtime state are unchanged.

The live OpenCode portion is opt-in because it may require model credentials, network access, time, and cost. It must use synthetic server credentials inherited through the environment and must not retain provider output, transcripts, runtime records, browser history exports, or disposable fixtures in either source repository.

## 20. Handoff to Sprint 4

Sprint 3 must leave Sprint 4 with:

- A documented and tested Neovim 0.12 public API.
- Stable command delegation and detached controller lifetime.
- A complete progress renderer for current and future active interaction status.
- One ephemeral deduplicated status watcher.
- Exact active-session browser opening.
- Normative session titles visible in OpenCode clients.
- Additive controller status fields with no premature durable question state.
- No plugin-owned workflow logic or persistence.

Sprint 4 will replace the non-mutating execution probe with a real Builder, extend production invocation monitoring through OpenCode's documented question lifecycle, persist `waiting_for_user`, and implement controller-owned Git handoff. Sprint 3 must not preempt those decisions by calling question reply/reject APIs or inventing a second workflow state machine in Lua.
