# Sprint Loop Controller Threat Model

## Purpose

This document defines the operating assumptions used to assess implementation risk during the current prototype stage. It does not override explicit requirements in the V1 software specification or an active sprint specification. It determines whether a technically valid finding is plausible enough to block current work.

## Current Environment

- The controller is prototype software operated by one trusted user in a local Linux mkchad environment.
- The source repository, sprint-history repository, managed repository, OpenCode server, and local Git processes are controlled by that user.
- Untrusted users and hostile local processes are not expected to have filesystem access to these repositories.
- The operator may accidentally start two controller processes, interrupt a process, provide malformed input, or leave a repository in an unexpected ordinary Git state.
- The operator is expected not to edit controller runtime artifacts or change Git state while a controller transition is actively executing.
- Sprint-history and managed-repository data remain important even though the controller itself is a prototype.

## Protected Assets

- Existing user work in sprint and managed repositories.
- Git history, branches, indexes, worktrees, remotes, and submodule state.
- Authoritative controller state and append-only event history.
- Credentials, tokens, private transcript content, and other secrets.
- Evidence associating agent, commit, audit, and CI decisions with the correct run and commit.

## In-Scope Failures

Audits and implementation must give attention to:

- Malformed, incomplete, oversized, unsupported, or inconsistent configuration and persistence.
- Missing files, wrong branches, dirty worktrees, uninitialized submodules, and ordinary Git operation state.
- Accidental concurrent controller launches and stale descriptive lock metadata.
- Process interruption, ordinary crashes, short writes, permission failures, disk errors, and unsupported filesystem operations at documented durability boundaries.
- Network loss, service failure, malformed external responses, timeouts, and stale external evidence when those systems enter the implemented sprint scope.
- Common operator mistakes that can occur through documented commands and normal repository use.
- Conventional credential exposure through URLs, headers, environment-derived diagnostics, configuration fields, transcripts, and external-service output.
- Deterministic implementation defects reachable without a hostile concurrent actor.

## Excluded Adversarial Scenarios

The prototype is not required to defend against:

- A hostile local process racing individual filesystem syscalls.
- Deliberate inode, hard-link, symlink, FIFO, or path substitution after successful validation solely to defeat the controller.
- Manual edits to `state.json`, `events.jsonl`, or `lock.json` during an active persistence critical section.
- Sub-millisecond staging, branch, index, or worktree changes deliberately timed between final validation and one controller-owned write.
- Deliberately forged Git object databases, refs, submodule administrative directories, or same-SHA repository impersonation.
- A compromised kernel, filesystem, Git executable, Python runtime, OpenCode server, or controller process.
- Side-channel attacks or denial of service by another local user.

These scenarios may be recorded as limitations or future hardening work. They do not block a sprint unless the user expands the threat model or the same defect is reachable through a plausible in-scope failure.

## Absolute Safety Invariants

The excluded scenarios do not relax these rules for controller-authored behavior:

- Never intentionally reset, discard, stash, broadly stage, force-push, or rewrite user work.
- Never silently launch a replacement OpenCode server.
- Never treat ambiguous CI evidence, unsupported schemas, or interrupted dirty work as success.
- Never persist or print a credential when it is recognizable through the supported credential and URL handling rules.
- Never knowingly publish implementation work with unresolved audit findings when the configured gate requires a clean result.
- Preserve completed work and fail with an actionable diagnostic when ordinary recovery cannot proceed safely.

## Risk Acceptance

- Findings reachable in ordinary operation should be fixed in the sprint that owns the behavior.
- Findings requiring uncommon but credible non-adversarial conditions should be fixed when impact is material or the correction is small.
- Findings requiring excluded adversarial timing should normally be documented and deferred.
- A deferred finding must not be silently reclassified as implemented behavior.
- The user may explicitly accept, defer, or promote any finding regardless of its default classification.

## Review Triggers

Revisit this threat model before:

- Use by multiple independent users.
- Operation in a shared or remotely writable workspace.
- Unattended long-running or privileged controller deployment.
- Processing untrusted sprint repositories or agent output.
- A production or public V1 release.
- Any claim that the controller is hardened against malicious local actors.
