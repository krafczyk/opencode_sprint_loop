# Repository Guidance

## Purpose

This repository contains the Python Sprint Loop Controller and its implementation specifications. The `opencode_sprint_loop.lua/` directory is a separate Neovim plugin repository included as a Git submodule.

Do not confuse this source repository with a sprint-history repository created for running product sprints.

## Parent Sprint Coordination

When this checkout is used under `/data0/matthew/Projects/mkchad`, the parent
workspace owns development-sprint selection. Read `../AGENTS.md` before sprint
work. Do not independently choose the first incomplete sprint or infer a
current sprint from tracker rows.

This repository currently participates in the parent selector
`controller-v1/3`, resolved by:

- `../docs/sprints/controller-v1/sprint_plan.md`
- `../docs/sprints/controller-v1/3/sprint_spec.md`
- `../docs/sprints/controller-v1/3/sprint_checklist.md`
- `docs/v1_final_software_specification.md`
- `docs/threat_model.md`
- `docs/audit_policy.md`

If sprint work is requested from this child without an explicit parent-resolved
selector, return to the coordination root or ask the user to select one. Keep
the resolved selector and exact document paths fixed for all Builder, Auditor,
and closeout handoffs. Normal controller work does not require sprint selection.

## Authoritative Documents

Read these before changing behavior:

- `docs/v1_final_software_specification.md`
- `../docs/sprints/controller-v1/sprint_plan.md`

The V1 final software specification is authoritative. If an implementation decision changes a durable schema, CLI contract, Git ownership rule, CI success rule, or terminal-state rule, update the specification deliberately rather than allowing code and documentation to diverge.

## Threat Model and Audit Policy

- Read `docs/threat_model.md` before assessing security, concurrency, recovery, or user-work risks.
- Read `docs/audit_policy.md` before performing an audit or implementing audit findings.
- Do not treat hostile local filesystem races or deliberate repository forgery as current-sprint blockers when they are excluded by the threat model.

## Runtime Agent Contract

- The V1 project-local role requirements apply to sprint-history repositories created for controller runs and to their test fixtures. Global development agents do not replace or alter that runtime product contract.

## Architecture Boundaries

- The Python controller owns workflow state, transitions, persistence, Git commits and pushes, GitHub CI evaluation, and recovery decisions.
- OpenCode is the agent execution and visibility layer, not the workflow engine.
- Require an explicitly supplied, healthy OpenCode server rooted at the sprint repository. Never silently launch a replacement server.
- Use a fresh OpenCode session for every Builder, Auditor, and CI Fixer invocation.
- The Lua plugin is a thin launcher and status client. Do not move controller logic into the plugin.
- V1 supports exactly one managed implementation repository, represented using collection-shaped configuration and state.

## Safety Rules

- Agents select and stage implementation changes and draft commit messages. The controller validates the handoff and performs commits and pushes.
- Associate CI decisions with the exact pushed implementation SHA and all applicable checks.
- Fail closed on ambiguous repository state, unknown CI conclusions, unsupported schemas, and interrupted dirty worktrees.
- Persist state atomically and keep the event log append-only.

## Development Practices

- Keep sprint implementation within the parent-resolved sprint and V1
  specification.
- Keep CLI JSON output stable and separate diagnostics from JSON standard output.
- Avoid speculative support for multi-repository workflows, other CI providers, custom dashboards, or multiplexers in V1.

## mkchad Reference Safety

- `~/.config/mkchad` is a live user environment. Never edit it or run Sprint Loop development tests against its configuration, state, server processes, or credentials.
- For isolated mkchad integration fixtures, use a task-specific directory under
  `/tmp/opencode-mkchad` and isolated XDG config, state, data, and cache paths.

## Git Submodule Workflow

Changes under `opencode_sprint_loop.lua/` belong to the plugin repository. Commit and push plugin changes from inside that repository first, then update the parent repository's submodule pointer in a separate parent commit. Do not mix unrelated controller and plugin changes.

## Verification

Inspect repository status in both the parent and plugin repositories when a change touches the submodule.
