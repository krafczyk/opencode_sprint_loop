# Controller V1 Sprint 2 Specification: OpenCode Execution Layer

## Document Status

This document defines the implementation scope and acceptance contract for Sprint 2 of the Sprint Loop Controller V1 plan.

The following documents are authoritative over this sprint specification:

- `docs/v1_final_software_specification.md`
- `docs/multi_sprint_plan.md`

The operating assumptions and audit priorities in `docs/threat_model.md` and `docs/audit_policy.md` apply to this sprint. They do not override an explicit requirement in either authoritative document.

If this document appears to conflict with an authoritative document, implement the authoritative behavior and correct this document in the same change.

## 1. Sprint Goal

Connect the controller to an explicitly supplied OpenCode server and execute one fresh, visible, non-mutating, structured agent invocation through a reusable `AgentRunner` boundary.

Sprint 2 adds:

- Strict server URL and authentication handling.
- OpenCode health, version, workspace, agent, provider, and model validation.
- A typed runner interface isolated from OpenCode HTTP details.
- Fresh session creation and a synchronous terminal-response lifecycle.
- Controller-side structured-result validation.
- Durable invocation metadata, prompt, result, and sanitized transcript records.
- Active invocation information in state and status.
- Timeout, cooperative interruption, abort, malformed-response, and partial-record handling.
- Deterministic fake-runner and fake-server tests.

Sprint 2 does not run a product Builder, accept a staged handoff, create Git commits, perform audit rounds, push branches, monitor CI, or implement workflow resume.

## 2. Sprint Outcome

For a valid clean sprint repository and a compatible OpenCode server rooted at that repository, `run`:

1. Performs mutation-free repository and server preflight.
2. Acquires exclusive run ownership and repeats local repository assumptions.
3. Persists the existing `initializing -> validating` foundation transitions.
4. Records the validated credential-free server identity.
5. Creates one fresh session using the configured Auditor agent and Auditor model.
6. Persists the session ID before submitting the prompt.
7. Runs a non-mutating execution probe with a strict structured-output schema.
8. Persists bounded invocation records and exposes the active session through status while it runs.
9. Verifies that neither repository was changed by the probe.
10. Ends in the intentional incremental-delivery state:

```text
blocked / execution_not_implemented
```

The final block means the execution layer succeeded but the Builder workflow begins in Sprint 4. It is not an OpenCode failure.

Invalid, unreachable, unauthenticated, unhealthy, unsupported, or wrong-workspace servers fail before runtime artifacts or sessions are created. A failure after durable run creation preserves all available evidence and enters an actionable blocked or failed state without discarding repository or session data.

## 3. Threat-Model Application

### 3.1 In-Scope Risks

Sprint 2 must handle ordinary, non-adversarial failures including:

- Malformed URLs and accidental credentials embedded in URLs.
- Missing or incorrect inherited server authentication.
- DNS, connection, HTTP, timeout, and service failures.
- Unhealthy servers, unsupported versions, and wrong default workspaces.
- Missing configured agents, providers, or models.
- Malformed, incomplete, oversized, or inconsistent OpenCode responses.
- Lost connections during session creation, synchronous prompt submission, or status-only abort confirmation.
- Invalid structured output and server-reported structured-output failure.
- Controller interruption before or during an invocation.
- Permission, short-write, disk, and atomic-replacement failures while recording invocation evidence.
- Conventional credentials present in server diagnostics, model output, tool output, or transcripts.
- Accidental repository changes made by a role intended to be non-mutating.

### 3.2 Excluded Adversarial Scenarios

Sprint 2 is not required to defend against:

- A compromised or deliberately malicious OpenCode server, model provider, kernel, Python runtime, or Git executable.
- A hostile local process racing validated filesystem or repository operations.
- Deliberate runtime-artifact substitution after successful validation.
- Forged Git object databases or same-SHA repository impersonation.
- Manual state, event, invocation-record, or Git changes timed during a controller critical section.

These exclusions do not permit controller-authored credential exposure, destructive Git behavior, silent server replacement, or treatment of ambiguous evidence as success.

### 3.3 Accepted Sprint 2 Limitations

- OpenCode does not document an idempotency key for session creation. A connection loss after the server accepts `POST /session` but before the controller receives the response may leave an unidentifiable orphan session. The controller must not retry session creation in that case; it records `session_creation_ambiguous` when durable state is available.
- Full server-loss grace periods, session recovery after process death, and resume with a replacement server are Sprint 7 work.
- `SIGKILL`, container loss, or host loss may prevent a final abort or metadata update. The session ID must already be durable before prompt submission so later diagnosis is possible.
- The controller validates configured model presence, not provider billing, quota, credential expiry, or successful inference before the probe.
- Transcript sanitization recognizes conventional credential patterns. Protection against secrets encoded specifically to evade those rules is outside the current trusted-user threat model.
- OpenCode `1.17.18` may reject its stored structured prompt through the
  message-list endpoint. Sprint 2 deliberately does not use that endpoint:
  the documented synchronous message response is the terminal evidence source.

## 4. Scope

### 4.1 Included

Sprint 2 includes:

1. Server URL parsing, normalization, and safe endpoint construction.
2. Inherited OpenCode HTTP Basic authentication.
3. A narrow synchronous `AgentRunner` protocol and OpenCode implementation.
4. One synchronous documented prompt request; status checks are reserved for
   bounded abort confirmation and server-sent events are not required.
5. Server and capability validation through documented endpoints.
6. One non-mutating Auditor execution probe.
7. Structured-output request and independent result validation.
8. Fresh session identity, title, and reuse checks.
9. Invocation timeout, abort, and cooperative interruption behavior.
10. Durable invocation directories and bounded artifacts.
11. Sanitized transcript reconstruction from the synchronous returned assistant
    message plus the exact persisted submitted prompt.
12. Sprint 2 state, event, transition, and status extensions.
13. Post-invocation repository non-mutation validation.
14. Fake runner, fake OpenCode server, and opt-in real integration coverage.
15. User-facing server setup, authentication, limitation, and troubleshooting documentation.

### 4.2 Explicitly Excluded

Sprint 2 must not implement:

- Product implementation prompts or Builder execution.
- Mutating-agent commit-message paths or staged handoff validation.
- Managed repository staging, commits, pushes, resets, stashes, or cleanup.
- Sprint repository checkpoint commits.
- Audit finding or checklist-assessment schemas.
- Pre-CI or final audit rounds.
- GitHub API or CI behavior.
- Functional pause, resume, or stop coordination.
- Server-unavailable grace-period recovery.
- Reattachment to or continuation of an interrupted session.
- Automatic cleanup of partial agent work or orphan sessions.
- Neovim plugin changes.
- Multiple managed repositories or parallel invocations.
- An OpenCode server launcher, registry, multiplexer, or database integration.

## 5. Implementation Constraints

### 5.1 Runtime and Dependencies

- Continue to support Python 3.11 or newer on Linux in the mkchad environment.
- Use Python standard-library HTTP and JSON facilities unless implementation demonstrates a concrete requirement for a new runtime dependency.
- The selected Sprint 2 direction is synchronous HTTP. The long prompt request
  runs in a daemon worker while the controller blocks on a bounded queue wait;
  normal execution has no observation polling or tight loop. Socket/event waits
  consume negligible CPU, while wall-clock timeout remains the configured
  monotonic invocation timeout. An async framework and SSE dependency are not
  required.
- Any added runtime dependency must be justified, pinned through the repository's reproducible installation approach, and documented before its checklist item is complete.
- Keep the default suite offline, deterministic, credential-free, and independent of a real OpenCode server or model provider.
- Use monotonic time for invocation deadlines and wall-clock UTC only for durable timestamps.
- Document public runner, transport, artifact, and result APIs with concise docstrings.

### 5.2 Suggested Source Boundaries

The exact module names may vary, but responsibilities must remain comparably narrow:

```text
src/opencode_sprint_loop/
|-- agent_runner.py       # OpenCode-independent protocol and typed outcomes
|-- opencode_runner.py    # OpenCode HTTP implementation and response adaptation
|-- invocations.py        # IDs, schemas, paths, and durable invocation artifacts
|-- security.py           # shared redaction and credential checks
|-- state.py              # Sprint 2 state validation
|-- events.py             # Sprint 2 event-history validation
|-- transitions.py        # guarded transitions and same-state observations
|-- status.py             # active invocation projection
`-- cli.py                # orchestration only
```

OpenCode response shapes must not leak into the workflow state machine or fake runner interface.

### 5.3 External Requests

HTTP requests must:

- Be built from a validated base URL and fixed controller-owned endpoint paths.
- Percent-encode every server-provided identifier as one URL path segment before endpoint construction.
- Never interpolate a credential into a URL.
- Send authentication only in an HTTP header created in memory.
- Use bounded connection/read timeouts.
- Reject redirects rather than forwarding credentials or silently changing origin.
- Bound response bytes before JSON decoding.
- Decode JSON strictly and reject duplicate object keys and non-finite values.
- Validate required response fields before they influence state.
- Avoid logging full response bodies, request headers, prompts, or transcript content.

## 6. Server URL and Authentication Contract

### 6.1 URL Rules

`run --server-url` must accept only an absolute `http` or `https` origin URL.

The URL must:

- Include a non-empty host.
- Use an explicit valid port or the scheme default.
- Have no username or password.
- Have no query string or fragment.
- Have an empty path or `/` only.
- Contain no control characters or invalid percent encoding.

The controller stores only a normalized credential-free origin, for example:

```text
http://127.0.0.1:4096
https://opencode.internal.example
```

Endpoint paths are joined only after validation. A rejected URL is never included verbatim in diagnostics. HTTP is acceptable for the current trusted local mkchad transport; documentation must require HTTPS and server authentication when traffic leaves a trusted local interface.

### 6.2 HTTP Authentication

Sprint 2 supports OpenCode's documented inherited environment variables:

- `OPENCODE_SERVER_PASSWORD`
- `OPENCODE_SERVER_USERNAME`, defaulting to `opencode` when a password is present and no username is supplied.

Rules:

- The password must never be accepted as a CLI argument or configuration field.
- An empty password is treated as absent.
- A username without a password is invalid authentication configuration.
- The controller creates the Basic `Authorization` header only in memory.
- Neither the username, password, encoded header, nor full environment is persisted or logged.
- A `401` response becomes `server_authentication_failed` without echoing response authentication data.
- Provider credentials and `PUT /auth/{providerID}` are outside Sprint 2.

### 6.3 `resume`

Sprint 2 applies the same local URL syntax and credential-placement rules to `resume --server-url`, then returns `feature_not_implemented` without contacting the server or mutating state. Full server validation and workflow continuation on resume remain Sprint 7 work.

## 7. OpenCode Server Preflight

### 7.1 Validation Order

Before any runtime directory, state, event, lock metadata, invocation record, or session is created, `run` must:

1. Complete the existing configuration and read-only Git preflight.
2. Parse and normalize the server URL.
3. Validate authentication configuration without exposing it.
4. Validate health and version.
5. Validate the server's default workspace against the canonical sprint root.
6. Validate all configured agent names.
7. Validate all configured provider/model pairs to the extent exposed by the supported API.

After acquiring run ownership, the controller reloads configuration and repeats the existing local repository checks before the first durable transition. It does not repeat network preflight while ownership is held but no run state exists; this preserves the stable no-run status contract and avoids hiding a slow network wait behind otherwise invisible ownership. If the service changes after successful mutation-free preflight, the first later OpenCode operation records an ordinary post-start service failure.

Network calls are never made while holding the short persistence lock.

Any preflight failure leaves no new worktree files, runtime artifacts, invocation directory, OpenCode session, index change, or commit.

### 7.2 Health and Version

The runner uses:

```text
GET /global/health
```

The response must be an object containing:

```json
{
  "healthy": true,
  "version": "1.17.18"
}
```

Sprint 2 initially supports release versions `>=1.17.0` and `<1.18.0`. Pre-release, malformed, older, and newer minor versions fail with `unsupported_server_version` until their documented API is reviewed and fixtures are updated deliberately.

The version range is an adapter compatibility policy, not a claim that the OpenCode health endpoint negotiates an API version.

### 7.3 Default Workspace

The runner uses `GET /path` without a `directory` query parameter or directory override header. Both returned `directory` and `worktree` paths must canonicalize to the canonical sprint repository root.

Supplying the sprint root as a request override is not sufficient because it would prove only that the server can open the directory, not that the supplied server is rooted there.

All later Sprint 2 instance requests use the validated default server context. A session response whose directory does not canonicalize to the sprint root fails before prompt submission.

### 7.4 Agents

The runner uses:

```text
GET /agent
```

Every configured `builder`, `auditor`, and `ci_fixer` agent name must appear exactly once as an invocable agent. Sprint 2 executes only the configured Auditor. Missing, duplicate, or malformed records fail closed.

The controller does not infer availability from local agent files alone and does not silently substitute a built-in or differently named agent.

### 7.5 Providers and Models

The runner uses documented provider/configuration endpoints, including:

```text
GET /config/providers
GET /provider
```

Each configured `provider/model` value is split at the first `/` into OpenCode `providerID` and `modelID`. The provider must be connected/configured and the model must appear in its model map. A malformed or unavailable capability response fails closed.

For supported OpenCode `1.17.x`, `GET /config/providers` supplies configured
provider records under `providers`, while `GET /provider` supplies catalog
records under `all` and connected provider IDs under `connected`. Provider
records advertise models as object maps. The controller validates these
documented collection shapes and requires every configured provider/model in
both advertised record sets and the connected-ID collection.

This check proves only advertised configuration. The real probe is the evidence that the selected Auditor model accepted and completed the request.

## 8. `AgentRunner` Contract

### 8.1 Boundary

Define an OpenCode-independent protocol with semantic operations equivalent to:

```python
class AgentRunner(Protocol):
    def validate_server(self, request: ServerValidationRequest) -> ValidatedServer: ...
    def create_session(self, request: InvocationRequest) -> CreatedSession: ...
    def execute_prompt(self, session: CreatedSession, request: InvocationRequest) -> InvocationObservation: ...
    def observe_status(self, session: CreatedSession) -> str | None: ...
    def abort(self, session: CreatedSession) -> AbortObservation: ...
```

Equivalent decomposition is allowed, but the controller must be able to:

- Validate a server without creating a session.
- Create a session without submitting a prompt.
- Persist the returned session ID before prompt submission.
- Execute one synchronous prompt until a monotonic deadline without retry.
- Request abort separately.
- Retain the terminal response itself as transcript evidence; status checks are
  only permitted after abort.

The protocol must use typed controller concepts and stable normalized errors, not raw `urllib`, OpenCode SDK, or HTTP response objects.

### 8.2 Fake Runner

A deterministic fake runner must support scripted:

- Server validation success and each failure category.
- Unique and reused session IDs.
- Immediate and delayed completion.
- Blocked worker completion and bounded abort status observations.
- Valid, blocked, failed, malformed, and oversized structured results.
- Transport failure at every lifecycle operation.
- Timeout and abort acknowledgement/non-acknowledgement.
- Complete, credential-bearing, malformed, and oversized transcripts.

State-machine tests must use the fake runner. Real OpenCode access remains opt-in.

## 9. Sprint 2 Execution Probe

### 9.1 Role and Model

The probe uses:

- Role: configured `agents.auditor`.
- Model: configured `models.auditor`.
- A fresh top-level session with no parent or fork.

The Auditor is selected because Sprint 2 requires a non-mutating invocation. The prompt explicitly prohibits repository, shell, web, task, MCP, and external-mutation tools. OpenCode `1.17.18` applies matching session permissions in order with the last match winning. The create request and returned effective permissions must therefore be exactly this ordered two-rule set:

```json
[
  {"permission":"*","pattern":"*","action":"deny"},
  {"permission":"StructuredOutput","pattern":"*","action":"allow"}
]
```

The first rule denies every permission; the second narrowly re-allows only the
built-in `StructuredOutput` mechanism required to return the JSON-schema result.
No shell, repository, web, task, MCP, or external tool is permitted. The
controller still verifies repository state afterward rather than trusting either
control alone.

### 9.2 Session Title

The title is deterministic and recognizable:

```text
[<multisprint>/<sprint>] auditor <sequence> execution probe
```

For example:

```text
[authentication/3] auditor 0001 execution probe
```

Titles are descriptive only. They are not identifiers and need not be globally unique.

### 9.3 Prompt

The persisted and submitted prompt must:

- Identify the sprint and invocation.
- State that this is an execution-layer probe, not a sprint audit.
- Instruct the role not to use repository, shell, web, task, MCP, or external-mutation tools and not to modify any repository or external service.
- Permit only the built-in structured-output mechanism required by the OpenCode JSON-schema response format.
- Require the controller to enforce the exact ordered deny-plus-StructuredOutput-allow session permission set rather than relying only on prompt compliance.
- Require the exact structured result from Section 9.4.
- Contain no server credential, environment dump, provider credential, or server URL.
- Be deterministic for the same sprint identity and invocation sequence apart from explicitly documented identifiers.

Product specifications, checklist contents, managed source files, prior findings, and commit-message paths are not relevant probe inputs and must not be embedded merely to anticipate later sprints.

### 9.4 Structured Result

The OpenCode request uses `format.type = "json_schema"` and `additionalProperties: false`. The controller does not submit a corrective prompt, create another session, or otherwise retry an invalid structured result. A server-reported `StructuredOutputError` is treated as `invalid_agent_result`.

The result shape is:

```json
{
  "schema_version": 1,
  "status": "completed",
  "summary": "OpenCode execution probe completed.",
  "checks": [],
  "blocking_reason": null
}
```

Rules:

- Exact top-level fields are required.
- `schema_version` is integer `1`; booleans are invalid.
- `status` is `completed`, `blocked`, or `failed`.
- `summary` is a non-empty string of at most 4096 UTF-8 bytes.
- `checks` is an array with at most 100 entries. A check contains exact fields `command`, `result`, and `details`; `command` is at most 4096 UTF-8 bytes, `result` is at most 256 UTF-8 bytes, and nullable `details` is at most 4096 UTF-8 bytes.
- The probe must return an empty `checks` array because it was instructed not to execute substantive verification or mutation tools. OpenCode's built-in structured-output mechanism is not a reported check.
- `blocking_reason` is null for `completed` and a non-empty string of at most 4096 UTF-8 bytes for `blocked` or `failed`.
- The controller independently validates the structured object returned by OpenCode. Free-form assistant prose is never interpreted as success.
- Missing structured output, OpenCode `StructuredOutputError`, schema mismatch, unknown fields, or a recognized credential in any result string becomes `invalid_agent_result`.
- Any permission request or recorded tool part other than the internally injected `StructuredOutput` call becomes `unexpected_probe_tool` and prevents probe success.

Only `completed` advances to the successful Sprint 2 placeholder block. `blocked` and `failed` preserve the result and enter a corresponding actionable blocked state.

## 10. OpenCode Invocation Lifecycle

### 10.1 Required Endpoints

Sprint 2 uses the documented operations:

```text
POST /session
GET  /session
POST /session/<session-id>/message
GET  /session/status
POST /session/<session-id>/abort
```

The implementation must not access OpenCode database files or depend on undocumented local storage.

### 10.2 Fresh Session Creation

- Immediately before creation, the controller obtains a bounded `GET /session` snapshot of existing session IDs in the validated default workspace.
- Every controller invocation calls `POST /session` exactly once unless no session request was sent.
- Session creation has no automatic retry after an ambiguous transport failure.
- The create request supplies the exact title and the exact ordered deny-plus-StructuredOutput-allow permission ruleset and supplies no parent ID.
- The response must contain a non-empty bounded session ID, the exact requested title, a null/absent parent ID, the same exact ordered effective permission ruleset, and a directory matching the sprint root. A deny-only set, reordered set, added rule, or alias conflict fails before prompt submission.
- The session ID must not match the pre-creation session snapshot or any session ID already present in this run's invocation metadata.
- A duplicate or reused ID fails with `non_fresh_session` before prompt submission.
- No `parentID`, fork, or previous conversation is used.

### 10.3 Session-ID Durability Ordering

The controller must use this order:

1. Allocate the invocation ID and atomically persist initial metadata and `prompt.md`.
2. Create the fresh OpenCode session.
3. Persist an `agent.started` event and state containing the invocation and session IDs.
4. Atomically update invocation metadata with the session ID.
5. Start the one synchronous prompt request in a daemon worker.

If step 3 fails, the controller must not submit the prompt and makes a best-effort abort of the empty session. If step 4 fails, the state/event pair still identifies the session; the controller must not submit the prompt and attempts abort. No failure in these steps permits creation of another session in the same invocation.

### 10.4 Synchronous Prompt Submission

- `POST /session/<id>/message` is non-idempotent and is sent exactly once in a
  daemon worker. The controller main thread waits on a queue with a bounded
  timeout, checks cancellation and the monotonic deadline, and never waits for
  worker shutdown. A late worker result cannot mutate persistence or launch a
  new invocation.
- The returned object must be the configured assistant terminal response with
  bounded message, parent, and documented session IDs, with its session ID
  exactly equal to the created session, matching role/agent/provider/model, no
  error, and one unconflicted structured-output value. Top-level and `info`
  aliases for role, message ID, session ID (`sessionID` and supported
  `session_id`), error, structured output, route identity, and supported parent
  spellings reconcile exactly. Every retained part must carry documented
  `sessionID` and `messageID` fields exactly binding it to its containing
  message. Contradictory duplicates, missing/wrong associations, permission
  requests, forbidden tools, malformed parts, or absent output fail closed.
- The controller retains the returned assistant message and parts exactly as
  bounded external evidence. It reconstructs the sole user record from the
  exact already-persisted prompt and the returned assistant parent ID. It never
  calls the message-list endpoint for success or transcript capture.
- The total invocation deadline is `limits.invocation_timeout_seconds`, measured with monotonic time. The daemon worker timestamps normal response and exception completion with monotonic time. After dequeue, the controller re-arbitrates that timestamp against the deadline and cancellation timestamp: completion must be strictly before both boundaries. A response completed before a later cancellation remains acceptable; a response at or after either boundary is ignored and cannot reach persistence.
- Ordinary network failure during the invocation does not receive Sprint 7's server-unavailable grace period. It interrupts this invocation and preserves available evidence.
- An ambiguous synchronous request outcome leaves terminal state uncertain.
  Once the session ID is known it follows the bounded abort path in Section
  10.5; the prompt is never retried.

### 10.5 Abort and Interruption

Sprint 2 treats catchable `SIGINT` and `SIGTERM` as cooperative cancellation requests. Signal handlers record a cancellation request; they do not perform HTTP or persistence operations directly. The orchestration path finishes any active atomic persistence operation and then follows this sequence on timeout, cancellation, ambiguous prompt submission, or post-creation transport failure while terminal session state is uncertain:

1. Request `POST /session/<id>/abort` once.
2. Set one monotonic confirmation deadline and use only `GET /session/status`
   checks, at most once per second, sleeping or blocking between checks. Every
   check receives that same deadline; only `idle` confirms cancellation and an
   absent status entry is not confirmation.
3. Retain only a synchronous response that arrived before cancellation; never
   fetch the message-list endpoint.
4. Record `agent.interrupted` and clear active invocation state when persistence is possible.
5. Enter blocked with `invocation_timed_out` or `invocation_interrupted`.
6. Exit `130` for `SIGINT`, `143` for `SIGTERM`, or the documented non-zero blocked-run code for timeout.

An abort response is acknowledgement only when it is the documented exact JSON boolean; its `true` or `false` value is preserved. It is not proof of prior activity or complete cancellation. Failure to confirm abort is recorded in the interruption event and diagnostics without discarding the known session ID.

`pause` and `stop` remain mutation-free `feature_not_implemented` commands in Sprint 2 and do not abort the active session. Their coordinated boundaries remain Sprint 7 work.

A catchable signal before durable run creation exits with the corresponding conventional signal status and creates no runtime artifacts. A second signal or an uncatchable process loss may prevent orderly abort and persistence; the already-durable session ID remains the recovery evidence once prompt submission has begun.

A cancellation received after durable run creation but before a session exists requires no abort. The controller finalizes any already-created planned metadata, transitions directly to `blocked/invocation_interrupted`, and exits with the signal status. Once a successful create response makes the session ID known, the controller persists the ID and performs best-effort abort even if cancellation arrived before `agent.started` could otherwise complete. If cancellation coincides with a session-create request whose outcome is unknown, `session_creation_ambiguous` remains the durable reason because the controller cannot safely assert whether a session exists.

## 11. Run Flow, States, and Events

### 11.1 Sprint 2 Flow

```text
no run
  -> mutation-free repository and server preflight
  -> acquire ownership and repeat preflight
  -> initializing / run.started
  -> validating / state.entered
  -> validating / server.validated
  -> validating / agent.started
  -> validating / agent.completed
  -> validating / repository non-mutation verified
  -> blocked / execution_not_implemented
```

Failure after `agent.started` uses `agent.completed` for a valid terminal agent result or `agent.interrupted` for timeout, transport interruption, abort, or missing terminal evidence, followed by `run.blocked` when persistence remains available.

### 11.2 Same-State Events

Sprint 2 adds guarded same-state observations while remaining in `validating`:

| Source | Destination | Event | Purpose |
| --- | --- | --- | --- |
| `validating` | `validating` | `server.validated` | Persist normalized server identity and version. |
| `validating` | `validating` | `agent.started` | Persist invocation and session identity before prompt submission. |
| `validating` | `validating` | `agent.completed` | Persist valid terminal result status and clear active invocation. |
| `validating` | `validating` | `agent.interrupted` | Persist interruption evidence and clear active invocation. |

These are durable state updates, not free-form event appends. They use the existing event-first/state-second ordering and persistence lock. Network calls and transcript processing occur outside that lock.

Event payloads contain only bounded identifiers, statuses, prior state, and safe reason metadata. They do not contain prompts, result summaries, transcript content, HTTP bodies, headers, or credentials.

The exact Sprint 2 payloads are:

```json
{"previous_state":"validating","server_version":"1.17.18"}
{"previous_state":"validating","invocation_id":"0001-auditor","role":"auditor","session_id":"ses_example"}
{"previous_state":"validating","invocation_id":"0001-auditor","role":"auditor","session_id":"ses_example","result_status":"completed"}
{"previous_state":"validating","invocation_id":"0001-auditor","role":"auditor","session_id":"ses_example","interruption":{"code":"invocation_timed_out","message":"OpenCode invocation exceeded its configured timeout.","details":{}},"abort_acknowledged":true,"abort_confirmation":"idle"}
```

These correspond in order to `server.validated`, `agent.started`, `agent.completed`, and `agent.interrupted`. Exact fields are required. `abort_acknowledged` is boolean or null. `abort_confirmation` is `idle` or null and records whether bounded status-only post-abort confirmation was actually obtained. The safe interruption object uses the existing reason shape but is named `interruption` because same-state events leave `state.reason` null. Identifier bounds from Section 14 apply. `result_status` is `completed`, `blocked`, or `failed`.

### 11.3 Terminal Classification

- `blocked/execution_not_implemented`: the Sprint 2 probe completed and repository non-mutation was verified.
- `blocked/<specific external or invocation reason>`: ordinary service, agent, timeout, record, or repository postcondition failure after run persistence began.
- `failed/internal_error`: unexpected controller defect when best-effort durable failure persistence succeeds.
- Persistence corruption or inability to establish a coherent final state fails closed and is never represented as probe success.

## 12. Sprint 2 State Contract

State schema version remains `1`. No migration or silent rewrite of existing Sprint 1 state is permitted. Valid Sprint 1 `blocked/execution_not_implemented` histories remain readable by status and continue to cause `run_already_exists`.

### 12.1 Server State

After `server.validated`:

```json
{
  "server": {
    "url": "http://127.0.0.1:4096",
    "version": "1.17.18"
  }
}
```

Both values are null before validation and non-empty after validation. The URL is the normalized credential-free origin.

### 12.2 Active Invocation State

While a session is active:

```json
{
  "active_invocation": {
    "invocation_id": "0001-auditor",
    "sequence": 1,
    "role": "auditor",
    "model": "provider/strong-model",
    "session_id": "ses_example",
    "status": "running",
    "started_at": "2026-07-13T12:00:00Z"
  }
}
```

Rules:

- The exact fields above are required while active.
- Sequence is a positive integer and agrees with the invocation directory.
- Role and model agree with validated configuration.
- Session and invocation IDs are non-empty bounded strings without control characters.
- Status is exactly `running`. It begins at `agent.started` and remains `running` while prompt submission, observation, or abort confirmation is in progress.
- `active_invocation` is null before `agent.started` and after `agent.completed` or `agent.interrupted`.
- The final Sprint 2 blocked state has null `active_invocation` when interruption persistence succeeded.

All Sprint 1 commit, audit, CI, counter, checklist, control, and terminal-result constraints remain unchanged.

## 13. Invocation Records

### 13.1 Paths and IDs

The first invocation uses sequence `1` and directory `0001-auditor`:

```text
invocations/<multisprint>/<sprint>/0001-auditor/
|-- metadata.json
|-- prompt.md
|-- result.json
`-- transcript.json
```

Invocation sequences are positive, monotonically increasing within the configured sprint, and formatted with at least four decimal digits. Existing invocation directories and immutable artifacts are never overwritten or reused. `metadata.json` is the sole lifecycle artifact intentionally replaced in place through validated atomic replacement for the currently owned invocation. Paths are derived only from validated sprint identity, sequence, and configured role.

### 13.2 Metadata

`metadata.json` has this exact schema-version-one shape:

```json
{
  "schema_version": 1,
  "run_id": "<uuid>",
  "invocation_id": "0001-auditor",
  "sequence": 1,
  "purpose": "execution_probe",
  "role": "auditor",
  "model": "provider/strong-model",
  "session_id": "ses_example",
  "server_version": "1.17.18",
  "input_commits": {"backend": null},
  "status": "completed",
  "created_at": "2026-07-13T12:00:00Z",
  "started_at": "2026-07-13T12:00:01Z",
  "completed_at": "2026-07-13T12:00:05Z",
  "result": {"available": true, "status": "completed"},
  "transcript": {"status": "complete", "truncated": false},
  "error": null
}
```

Exact fields are required. Unknown fields are rejected in schema version 1. `run_id` is the current run UUID. Identity and categorical strings use the Section 14 bounds and contain no control characters. Timestamps are nullable RFC 3339 UTC values: `started_at` becomes non-null when a validated session ID is known, and `completed_at` becomes non-null only for a terminal metadata status. `session_id` is null whenever no validated session ID was obtained, including planned state, definitive create rejection, and ambiguous creation.

`input_commits` contains exactly the configured repository key with null value in Sprint 2. `result.available` is boolean; `result.status` is null when unavailable and otherwise matches the validated agent status. `transcript.status` is `pending`, `complete`, `truncated`, or `unavailable`; `transcript.truncated` is true exactly for `truncated`. `error` is null or an exact object containing non-empty bounded `code` and `message` strings and no external response body.

Lifecycle status is one of `planned`, `session_created`, `running`, `completed`, `blocked`, `failed`, `timed_out`, or `interrupted`. `planned` has no session/start/completion values. `session_created` and `running` have session and start values but no completion value. All remaining statuses are terminal and require `completed_at`; `completed`, `blocked`, and agent-reported `failed` require a valid result, while infrastructure `failed`, `timed_out`, and `interrupted` may have no result. A terminal infrastructure `failed` record may also have null session and start values when session creation was definitively rejected or remained ambiguous. Metadata updates are atomic replacements and never silently remove a known session ID.

### 13.3 Prompt

`prompt.md` is the exact sanitized UTF-8 prompt submitted to OpenCode, ending in a newline. It is written before session creation and is not changed afterward.

### 13.4 Result

`result.json` contains the exact independently validated, credential-free structured result from Section 9.4, serialized deterministically with a trailing newline. It is written only when a valid structured result exists; the controller must not fabricate an agent result after transport or schema failure. A schema-valid result containing a recognized credential is invalid, is not written, and leaves only sanitized transcript/error evidence.

A successful Sprint 2 invocation requires `result.json`. Failed or interrupted invocations identify absence of a valid result in metadata.

### 13.5 Transcript

`transcript.json` is a controller-owned bounded opaque reconstruction from the
synchronous `POST /session/<id>/message` response:

```json
{
  "schema_version": 1,
  "session_id": "ses_example",
  "format": "opencode-messages-json-v1",
  "sanitized": true,
  "truncated": false,
  "original_bytes": 2,
  "content": "[]"
}
```

Exact wrapper fields are required. `content` is a UTF-8 string containing a deterministic canonical JSON representation of the validated OpenCode message array after recursive redaction of recognizable credentials in both object keys and values. A collision introduced by key redaction, or later key bounding, fails transcript capture rather than silently dropping a field. Array ordering remains the server response order; object keys inside the opaque representation are sorted. Unknown OpenCode message and part fields may remain inside `content` but do not become fields in the controller-owned wrapper schema. `original_bytes` is the non-negative byte length of the complete sanitized canonical representation before total-transcript truncation.

The controller recursively replaces recognized credential values with the exact marker `[REDACTED]` before canonical serialization. If the complete wrapper would exceed the transcript limit, it truncates `content` at a valid UTF-8 code-point boundary to the largest deterministic prefix that permits the final wrapper to remain within the limit, appends `\n[TRUNCATED]`, and sets `truncated` true. Truncated `content` is evidence text and need not itself remain parseable JSON; untruncated `content` must parse as the complete message array. Per-string limits are applied before canonical serialization using the same `[TRUNCATED]` marker, and any such truncation also sets the wrapper flag. Redaction precedes per-string and total truncation.

Before opaque serialization, the controller validates the documented message/part envelope needed to associate the terminal response and detect tool use. The assistant response is retained unchanged; its parent ID becomes the reconstructed user record ID and binds that record to the exact persisted prompt. The reconstructed user record and text part carry that exact parent/message ID and the created session ID. For a successful probe the assistant's documented session ID must equal the created session and it requires configured Auditor/provider/model identity. Every retained part must carry matching documented `sessionID`/`messageID` association fields. Every `type: "tool"` part must carry a bounded documented `tool` string; a supported `name` alias is consistency-only and cannot replace missing `tool`. Only exact `StructuredOutput` is permitted (`StructuredOutputError` remains invalid output); missing, malformed, conflicting, or shell-like tool identities fail closed. The controller does not invoke `opencode export`, the message-list endpoint, or OpenCode local storage.

Transcript capture is required after successful completion. On failure or interruption it is best effort, and metadata records `complete`, `truncated`, or `unavailable`.

### 13.6 Artifact Safety

Invocation artifact writes must:

- Use controller-derived paths beneath the expected invocation directory.
- Reject symlinks, non-regular files, and unsafe existing path components.
- Use restrictive permissions equivalent to `0600` for files.
- Serialize and validate complete content before replacement.
- Write through same-directory temporary files, flush, `fsync`, atomically replace, and sync the directory where supported.
- Avoid holding the persistence lock during large prompt, result, or transcript writes.
- Never broad-stage or commit invocation artifacts in Sprint 2.

### 13.7 Cross-Record Consistency and Terminal Ordering

Readers must cross-validate invocation records rather than validating each artifact independently:

- Metadata `run_id` equals state and every related event run ID.
- Directory name, metadata invocation ID/sequence/role, active state, and related event payloads agree.
- Metadata role/model and server version agree with validated configuration and state.
- Every known session ID agrees across metadata, active state, events, transcript, and endpoint observations.
- Outside the documented write-ahead prefixes below, `result.json` exists if and only if `metadata.result.available` is true, and its status equals metadata and `agent.completed.result_status` when that event exists.
- Outside a documented transcript-before-metadata prefix, `metadata.transcript.status` `complete` or `truncated` requires `transcript.json`; `complete` requires `truncated: false`, and `truncated` requires `truncated: true`. `unavailable` requires no transcript file, and `pending` normally precedes transcript creation.
- Invocation path, metadata purpose, and artifact schemas agree with the `execution_probe` contract.
- A terminal `agent.completed` or `agent.interrupted` event requires terminal metadata for the same invocation. `agent.completed` additionally requires every artifact claimed by metadata.

Terminal completion uses this order:

1. Write valid immutable `result.json` when available.
2. Write immutable sanitized `transcript.json` when captured.
3. Atomically replace metadata with the terminal artifact availability and status.
4. Persist `agent.completed` or `agent.interrupted`, clearing active invocation state.
5. Persist the final actionable `run.blocked` transition.

Failure before a valid result skips step 1. Transcript failure or unavailability is reflected in terminal metadata before step 4. The controller never appends a terminal agent event whose metadata claims a missing or contradictory artifact.

Process death or persistence failure may leave a prefix of this ordering. Immutable result/transcript artifacts may therefore exist while metadata is still non-terminal, or terminal metadata may exist while state still shows the invocation active. These prefixes are interruption evidence, never completion evidence. Readers accept only documented ordering prefixes while the owning process is active; after ownership is gone they report the nonterminal/interrupted state without promoting it to success. An impossible combination, a terminal agent event ahead of required metadata/artifacts, or any identity/status disagreement fails closed as `inconsistent_invocation_record`. Sprint 2 does not repair, delete, replay, or synthesize missing invocation records automatically.

## 14. Bounds and Sanitization

### 14.1 Hard Bounds

Sprint 2 must enforce at least these decoded or encoded limits before unbounded allocation or persistence:

| Data | Limit | Behavior |
| --- | --- | --- |
| One HTTP response body | 8 MiB | Fail the operation before JSON decoding beyond the limit. |
| Prompt | 1 MiB UTF-8 | Reject before session creation. |
| Structured result | 1 MiB JSON | Treat as `invalid_agent_result`. |
| Metadata | 1 MiB JSON | Fail artifact persistence. |
| Transcript | 8 MiB JSON | Deterministically redact/truncate and mark `truncated`. |
| One string field retained in transcript | 1 MiB UTF-8 | Truncate with an explicit marker. |
| Session ID, invocation ID, role, model, status | 1024 UTF-8 bytes each | Reject malformed response or artifact. |

An implementation may choose smaller documented limits. It must not silently choose larger limits without updating this specification.

### 14.2 Credential Handling

- Server URL, state, events, metadata, results, transcripts, prompts, and diagnostics must not contain recognizable credentials.
- Controller-authored prompt and metadata content is rejected before persistence if it contains a recognized credential.
- Credential-bearing structured results are rejected as `invalid_agent_result` and are not persisted in `result.json`.
- Other external response and transcript strings are redacted before persistence because rejecting all evidence would impede diagnosis.
- Redaction covers URI user-info for every scheme, URL query values and fragments regardless of key name, HTTP authorization values, common secret-bearing key names, and supported provider token patterns including current OpenAI project, Anthropic, OpenRouter, Google, GitLab (`glpat-`, `glcbt-`, `glptt-`, `glrt-`, `glimt-`, `glsoat-`, `gldt-`, `glrtr-`, `glft-`, `glagent-`, `glwt-`, `glffct-`, and `gloas-`), Hugging Face, Slack, GitHub (including variable-length stateless `ghs_<APPID>_<JWT>` installation tokens), and AWS forms.
- Redaction is recursive and applies before size-based truncation.
- Logs and errors summarize response type and endpoint operation; they do not include full bodies.
- Tests use synthetic credential values only.

## 15. Repository Non-Mutation Postcondition

Before creating the session, the controller captures the validated sprint and managed repository identities needed to detect ordinary changes. After transcript/result capture and before declaring probe success, it verifies:

- Sprint and managed HEADs and branches are unchanged.
- The managed repository index and worktree remain clean and unchanged.
- No managed untracked files appeared.
- The sprint repository has no changes except exact controller-owned Sprint 2 runtime artifacts.
- The managed submodule gitlink remains unchanged.
- No mutating Git operation began and no new in-progress Git operation state appeared.

Unexpected changes produce `unexpected_agent_repository_change`. The controller must preserve them exactly and must not reset, stash, stage, clean, commit, or otherwise repair them. The final diagnostic identifies the affected repository and directs the user to inspect the worktree.

Controller-owned runtime paths must be enumerated narrowly. A broad allowance for all files under `info/` or `invocations/` must not conceal unrelated changes.

## 16. Error Model

Sprint 2 adds stable reason codes including:

```text
invalid_server_url
invalid_server_authentication
server_unavailable
server_authentication_failed
server_unhealthy
unsupported_server_version
server_api_incompatible
wrong_server_workspace
configured_agent_unavailable
configured_model_unavailable
malformed_server_response
server_response_too_large
session_creation_failed
session_creation_ambiguous
non_fresh_session
prompt_submission_failed
invocation_timed_out
invocation_interrupted
invocation_failed
invalid_agent_result
unexpected_probe_tool
transcript_capture_failed
invocation_record_failed
inconsistent_invocation_record
unexpected_agent_repository_change
```

Equivalent more-specific codes are allowed only when documented and tested. They must not collapse expected external failures into `internal_error`.

Error behavior:

- Before durable run creation, return non-zero with an actionable sanitized diagnostic and no mutation.
- After durable run creation, persist available invocation evidence and an actionable blocked reason when the state/event pair remains writable.
- Use `failed/internal_error` only for unexpected controller defects, with best-effort persistence.
- Preserve the original evidence when persistence is corrupt or inconsistent rather than overwriting it with a cleaner-looking failure.
- Never retry a non-idempotent operation when its outcome is ambiguous.

## 17. Status Contract

The stable Sprint 1 JSON envelope remains unchanged. While an invocation is active, populate:

```json
{
  "active": {
    "role": "auditor",
    "invocation_id": "0001-auditor",
    "session_id": "ses_example"
  }
}
```

Before and after the active invocation, these three fields are null. Do not expose the server URL, credentials, prompt, result summary, transcript, or raw error body in status.

Human status adds active role, invocation ID, and session ID when present. Status remains local, read-only, and independent of OpenCode availability. It does not poll or validate the server and remains readable while the controller owns the run lock, apart from the short persistence critical section.

The last event may be `server.validated`, `agent.started`, `agent.completed`, `agent.interrupted`, or `run.blocked` according to the latest durable boundary.

## 18. Control and Recovery Boundaries

- `pause` and `stop` continue to return mutation-free `feature_not_implemented` results.
- `resume` validates local URL syntax and then returns mutation-free `feature_not_implemented`.
- Sprint 2 does not resume its final placeholder block or interrupted invocations.
- A clean interrupted session may be rerun only after Sprint 7 defines recovery and a new fresh-session policy.
- Dirty interrupted work remains untouched and must never be interpreted as a successful probe.
- The `server_unavailable_grace_seconds` configuration value remains reserved until Sprint 7; Sprint 2 does not silently assign it partial semantics.

## 19. Required Automated Tests

### 19.1 URL and Authentication

- Valid HTTP and HTTPS origins normalize deterministically.
- Missing host, unsupported scheme, path, user-info, query, fragment, invalid port, control character, and malformed percent encoding fail.
- Username without password fails safely.
- Password-only authentication uses default username in memory.
- Authentication headers are sent to the configured origin and never redirected.
- Credentials are absent from argv, state, events, metadata, prompts, results, transcripts, status, and diagnostics.

### 19.2 Server Validation

- Healthy supported server succeeds.
- Connection refusal, timeout, TLS error, `401`, `403`, `404`, `500`, redirect, unhealthy response, malformed JSON, duplicate key, oversized body, and invalid field type fail distinctly.
- Versions below `1.17.0`, at or above `1.18.0`, pre-release, and malformed versions fail.
- Wrong `directory`, wrong `worktree`, and path aliases that do not canonicalize correctly fail.
- Missing, duplicate, or malformed configured agents fail.
- Missing connected provider or model fails.
- Every preflight failure creates no runtime artifact or session and leaves Git unchanged.
- No-run ownership covers only bounded local revalidation; later service changes become recorded post-start failures.

### 19.3 Runner and Lifecycle

- The controller uses a fake through the `AgentRunner` boundary.
- Every invocation creates one new top-level session and a deterministic title.
- A reused session ID fails before prompt submission.
- Session ID is durable in event/state before prompt submission.
- A failure persisting the ID prevents submission and attempts abort.
- Ambiguous create failure is not retried.
- The one synchronous response supplies terminal message evidence; normal
  status and message polling do not occur.
- Valid terminal message and structured result complete; absent or inconsistent
  terminal evidence fails closed.
- Local HTTP fake coverage rejects the former deny-only create request and accepts
  only the exact ordered two-rule request. It captures the complete session body
  and complete message body, including the agent, provider/model route, exact
  persisted prompt, complete JSON schema, and absence of `retryCount`.
- Deterministic permission selection coverage models OpenCode `1.17.18`
  last-match semantics: `StructuredOutput` is allowed by the final exception and
  representative shell, repository, web, task, MCP, and external permissions
  remain denied. Direct-adapter and full-controller captures use the same fixture,
  route sequence, and request bodies.
- Timeout uses monotonic time, calls abort once, and records interruption. The
  daemon worker timestamp and post-dequeue cancellation/deadline arbitration
  prevent queue wake-up order from promoting late evidence.
- Cooperative interruption attempts abort and preserves the session ID.
- Abort non-acknowledgement is retained as failure evidence.

### 19.4 Structured Results

- Exact completed, blocked, and failed results validate according to nullability rules.
- Missing fields, unknown fields, wrong types, booleans as schema integers, invalid statuses, empty/oversized strings, non-empty probe checks, excessive checks, and oversized JSON fail.
- Free-form text without structured output fails.
- A server-reported `StructuredOutputError` fails without a controller retry, corrective prompt, or second session.
- Valid blocked and failed agent results are persisted but do not produce `execution_not_implemented` success.

### 19.5 Invocation Artifacts

- Sequence and path allocation are deterministic and reject reuse.
- Metadata lifecycle updates are atomic and retain known session IDs.
- Cross-record identities, result availability/status, transcript status/truncation, and path identity are validated.
- Terminal writes follow result, transcript, metadata, agent event, then run-block ordering; injected failures preserve only documented prefixes and never imply success.
- Prompt bytes equal submitted sanitized prompt bytes.
- `result.json` is written only for valid agent output.
- Transcript messages are reconstructed, recursively sanitized, and bounded;
  assistant session aliases, exact created-session identity, exact reconstructed
  parent linkage, and every retained part's `sessionID`/`messageID` association
  are validated on both live and persisted evidence.
- Every tool part requires a bounded documented `tool` string; a compatible
  `name` alias may agree but never substitutes for it, and only exact
  `StructuredOutput` is accepted.
- Credential-bearing transcript fixtures redact synthetic values.
- Oversized transcript fields and total transcript are deterministically truncated and marked.
- Symlink, non-regular, hard-linked, replaced, unwritable, short-write, pre-replace, and post-replace failure cases fail closed without corrupting prior complete artifacts.
- Failed/interrupted metadata accurately reports absent result or transcript.
- Definitive session-create rejection produces terminal failed metadata with null session/start values and a non-null completion/error.

### 19.6 State, Events, and Status

- Sprint 1 histories remain valid and unchanged.
- Server fields accept only normalized safe URL and supported version pairs after validation.
- Active invocation schema and lifecycle validate strictly.
- Same-state Sprint 2 events are accepted only in the documented order.
- Event/state consistency remains event-first and state-second.
- Status during `agent.started` reports active role, invocation, and session with active status `running` in durable state.
- Status after completion/interruption clears active fields.
- Human status renders active identifiers safely.
- Status makes no network request and remains available while the synchronous worker waits.

### 19.7 Repository Safety

- A successful non-mutating probe leaves both repositories unchanged except exact controller artifacts.
- Sprint repository edits outside exact controller artifacts are detected and preserved.
- Managed staged, unstaged, untracked, branch, HEAD, index-flag, operation-state, and gitlink changes are detected and preserved.
- No reset, stash, clean, checkout, switch, add, commit, push, or blanket staging command is invoked.

### 19.8 Integration and Packaging

- A local fake HTTP server exercises the real OpenCode HTTP adapter without external network access.
- A fake runner exercises deterministic state-machine outcomes without HTTP.
- Real OpenCode tests are opt-in and skipped by default.
- The complete default suite passes without OpenCode or provider credentials.
- Formatting, linting, type checking, compilation, package build, and clean-install smoke tests pass.

## 20. Documentation Requirements

Update user-facing documentation in the implementation change to include:

- Sprint 2 implemented behavior and remaining limitations.
- Supported OpenCode version range.
- Requirement for an already-running server rooted at the sprint repository.
- URL syntax and trusted-local HTTP limitation.
- `OPENCODE_SERVER_PASSWORD` and `OPENCODE_SERVER_USERNAME` handling without showing real credentials.
- Required configured agents and models.
- Invocation record layout and size/redaction behavior.
- Active session status fields.
- Timeout, abort, orphan-session ambiguity, and non-resumable failure behavior.
- Offline default tests and opt-in real-server test/demonstration instructions.
- Continued absence of product Builder, Git commits, CI, functional controls, and Neovim integration.

Documentation examples use synthetic hosts, session IDs, model names, and credentials only.

## 21. Acceptance Criteria

Sprint 2 is accepted when:

1. `run` rejects malformed, credential-bearing, unreachable, unauthenticated, unhealthy, unsupported, and wrong-workspace servers before mutation.
2. A supported healthy server rooted at the sprint repository passes agent and model preflight.
3. The controller never starts or substitutes an OpenCode server.
4. One fresh configured Auditor session is created with a recognizable title.
5. The session ID is durably visible through state/status before prompt submission.
6. The execution probe runs under the exact ordered wildcard-deny then `StructuredOutput`-allow permissions and returns a controller-validated structured result; free-form prose, terminal evidence not bound to the created session/prompt, or any non-exact-`StructuredOutput` tool use cannot pass.
7. Complete bounded metadata, prompt, result, and sanitized transcript records exist for a successful probe.
8. Status remains local and reports active role, invocation, and session while the probe runs.
9. Timeout and cooperative interruption request abort, preserve evidence, and fail closed.
10. Ambiguous session creation is not retried and is not treated as success.
11. Credentials and authorization data are absent from every persisted artifact and diagnostic.
12. Post-invocation checks prove no repository mutation beyond exact controller runtime artifacts.
13. Unexpected agent changes are preserved and block completion without destructive Git action.
14. A successful probe ends at `blocked/execution_not_implemented` without commit, push, audit, CI, or Neovim behavior.
15. Existing Sprint 1 state/event histories and stable status fields remain compatible.
16. Default tests use deterministic fakes and require no network, credentials, model usage, or global Git identity.
17. The required opt-in real-server demonstration succeeds against a supported OpenCode server and wrong-workspace failure is demonstrated.
18. Full tests and formatting, linting, typing, build, clean-install, and diff checks pass.

## 22. Exit Demonstration

The sprint review must demonstrate:

1. Install the built package in a clean Python 3.11+ environment.
2. Create a clean example sprint repository with a real managed submodule and configured Auditor model.
3. Start an authenticated supported OpenCode server rooted at that sprint repository outside the controller.
4. Run `sprint-loop run --root <root> --server-url <origin>` without putting credentials in argv.
5. Observe the newly titled `0001-auditor` execution-probe session through an ordinary OpenCode client.
6. Query status during the invocation and observe role, invocation ID, and session ID.
7. Inspect sanitized metadata, prompt, validated result, and transcript artifacts.
8. Verify sprint and managed Git state changed only by expected uncommitted controller runtime records.
9. Observe the final `blocked/execution_not_implemented` state.
10. Repeat with a healthy server whose default workspace is different and show a mutation-free `wrong_server_workspace` failure with no session.
11. Run a deterministic fake timeout scenario and show abort, interruption evidence, and preserved session identity without waiting for a real model timeout.

The real-server portion is opt-in because it may require model credentials, network access, time, and cost. The default automated suite must not invoke it.

## 23. Handoff to Later Sprints

Sprint 2 must leave later sprints with:

- A stable `AgentRunner` boundary and deterministic fake.
- Safe server URL, authentication, health, workspace, agent, and model validation.
- Fresh-session creation with session-ID durability before prompt submission.
- Synchronous response waiting, timeout, bounded abort-status checks, result validation, and transcript capture.
- Versioned invocation artifacts and active status projection.
- A transition mechanism capable of durable same-state observations.
- No premature Builder handoff, audit, CI, recovery, or plugin behavior.

Sprint 3 may consume the stable CLI/status contract in the plugin repository. Sprint 4 replaces the Sprint 2 probe with real Builder prompt assembly and controller-owned Git handoff while reusing the runner and invocation-record boundaries. Sprint 7 completes resume and server-loss recovery.
