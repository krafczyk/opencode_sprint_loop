# Controller V1 Sprint 2 Checklist: OpenCode Execution Layer

## Usage

This checklist tracks implementation of `docs/controller-v1/2/sprint_spec.md`. It does not replace the sprint specification or authoritative V1 documents.

An item may be checked only when its implementation, tests, and required documentation are complete. If implementation changes a durable schema, CLI contract, Git ownership rule, terminal-state rule, or external compatibility policy, update the governing specification before checking the item.

## 1. Scope and Inputs

- [x] **S2-SCOPE-001** Read `AGENTS.md`, both authoritative V1 documents, `docs/threat_model.md`, `docs/audit_policy.md`, and the Sprint 2 specification before implementation.
- [x] **S2-SCOPE-002** Confirm Sprint 1's Completion Gate remains fully checked and Sprint 2 is the current sprint.
- [x] **S2-SCOPE-003** Inspect the worktree and preserve unrelated existing changes.
- [x] **S2-SCOPE-004** Keep product Builder prompts, staged handoff, Git commits/pushes, audit rounds, CI, functional controls, recovery, Neovim code, and multi-repository execution out of Sprint 2.
- [x] **S2-SCOPE-005** Apply the trusted-user prototype threat model without treating excluded hostile local races as Sprint 2 blockers.
- [x] **S2-SCOPE-006** Preserve the absolute safety invariants for credentials, user work, server ownership, ambiguous evidence, and interrupted dirty work.
- [x] **S2-SCOPE-007** Record accepted Sprint 2 limitations without presenting them as implemented recovery behavior.

## 2. Runtime and Architecture

- [x] **S2-ARCH-001** Define a typed OpenCode-independent `AgentRunner` protocol.
- [x] **S2-ARCH-002** Keep raw HTTP and OpenCode response shapes inside the OpenCode adapter.
- [x] **S2-ARCH-003** Support separate server validation, session creation, prompt submission, observation, abort, and transcript operations.
- [x] **S2-ARCH-004** Permit the controller to persist a session ID before prompt submission.
- [x] **S2-ARCH-005** Add concise contract and side-effect docstrings to public Sprint 2 APIs.
- [x] **S2-ARCH-006** Use monotonic time for invocation deadlines and UTC RFC 3339 timestamps for durable records.
- [x] **S2-ARCH-007** Use standard-library synchronous HTTP and bounded queue/status waits, or document and pin any justified runtime dependency before use.
- [x] **S2-ARCH-008** Keep network calls and large artifact processing outside the short persistence-lock critical section.
- [x] **S2-ARCH-009** Keep the default package and test suite usable without a real OpenCode server.

## 3. Server URL Validation

- [x] **S2-URL-001** Require a non-empty absolute `http` or `https` server origin.
- [x] **S2-URL-002** Require a non-empty valid host and valid optional port.
- [x] **S2-URL-003** Reject URL username, password, query, fragment, control characters, and malformed percent encoding.
- [x] **S2-URL-004** Reject non-root URL paths while accepting empty path or `/`.
- [x] **S2-URL-005** Normalize a valid origin deterministically without embedding credentials.
- [x] **S2-URL-006** Construct endpoint URLs only from the normalized origin and controller-owned paths.
- [x] **S2-URL-007** Avoid printing a rejected URL verbatim in diagnostics.
- [x] **S2-URL-008** Apply local URL syntax and credential-placement validation to both `run` and `resume`.
- [x] **S2-URL-009** Keep `resume` otherwise mutation-free and `feature_not_implemented` in Sprint 2.

## 4. Authentication and Transport Safety

- [x] **S2-AUTH-001** Read OpenCode HTTP Basic credentials only from inherited `OPENCODE_SERVER_PASSWORD` and optional `OPENCODE_SERVER_USERNAME`.
- [x] **S2-AUTH-002** Default the username to `opencode` only when a password is present.
- [x] **S2-AUTH-003** Reject username-without-password authentication configuration safely.
- [x] **S2-AUTH-004** Build the Basic authorization header only in memory.
- [x] **S2-AUTH-005** Keep credentials out of CLI arguments, configuration, state, events, metadata, prompts, results, transcripts, status, and logs.
- [x] **S2-AUTH-006** Distinguish `401` authentication failure without echoing authentication response data.
- [x] **S2-AUTH-007** Do not use provider-authentication endpoints or copy provider credentials.
- [x] **S2-HTTP-001** Set bounded connection and response timeouts for every request.
- [x] **S2-HTTP-002** Reject HTTP redirects instead of forwarding credentials or changing origin.
- [x] **S2-HTTP-003** Bound response bytes before JSON decoding.
- [x] **S2-HTTP-004** Strictly decode JSON with duplicate-key and non-finite-value rejection.
- [x] **S2-HTTP-005** Normalize transport and HTTP failures into safe stable controller errors.
- [x] **S2-HTTP-006** Never log complete request headers, response bodies, prompts, or transcripts.
- [x] **S2-HTTP-007** Percent-encode every server-provided session identifier as one URL path segment.

## 5. Mutation-Free Server Preflight

- [x] **S2-PREFLIGHT-001** Run existing configuration and Git preflight before server validation.
- [x] **S2-PREFLIGHT-002** Validate URL and authentication before any request.
- [x] **S2-PREFLIGHT-003** Validate health, version, workspace, agents, providers, and models before runtime mutation.
- [x] **S2-PREFLIGHT-004** Acquire no session during server preflight.
- [x] **S2-PREFLIGHT-005** Repeat local configuration and repository preflight after acquiring run ownership.
- [x] **S2-PREFLIGHT-006** Perform no network call while ownership is held but no run state exists; classify later service changes as post-start failures.
- [x] **S2-PREFLIGHT-007** Prove each representative server-preflight failure creates no runtime path, invocation artifact, lock metadata, session, Git index change, or commit.
- [x] **S2-PREFLIGHT-008** Prove no-run status semantics remain unchanged during bounded post-lock local revalidation.

## 6. Health and Version Validation

- [x] **S2-HEALTH-001** Call `GET /global/health` against the supplied server.
- [x] **S2-HEALTH-002** Require an object with `healthy: true` and a non-empty version string.
- [x] **S2-HEALTH-003** Accept supported release versions `>=1.17.0` and `<1.18.0`.
- [x] **S2-HEALTH-004** Reject malformed, pre-release, older, and newer-minor versions with `unsupported_server_version`.
- [x] **S2-HEALTH-005** Distinguish unavailable, unauthenticated, unhealthy, malformed, oversized, and incompatible server responses.
- [x] **S2-HEALTH-006** Persist only the validated version after durable run creation.

## 7. Workspace Validation

- [x] **S2-WORKSPACE-001** Call `GET /path` without a directory query or override header.
- [x] **S2-WORKSPACE-002** Require both returned `directory` and `worktree` values.
- [x] **S2-WORKSPACE-003** Canonicalize both values and require equality with the canonical sprint root.
- [x] **S2-WORKSPACE-004** Reject a server merely capable of opening the root when its default context is elsewhere.
- [x] **S2-WORKSPACE-005** Use the validated default context for all later Sprint 2 instance requests.
- [x] **S2-WORKSPACE-006** Revalidate the session response directory before prompt submission.
- [x] **S2-WORKSPACE-007** Return actionable `wrong_server_workspace` diagnostics without leaking unrelated server paths unnecessarily.

## 8. Agent, Provider, and Model Validation

- [x] **S2-CAP-001** Call `GET /agent` and strictly validate the response collection.
- [x] **S2-CAP-002** Require each configured Builder, Auditor, and CI Fixer name exactly once.
- [x] **S2-CAP-003** Reject missing, duplicate, malformed, or non-invocable configured agent records.
- [x] **S2-CAP-004** Do not silently substitute built-in or differently named agents.
- [x] **S2-CAP-005** Call documented provider/configuration endpoints needed to inspect connected providers and models.
- [x] **S2-CAP-006** Split configured model values at the first `/` into `providerID` and `modelID`.
- [x] **S2-CAP-007** Require every configured provider to be connected/configured and every configured model to be advertised.
- [x] **S2-CAP-008** Treat provider/model presence as preflight evidence rather than proof of quota, billing, or inference success.
- [x] **S2-CAP-009** Use only the configured Auditor agent and model for the Sprint 2 probe.

Real-server verification on 2026-07-13 confirmed that supported OpenCode
`1.17.18` reports configured provider records through
`/config/providers.providers`, catalog records through `/provider.all`, and
connection state through `/provider.connected`; the adapter and deterministic
fixtures use those documented shapes.

## 9. Fake Runner and Fake Server

- [x] **S2-FAKE-001** Implement a deterministic fake satisfying the `AgentRunner` protocol.
- [x] **S2-FAKE-002** Script every server validation success and failure category.
- [x] **S2-FAKE-003** Script unique, duplicate, and ambiguous session creation outcomes.
- [x] **S2-FAKE-004** Script blocked synchronous completion and bounded status-only abort observations.
- [x] **S2-FAKE-005** Script valid, blocked, failed, malformed, free-form, and oversized results.
- [x] **S2-FAKE-006** Script timeout, interruption, abort acknowledgement, and abort non-acknowledgement.
- [x] **S2-FAKE-007** Script complete, malformed, credential-bearing, and oversized transcripts.
- [x] **S2-FAKE-008** Add a local fake HTTP server exercising the real adapter without external network access.
- [x] **S2-FAKE-009** Keep state-machine tests independent of OpenCode HTTP implementation details.

## 10. Execution Probe Prompt and Schema

- [x] **S2-PROBE-001** Use the configured Auditor as the non-mutating Sprint 2 probe role.
- [x] **S2-PROBE-002** Build a deterministic title `[<multisprint>/<sprint>] auditor <sequence> execution probe`.
- [x] **S2-PROBE-003** Create a deterministic prompt prohibiting repository, shell, web, task, MCP, and mutation tools while permitting only the required built-in structured-output mechanism.
- [x] **S2-PROBE-003A** Create the probe session with the exact ordered OpenCode 1.17.18 wildcard-deny then `StructuredOutput`-allow permission ruleset that overrides configured Auditor tool permissions.
- [x] **S2-PROBE-004** Exclude irrelevant product specifications, source content, findings, and commit-message paths from the probe prompt.
- [x] **S2-PROBE-005** Keep server URL, credentials, and environment data out of the prompt.
- [x] **S2-RESULT-001** Request `json_schema` structured output with exact fields and no additional properties.
- [x] **S2-RESULT-002** Send the documented `json_schema` request without speculative compatibility hints or controller retries.
- [x] **S2-RESULT-003** Independently validate schema version, status, summary, checks, and blocking-reason rules.
- [x] **S2-RESULT-004** Require an empty checks array because no substantive verification tool ran; do not count the built-in structured-output mechanism as a check.
- [x] **S2-RESULT-005** Accept only `completed`, `blocked`, and `failed` statuses.
- [x] **S2-RESULT-006** Reject free-form prose, missing structured output, schema mismatch, unknown fields, server-reported `StructuredOutputError`, and credential-bearing results without controller retry.
- [x] **S2-RESULT-007** Advance to the successful Sprint 2 placeholder only for `completed`.
- [x] **S2-RESULT-008** Persist valid blocked/failed results while entering an actionable blocked state.
- [x] **S2-RESULT-009** Reject any permission request or tool part other than the internally injected `StructuredOutput` mechanism as `unexpected_probe_tool`.

## 11. Session Creation and Freshness

- [x] **S2-SESSION-001** Allocate invocation sequence `1` and ID `0001-auditor` for the first Sprint 2 run invocation.
- [x] **S2-SESSION-002** Capture a bounded `GET /session` snapshot, then create one top-level session with `POST /session` and no parent or fork.
- [x] **S2-SESSION-003** Validate non-empty bounded session ID and matching session directory.
- [x] **S2-SESSION-003A** Validate exact returned title, absent/null parent ID, and the exact ordered effective deny-plus-`StructuredOutput` permission rules before prompt submission.
- [x] **S2-SESSION-004** Reject a session ID present in the pre-creation snapshot or already recorded in the run as `non_fresh_session`.
- [x] **S2-SESSION-005** Do not use title uniqueness as identity evidence.
- [x] **S2-SESSION-006** Do not retry session creation after an ambiguous transport outcome.
- [x] **S2-SESSION-007** Record `session_creation_ambiguous` without claiming that no external session exists.
- [x] **S2-SESSION-008** Never silently launch or select another OpenCode server.

## 12. Session-ID Durability

- [x] **S2-DURABLE-001** Persist initial invocation metadata and prompt before session creation.
- [x] **S2-DURABLE-002** Persist `agent.started` state/event with invocation and session IDs immediately after a successful create response.
- [x] **S2-DURABLE-003** Complete session-ID state/event persistence before prompt submission.
- [x] **S2-DURABLE-004** Atomically update metadata with the known session ID before prompt submission.
- [x] **S2-DURABLE-005** Prevent prompt submission if either required session-ID persistence step fails.
- [x] **S2-DURABLE-006** Best-effort abort an empty known session after session-ID persistence failure.
- [x] **S2-DURABLE-007** Never create a replacement session after such a failure.
- [x] **S2-DURABLE-008** Preserve a known session ID in every later metadata update and failure transition.

## 13. Prompt Submission and Observation

- [x] **S2-OBS-001** Submit the prompt with documented synchronous `POST /session/<id>/message` only after session-ID durability.
- [x] **S2-OBS-002** Require the returned terminal assistant response rather than asynchronous acceptance.
- [x] **S2-OBS-003** Use no normal status/message polling; wait on a bounded queue while the daemon request worker is blocked.
- [x] **S2-OBS-004** Reserve documented status checks for bounded abort confirmation only.
- [x] **S2-OBS-005** Require returned terminal assistant-message evidence, exact prompt binding through its parent ID, and configured identity.
- [x] **S2-OBS-006** Reject conflicting structured aliases, errors, forbidden tools, permission requests, malformed response, and absent output.
- [x] **S2-OBS-007** Associate the result with the sole prompt in the fresh session.
- [x] **S2-OBS-008** Enforce `limits.invocation_timeout_seconds` with monotonic time.
- [x] **S2-OBS-009** Treat ordinary mid-invocation service loss as interruption without assigning Sprint 7 grace semantics.
- [x] **S2-OBS-009A** Route ambiguous prompt submission and status/message transport failures with uncertain terminal state through the bounded abort path.
- [x] **S2-OBS-010** Make no checkpoint commit or polling-only event during observation.

## 14. Abort and Cooperative Interruption

- [x] **S2-ABORT-001** Call `POST /session/<id>/abort` once on timeout.
- [x] **S2-ABORT-002** Treat catchable `SIGINT` and `SIGTERM` as cooperative cancellation requests and attempt abort while a session is active.
- [x] **S2-ABORT-003** Bound abort confirmation to 10 seconds.
- [x] **S2-ABORT-004** Treat abort acknowledgement as acknowledgement rather than proof of full cancellation.
- [x] **S2-ABORT-005** Preserve only already-returned synchronous transcript evidence; do not retrieve messages after abort.
- [x] **S2-ABORT-006** Persist `agent.interrupted` and clear active invocation when persistence remains coherent.
- [x] **S2-ABORT-007** Enter blocked with `invocation_timed_out` or `invocation_interrupted`.
- [x] **S2-ABORT-008** Record inability to confirm abort without losing the session ID.
- [x] **S2-ABORT-009** Keep pause/stop commands non-functional and do not route them to Sprint 2 abort behavior.
- [x] **S2-ABORT-010** Document that uncatchable process loss may leave an externally running session.
- [x] **S2-ABORT-011** Exit `130` after orderly `SIGINT` handling and `143` after orderly `SIGTERM` handling.
- [x] **S2-ABORT-012** Add a process-level signal test in addition to injected fake cancellation tests.
- [x] **S2-ABORT-013** Best-effort abort any known successfully created session even when cancellation precedes normal `agent.started` completion.

## 15. State Model

- [x] **S2-STATE-001** Keep state schema version `1` and preserve Sprint 1 history compatibility.
- [x] **S2-STATE-002** Accept null server fields before validation and a normalized origin/supported version pair afterward.
- [x] **S2-STATE-003** Reject credential-bearing or malformed persisted server URLs.
- [x] **S2-STATE-004** Implement the exact active invocation fields from the Sprint 2 specification.
- [x] **S2-STATE-005** Validate invocation ID, sequence, role, model, session ID, status, and start timestamp.
- [x] **S2-STATE-006** Require active invocation role/model to agree with configured Auditor values when cross-validating persistence.
- [x] **S2-STATE-007** Require active invocation status `running` from `agent.started` through completion or interruption.
- [x] **S2-STATE-008** Clear active invocation after completion or interruption.
- [x] **S2-STATE-009** Leave commit, audit, CI, counters, checklist, control, and terminal-result Sprint 1 constraints unchanged.
- [x] **S2-STATE-010** Reject missing, malformed, unsafe, or inconsistent Sprint 2 fields instead of supplying defaults.

## 16. Events and Transitions

- [x] **S2-EVENT-001** Extend event-history validation for `server.validated`, `agent.started`, `agent.completed`, and `agent.interrupted`.
- [x] **S2-EVENT-002** Implement guarded same-state `validating -> validating` updates only in the documented order.
- [x] **S2-EVENT-003** Persist normalized server identity/version through `server.validated`.
- [x] **S2-EVENT-004** Persist invocation/session identity through `agent.started`.
- [x] **S2-EVENT-005** Persist valid terminal result status through `agent.completed`.
- [x] **S2-EVENT-006** Persist safe interruption reason through `agent.interrupted`.
- [x] **S2-EVENT-006A** Enforce the exact version-one payload fields and types documented for each Sprint 2 event.
- [x] **S2-EVENT-007** Keep prompts, summaries, transcripts, HTTP bodies, headers, and credentials out of event payloads.
- [x] **S2-EVENT-008** Preserve monotonic sequence, event-first/state-second ordering, and persistence locking.
- [x] **S2-EVENT-009** Keep network and transcript operations outside transition critical sections.
- [x] **S2-EVENT-010** End successful Sprint 2 execution with `run.blocked/execution_not_implemented`.
- [x] **S2-EVENT-011** Use `failed/internal_error` only for unexpected controller defects with best-effort coherent persistence.

## 17. Invocation Paths and Metadata

- [x] **S2-INV-001** Derive `invocations/<multisprint>/<sprint>/<sequence>-<role>/` only from validated values.
- [x] **S2-INV-002** Format sequences with at least four decimal digits and increase them monotonically.
- [x] **S2-INV-003** Reject existing invocation directories and immutable artifacts while permitting validated atomic replacement of current-invocation `metadata.json` only.
- [x] **S2-INV-004** Implement the exact version-one metadata fields, types, nullability, bounds, and lifecycle invariants.
- [x] **S2-INV-005** Support only documented metadata lifecycle statuses.
- [x] **S2-INV-006** Keep Sprint 2 input commit values null under the configured repository key.
- [x] **S2-INV-007** Store server version but no URL, username, password, header, or environment in metadata.
- [x] **S2-INV-008** Update metadata atomically at lifecycle boundaries.
- [x] **S2-INV-009** Retain known session identity and prior complete fields across updates.
- [x] **S2-INV-010** Record unavailable result/transcript evidence accurately after failure or interruption.
- [x] **S2-INV-011** Permit terminal infrastructure-failed metadata with null session/start values after definitive or ambiguous creation failure.
- [x] **S2-INV-012** Cross-validate run, invocation, sequence, role, model, session, server, path, result, and transcript identities across every durable record.
- [x] **S2-INV-013** Require result-file existence/status and transcript-file existence/status/truncation to agree exactly with metadata and terminal events.
- [x] **S2-INV-014** Persist terminal records in result, transcript, metadata, agent-event/state, then run-block order, skipping unavailable artifacts explicitly.
- [x] **S2-INV-015** Accept only documented in-progress ordering prefixes and fail impossible or contradictory combinations as `inconsistent_invocation_record` without repair.

## 18. Prompt, Result, and Transcript Artifacts

- [x] **S2-ART-001** Persist exact sanitized submitted prompt bytes in newline-terminated `prompt.md`.
- [x] **S2-ART-002** Never modify `prompt.md` after session creation.
- [x] **S2-ART-003** Persist only independently validated, credential-free agent output in `result.json`.
- [x] **S2-ART-004** Do not fabricate a failed agent result after transport or schema failure.
- [x] **S2-ART-005** Reconstruct `transcript.json` from the synchronous returned assistant message and exact persisted prompt; never call the message-list endpoint.
- [x] **S2-ART-005A** Use the exact version-one opaque wrapper with `format`, `original_bytes`, and canonical sanitized JSON `content`.
- [x] **S2-ART-006** Do not invoke `opencode export` or access OpenCode local storage.
- [x] **S2-ART-007** Preserve useful message/part structure while sanitizing recursively.
- [x] **S2-ART-007A** Preserve array order, sort opaque object keys, retain unknown raw fields only inside opaque content, and validate the message/part envelope needed for result association and tool detection.
- [x] **S2-ART-008** Require transcript capture for successful invocation completion.
- [x] **S2-ART-009** Make transcript capture best effort after failure/interruption and record availability in metadata.
- [x] **S2-ART-010** Mark sanitized and truncated transcript state explicitly.
- [x] **S2-ART-011** Use exact `[REDACTED]` and `[TRUNCATED]` markers with redaction before per-string and total truncation.

## 19. Safe Artifact Persistence

- [x] **S2-SAFEIO-001** Anchor invocation paths beneath the expected invocation directory.
- [x] **S2-SAFEIO-002** Reject symlink, non-regular, unsafe, and unexpectedly replaced artifact paths.
- [x] **S2-SAFEIO-003** Create files with restrictive permissions equivalent to `0600`.
- [x] **S2-SAFEIO-004** Serialize and validate complete artifact content before replacement.
- [x] **S2-SAFEIO-005** Use same-directory temporary files and atomic replacement.
- [x] **S2-SAFEIO-006** Flush file data and sync directories where supported.
- [x] **S2-SAFEIO-007** Clean handled temporary artifacts only when safe.
- [x] **S2-SAFEIO-008** Inject short-write, permission, pre-replace, post-replace, and directory-sync failures.
- [x] **S2-SAFEIO-009** Prove readers see prior or next complete metadata/result/transcript artifacts, never truncated JSON.
- [x] **S2-SAFEIO-010** Do not stage or commit invocation artifacts in Sprint 2.

## 20. Bounds and Sanitization

- [x] **S2-BOUND-001** Enforce an 8 MiB limit on each HTTP response body before JSON decoding.
- [x] **S2-BOUND-002** Enforce a 1 MiB UTF-8 prompt limit before session creation.
- [x] **S2-BOUND-003** Enforce a 1 MiB structured-result JSON limit.
- [x] **S2-BOUND-004** Enforce a 1 MiB metadata JSON limit.
- [x] **S2-BOUND-005** Enforce an 8 MiB persisted transcript JSON limit.
- [x] **S2-BOUND-006** Enforce a 1 MiB per retained transcript string limit.
- [x] **S2-BOUND-007** Enforce 1024-byte bounds for identifiers and small categorical fields.
- [x] **S2-BOUND-008** Redact external strings before transcript size truncation.
- [x] **S2-BOUND-009** Truncate oversized transcript content deterministically with explicit markers.
- [x] **S2-BOUND-010** Fail non-transcript oversized inputs instead of silently truncating semantic data.
- [x] **S2-SEC-001** Reject credentials in controller-authored prompt, metadata, state, event, and result data.
- [x] **S2-SEC-002** Reject credential-bearing structured results without writing `result.json`; redact recognizable credentials in other external response and transcript keys and values.
- [x] **S2-SEC-003** Cover URL user-info, URL query/fragment values, authorization headers, common secret keys, and supported provider token patterns.
- [x] **S2-SEC-004** Use synthetic credentials in every test and example.

## 21. Repository Non-Mutation

- [x] **S2-GIT-001** Capture sprint and managed repository identities before session execution.
- [x] **S2-GIT-002** Verify sprint and managed HEADs and branches after invocation.
- [x] **S2-GIT-003** Verify managed index, worktree, untracked files, hidden index flags, operation state, and gitlink remain unchanged.
- [x] **S2-GIT-004** Permit only exact controller-owned Sprint 2 runtime files in the sprint repository.
- [x] **S2-GIT-005** Enumerate permitted runtime paths narrowly instead of allowing arbitrary files beneath `info/` or `invocations/`.
- [x] **S2-GIT-006** Detect unexpected sprint-repository changes outside controller artifacts.
- [x] **S2-GIT-007** Enter `unexpected_agent_repository_change` with an actionable repository path.
- [x] **S2-GIT-008** Preserve all unexpected changes exactly.
- [x] **S2-GIT-009** Never reset, stash, clean, checkout, switch, add, commit, push, or broad-stage in response.
- [x] **S2-GIT-010** Prove successful probe execution changes only expected uncommitted controller runtime artifacts.

## 22. Error Model

- [x] **S2-ERR-001** Implement and document every required Sprint 2 stable reason code or a tested equivalent specific mapping.
- [x] **S2-ERR-002** Distinguish URL, authentication, health, version, API, workspace, capability, session, prompt, timeout, result, transcript, record, and repository failures.
- [x] **S2-ERR-003** Keep expected external failures out of `internal_error`.
- [x] **S2-ERR-004** Before durable run creation, return non-zero with no mutation.
- [x] **S2-ERR-005** After durable run creation, preserve available evidence and persist an actionable blocked reason when possible.
- [x] **S2-ERR-006** Preserve inconsistent/corrupt persistence rather than overwriting it with a new failure.
- [x] **S2-ERR-007** Never retry a non-idempotent request with ambiguous outcome.
- [x] **S2-ERR-008** Redact credentials and bound external diagnostics before display or persistence.
- [x] **S2-ERR-009** Avoid normal Python tracebacks for expected Sprint 2 failures.

## 23. Status Projection

- [x] **S2-STATUS-001** Preserve every stable Sprint 1 JSON status field and meaning.
- [x] **S2-STATUS-002** Populate active role, invocation ID, and session ID during the active session.
- [x] **S2-STATUS-003** Clear active fields before and after the active invocation.
- [x] **S2-STATUS-004** Render active identifiers in human status.
- [x] **S2-STATUS-005** Keep server URL, credentials, prompt, result summary, transcript, and raw errors out of status.
- [x] **S2-STATUS-006** Keep status read-only and independent of OpenCode availability.
- [x] **S2-STATUS-007** Make no HTTP request from human or JSON status.
- [x] **S2-STATUS-008** Keep status readable during synchronous worker waiting apart from short persistence writes.
- [x] **S2-STATUS-009** Project same-state Sprint 2 events as the latest event correctly.
- [x] **S2-STATUS-010** Continue reading valid Sprint 1 placeholder histories unchanged.

## 24. Run and Control Flow

- [x] **S2-RUN-001** Replace the immediate Sprint 1 placeholder with the documented server-validation and probe flow.
- [x] **S2-RUN-002** Preserve existing-run detection before dirty-worktree validation.
- [x] **S2-RUN-003** Complete initial and post-lock mutation-free preflight before first runtime write.
- [x] **S2-RUN-004** Persist `initializing`, `validating`, and `server.validated` before session creation.
- [x] **S2-RUN-005** Execute exactly one fresh Auditor probe.
- [x] **S2-RUN-006** Persist completion/interruption evidence and verify repository non-mutation.
- [x] **S2-RUN-007** End successful Sprint 2 execution at `blocked/execution_not_implemented` with non-zero status.
- [x] **S2-RUN-008** Do not invoke Builder handoff, Git mutation, audit, CI, checkpoint, recovery, or Neovim behavior.
- [x] **S2-CTRL-001** Keep `pause` and `stop` mutation-free `feature_not_implemented` commands.
- [x] **S2-CTRL-002** Keep `resume` mutation-free after local URL validation.
- [x] **S2-CTRL-003** Do not apply `server_unavailable_grace_seconds` before Sprint 7.

## 25. Automated Verification

- [x] **S2-TEST-001** Cover every URL and authentication rule.
- [x] **S2-TEST-002** Cover every health/version HTTP and schema failure class.
- [x] **S2-TEST-003** Cover workspace canonicalization and wrong-default-context failures.
- [x] **S2-TEST-004** Cover configured agent, provider, and model failures.
- [x] **S2-TEST-005** Cover initial server-preflight and post-lock local-revalidation no-mutation guarantees.
- [x] **S2-TEST-006** Cover runner protocol behavior with deterministic fake outcomes.
- [x] **S2-TEST-007** Cover fresh session, reused ID, ambiguous creation, and durability ordering.
- [x] **S2-TEST-007A** Cover definitive session-create rejection with terminal failed metadata and null session/start values.
- [x] **S2-TEST-008** Cover synchronous submission, terminal evidence, daemon-worker timeout/cancellation, and bounded abort.
- [x] **S2-TEST-008A** Cover real process delivery of `SIGINT` and `SIGTERM`, orderly abort, durable interruption, and exit statuses `130`/`143`.
- [x] **S2-TEST-008B** Cover ambiguous prompt submission and status/message transport failures attempting one bounded abort.
- [x] **S2-TEST-009** Cover every structured-result validation rule.
- [x] **S2-TEST-009A** Cover the exact ordered OpenCode 1.17.18 permission request/response, deny-only fake rejection, last-match `StructuredOutput` allowance, representative shell/repository/web/task/MCP/external denials, and rejection of non-`StructuredOutput` tool evidence.
- [x] **S2-TEST-010** Cover invocation artifact schemas, atomic persistence, and fault injection.
- [x] **S2-TEST-010A** Inject failure after each terminal write boundary and verify documented prefixes never become success.
- [x] **S2-TEST-010B** Reject every cross-record identity, status, availability, truncation, and path mismatch.
- [x] **S2-TEST-011** Cover response, prompt, result, metadata, transcript, string, and identifier bounds.
- [x] **S2-TEST-012** Cover recursive credential sanitization with synthetic values.
- [x] **S2-TEST-013** Cover Sprint 2 state/event histories and Sprint 1 backward compatibility.
- [x] **S2-TEST-014** Cover active human/JSON status while a separate process owns the run.
- [x] **S2-TEST-015** Cover post-invocation repository mutation detection and preservation.
- [x] **S2-TEST-016** Prove no destructive or mutating Git command is invoked.
- [x] **S2-TEST-017** Exercise the real HTTP adapter against a local fake OpenCode server with exact complete session/message request capture (agent, provider/model route, prompt, schema, permissions, and no `retryCount`) and compare direct-adapter and full-controller captures using the same fixture/route semantics.
- [x] **S2-TEST-018** Keep real OpenCode integration opt-in and skipped by default.
- [x] **S2-TEST-019** Run the full default suite without network, OpenCode, model/provider credentials, or global Git identity.
- [x] **S2-TEST-020** Run formatting, linting, strict type checking, compilation, package build, and clean-wheel installation smoke tests.

## 26. Documentation

- [x] **S2-DOC-001** Update README Sprint status to describe implemented OpenCode execution behavior accurately.
- [x] **S2-DOC-002** Document the supported OpenCode `1.17.x` compatibility range.
- [x] **S2-DOC-003** Document the requirement for an already-running server rooted at the sprint repository.
- [x] **S2-DOC-004** Document accepted URL syntax and the trusted-local HTTP limitation.
- [x] **S2-DOC-005** Document inherited Basic authentication without real credential examples.
- [x] **S2-DOC-006** Document configured agent/provider/model preflight.
- [x] **S2-DOC-007** Document the non-mutating Auditor execution probe and final placeholder state.
- [x] **S2-DOC-008** Document invocation directories, artifact schemas, permissions, bounds, sanitization, and uncommitted status until Sprint 4.
- [x] **S2-DOC-009** Document active session fields in status.
- [x] **S2-DOC-010** Document timeout, abort, ambiguous orphan-session, and non-resumable interruption behavior.
- [x] **S2-DOC-011** Document default fake tests and opt-in real-server tests/demonstration.
- [x] **S2-DOC-012** Keep Builder handoff, commits, audits, CI, controls, recovery, and Neovim clearly marked unimplemented.
- [x] **S2-DOC-013** Keep command output, JSON examples, paths, and reason codes aligned with tests.

## 27. Threat and Security Review

- [ ] **S2-REVIEW-001** Audit implementation against `docs/threat_model.md` and `docs/audit_policy.md`.
- [ ] **S2-REVIEW-002** Prioritize ordinary malformed input, service failure, interruption, disk error, credentials, and accidental repository mutation.
- [ ] **S2-REVIEW-003** Keep hostile local races and compromised-server behavior in residual limitations unless the user expands scope.
- [ ] **S2-REVIEW-004** Confirm no credentials appear in tracked fixtures, snapshots, docs, runtime artifacts, diagnostics, or process arguments.
- [ ] **S2-REVIEW-005** Confirm no OpenCode server launch or silent substitution path exists.
- [ ] **S2-REVIEW-006** Confirm ambiguous session and terminal evidence fail closed.
- [ ] **S2-REVIEW-007** Confirm unexpected agent changes are preserved rather than repaired.
- [ ] **S2-REVIEW-008** Confirm no unbounded external response or transcript path remains.
- [ ] **S2-REVIEW-009** Record residual orphan-session, uncatchable-interruption, provider-readiness, and sanitization limitations.

Auditor pass 1 reported **AUD-S2-001** through **AUD-S2-003** as P1
`fix_now` findings. The prior review record's claim that no P0/P1 findings
remained was incorrect; its review and clean-audit checklist assertions remain
unchecked pending a fresh audit after the required repairs.

Repair verification (2026-07-13) updated the HTTP adapter and local HTTP fake
to consume documented OpenCode 1.17 `status.type`, `info.structured`, and
`info.error.name` fields; reject empty or relative server workspace/session
paths before canonicalization; and reject escaped unpaired-surrogate session
identifiers through stable controller errors. Focused adapter coverage and the
complete offline suite passed. This revalidates the affected implementation and
test items (**S2-HTTP-004**, **S2-WORKSPACE-002** through **S2-WORKSPACE-006**,
**S2-OBS-004** through **S2-OBS-006**, **S2-ERR-003**, **S2-ERR-005**, and
**S2-TEST-003**, **S2-TEST-008**, **S2-TEST-009**, **S2-TEST-017**), but is not
a fresh audit; **S2-REVIEW-001** through **S2-REVIEW-009** and **S2-DONE-012**
remain unchecked.

Auditor pass 2 findings **AUD-S2-004** through **AUD-S2-007** were repaired
on 2026-07-13. External UTF-8 validation now converts escaped unpaired
surrogates in result and transcript data into stable invocation failures;
`StructuredOutputError` takes precedence over a generic terminal message
error; sanitized transcript envelopes are retained before a permission, tool,
or structured-output semantic violation is reported; and each invocation HTTP
request is capped to its remaining monotonic deadline. Focused execution tests
cover the complete surrogate failure lifecycle, documented
`info.error.name`, retained violation evidence, and slow transport deadline
caps. This revalidates **S2-RESULT-006**, **S2-OBS-008**,
**S2-ART-007A** through **S2-ART-009**, **S2-ERR-003**, **S2-ERR-005**,
**S2-TEST-008** through **S2-TEST-011**, but is not a fresh audit;
**S2-REVIEW-001** through **S2-REVIEW-009** and **S2-DONE-012** remain
unchecked.

Builder repair verification (2026-07-13) addressed **AUD-S2-009** through
**AUD-S2-011**. Synthetic current-provider tokens, including all documented
GitLab forms, plus PostgreSQL and SSH URI user-info are rejected from
result/state/event data and redacted from transcript/diagnostic paths. Terminal
transcript acceptance requires the exact submitted prompt and documented
configured Auditor/provider/model identity; malformed present status entries
are rejected rather than normalized. Abort responses strictly accept and retain
only their documented JSON-boolean acknowledgement, all post-abort observations
share one monotonic confirmation deadline, and interruption events record
`idle` or absent confirmation. Focused offline execution and
foundation tests passed. These repairs revalidate the affected checked
implementation and test items, but are not a fresh audit; **S2-REVIEW-001**
through **S2-REVIEW-009** and **S2-DONE-012** remain unchecked.

Restart verification (2026-07-13) ran the focused abort, token-sanitization,
prompt-binding, status-entry, local-adapter, and foundation tests, followed by
the complete offline suite (196 passing, one opt-in real-server test skipped),
Ruff, mypy, compilation, build, clean-wheel installation, and diff checks.
The synthetic server now returns the documented JSON-boolean abort response.

Builder repair verification (2026-07-13) addressed the value-only portion of
**AUD-S2-013**. Current
variable-length stateless GitHub App installation tokens shaped
`ghs_<APPID>_<JWT>` now fail controller-authored result, state, and event
validation and redact from transcript and diagnostic paths. Any URI query value
or fragment, regardless of its key name, receives the same treatment. Focused
credential-path tests and the complete offline suite (196 passing, one opt-in
real-server test skipped), Ruff, mypy, compilation, build, clean-wheel
installation, and diff checks passed. This revalidates **S2-SEC-001** through
**S2-SEC-004**, **S2-TEST-009**, **S2-TEST-012**, and **S2-ERR-008**, but is not
a fresh audit; **S2-REVIEW-001** through **S2-REVIEW-009** and **S2-DONE-012**
remain unchecked.

Builder repair verification (2026-07-13) addressed residual **AUD-S2-012**
and **AUD-S2-013**. Timeout, transport-interruption, and signal-cancellation
paths now emit a concise fixed diagnostic only when bounded abort confirmation
is unavailable, while retaining the exact interruption-event schema. Recursive
transcript redaction and controller-authored safe-data validation now scan
object keys as well as values; sanitized key collisions fail transcript capture
rather than dropping evidence. Focused abort, transcript, and state-contract
tests and the complete offline suite passed (196 passing, one opt-in real-server
test skipped), along with Ruff, mypy, compilation, build, and diff checks. This
is not a fresh audit; **S2-REVIEW-001** through
**S2-REVIEW-009** and **S2-DONE-012** remain unchecked.

Builder repair verification (2026-07-14) addressed **AUD-S2-014** through
**AUD-S2-016**. The one synchronous `POST /session/:id/message` now receives
the complete remaining monotonic invocation budget, while preflight, abort, and
status requests retain short bounded transport timeouts. Adapter and persisted
transcript validation reconcile top-level and `info` aliases for role, message
ID, error, structured output, route identity, and supported parent spellings;
contradictory duplicates fail closed while identical aliases retain the complete
assistant evidence. Sprint terminology and fake/protocol coverage now describe
one synchronous response, no normal polling or post-abort message retrieval,
and status-only abort confirmation (`idle` or null). Focused and full
verification plus the fresh audit and real-server exit-demo gates remain
required before completion items are checked.

Builder repair verification (2026-07-14) addressed **AUD-S2-017** through
**AUD-S2-019**. The daemon prompt worker records monotonic completion for both
responses and exceptions; after dequeue, the controller accepts evidence only
when completion is strictly before the monotonic deadline and cancellation
boundary. A completion before a later cancellation remains valid, while late
results are ignored without persistence or worker joining. The queue wait
remains bounded and blocking, with no normal polling or busy-spin. Live and
persisted transcript validation now reconciles documented top-level/`info`
`sessionID`/`session_id` aliases, requires exact created-session identity, and
requires every retained part's documented `sessionID`/`messageID` association.
Tool evidence now requires bounded documented `tool`, treats `name` only as a
non-substituting consistency alias, and permits only exact `StructuredOutput`.
Focused delayed-worker race, adapter, and persisted-transcript coverage passed,
followed by the complete offline suite (**204 passing, 1 opt-in real-server test
skipped**), Ruff, formatting, strict mypy, compilation, build, clean-wheel
installation, and diff checks. A fresh installed-wheel demonstration against an
external authenticated OpenCode `1.17.18` server and disposable clean real
submodule fixture observed active status, exact live session/part and prompt
parent associations, validated result/transcript/metadata shapes, ordered
events, clean managed state, and final `blocked/execution_not_implemented`.
Temporary server, fixture, credentials, logs, build, and virtual-environment
data were removed. This verification does not itself constitute the required
fresh independent audit; **S2-REVIEW-001** through **S2-REVIEW-009** and
**S2-DONE-012** remain unchecked.

## 28. Scope Review

- [x] **S2-SCOPEREVIEW-001** Confirm no product Builder prompt or mutating-agent result schema was implemented.
- [x] **S2-SCOPEREVIEW-002** Confirm no commit-message path, staged handoff, Git commit, push, or checkpoint behavior was added.
- [x] **S2-SCOPEREVIEW-003** Confirm no audit finding, checklist assessment, or audit-round behavior was added.
- [x] **S2-SCOPEREVIEW-004** Confirm no GitHub or CI integration was added.
- [x] **S2-SCOPEREVIEW-005** Confirm no functional pause/resume/stop or server-loss grace recovery was added.
- [x] **S2-SCOPEREVIEW-006** Confirm no plugin code or submodule pointer update was required.
- [x] **S2-SCOPEREVIEW-007** Confirm no multi-repository, parallel invocation, multiplexer, or OpenCode database behavior was added.
- [x] **S2-SCOPEREVIEW-008** Compare public and durable contracts with the V1 specification and update authoritative documentation deliberately for any approved difference.

## 29. Exit Demonstration

- [x] **S2-DEMO-001** Install the built package into a clean Python 3.11+ environment.
- [x] **S2-DEMO-002** Create a clean sprint fixture with real managed submodule and valid configured agents/models.
- [x] **S2-DEMO-003** Start an authenticated supported OpenCode server rooted at the fixture outside the controller.
- [x] **S2-DEMO-004** Supply credentials only through inherited environment, never argv or files.
- [x] **S2-DEMO-005** Run `sprint-loop run` with a credential-free origin URL.
- [x] **S2-DEMO-006** Observe the fresh `0001-auditor` execution-probe session in an ordinary OpenCode client.
- [x] **S2-DEMO-007** Query status during execution and show role, invocation ID, and session ID.
- [x] **S2-DEMO-008** Inspect sanitized metadata, exact prompt, validated result, and bounded transcript.
- [x] **S2-DEMO-009** Verify both repositories changed only by expected uncommitted controller runtime records.
- [x] **S2-DEMO-010** Show final `blocked/execution_not_implemented` state and ordered Sprint 2 events.
- [x] **S2-DEMO-011** Demonstrate a wrong-default-workspace server failure with no runtime mutation or session.
- [x] **S2-DEMO-012** Demonstrate deterministic fake timeout, abort, interruption evidence, and preserved session identity.
- [x] **S2-DEMO-013** Confirm the default demonstration/test path does not require GitHub or plugin behavior.

On 2026-07-14, a freshly built wheel was installed in a disposable virtual
environment and exercised against externally launched installed OpenCode
`1.17.18` servers rooted at disposable real-submodule fixtures. The synthetic
Basic server password and username were inherited only through environment
variables; the controller received only credential-free loopback origins.
After server-generated disposable `.opencode` dependency artifacts settled,
only those fixture artifacts were removed and sprint/managed status including
ignored files was clean twice while each server remained active.

The successful full run used a configured `openai/gpt-5.6-terra` probe model.
During execution, status exposed active `auditor`, `0001-auditor`, and a fresh
session ID. The ordinary `opencode session list` client displayed the matching
`[foundation/1] auditor 0001 execution probe` title. A sanitized session
inspection verified null parent and exactly ordered permissions
`[* deny, StructuredOutput allow]`. Safe artifact inspection recorded only
hashes/shapes: validated result shape/status, prompt SHA-256, transcript-wrapper
shape/flags/content SHA-256, and metadata availability/status. It recorded the
ordered events `run.started`, `state.entered`, `server.validated`,
`agent.started`, `agent.completed`, `run.blocked`, final
`blocked/execution_not_implemented`, and expected uncommitted `info/` and
`invocations/` paths with a clean managed repository. The deterministic local
HTTP coverage records matching direct-adapter and full-controller captures from
the same fixture/route/request semantics, including complete session/message
bodies, exact schema and prompt, provider/model route, permission order, and no
`retryCount`. No raw provider response, transcript, credential, fixture, log,
virtual environment, build runtime data, or server remained after the demo.

## 30. Completion Gate

- [ ] **S2-DONE-001** Every applicable checklist item above is checked.
- [ ] **S2-DONE-002** Every Sprint 2 acceptance criterion in `sprint_spec.md` is demonstrably satisfied.
- [x] **S2-DONE-003** Narrow unit, fake-runner, fake-server, persistence, and repository tests pass during development.
- [x] **S2-DONE-004** The complete default test suite passes without external network or credentials.
- [x] **S2-DONE-005** Formatting, linting, strict typing, compilation, package build, and clean-install smoke tests pass.
- [x] **S2-DONE-006** `git diff --check` passes.
- [x] **S2-DONE-007** Parent repository status contains only intended Sprint 2 changes.
- [x] **S2-DONE-008** Plugin repository status is clean and its parent gitlink is unchanged.
- [x] **S2-DONE-009** No credentials, generated demonstration state, build artifacts, temporary repositories, or real transcripts are tracked.
- [x] **S2-DONE-010** Documentation describes actual Sprint 2 behavior and does not claim deferred features.
- [x] **S2-DONE-011** The opt-in real-server exit demonstration has been performed against a supported server.
- [ ] **S2-DONE-012** A fresh audit reports no unresolved P0 or P1 findings under the current threat model.
