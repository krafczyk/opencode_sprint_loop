# Repository Guidance

## Purpose

This repository contains the Python Sprint Loop Controller and its implementation specifications. The `opencode_sprint_loop.lua/` directory is a separate Neovim plugin repository included as a Git submodule.

Do not confuse this source repository with a sprint-history repository created for running product sprints.

## Authoritative Documents

Read these before changing behavior:

- `docs/v1_final_software_specification.md`
- `docs/multi_sprint_plan.md`

The V1 final software specification is authoritative. If an implementation decision changes a durable schema, CLI contract, Git ownership rule, CI success rule, or terminal-state rule, update the specification deliberately rather than allowing code and documentation to diverge.

## Architecture Boundaries

- The Python controller owns workflow state, transitions, persistence, Git commits and pushes, GitHub CI evaluation, and recovery decisions.
- OpenCode is the agent execution and visibility layer, not the workflow engine.
- Require an explicitly supplied, healthy OpenCode server rooted at the sprint repository. Never silently launch a replacement server.
- Use a fresh OpenCode session for every Builder, Auditor, and CI Fixer invocation.
- The Lua plugin is a thin launcher and status client. Do not move controller logic into the plugin.
- V1 supports exactly one managed implementation repository, represented using collection-shaped configuration and state.

## Safety Rules

- Never reset, discard, stash, broadly stage, force-push, or rewrite user work automatically.
- Agents select and stage implementation changes and draft commit messages. The controller validates the handoff and performs commits and pushes.
- Associate CI decisions with the exact pushed implementation SHA and all applicable checks.
- Fail closed on ambiguous repository state, unknown CI conclusions, unsupported schemas, and interrupted dirty worktrees.
- Persist state atomically and keep the event log append-only.
- Do not store credentials in state, events, transcripts, prompts, process arguments, or committed fixtures.
- Preserve partial agent work after interruption and report an actionable blocked state.

## Development Practices

- Prefer the smallest implementation that satisfies the current sprint and V1 specification.
- Keep external systems behind narrow interfaces so tests can use deterministic fakes.
- Use machine-readable Git, GitHub, and OpenCode responses where available; do not parse presentation-oriented output.
- Keep CLI JSON output stable and separate diagnostics from JSON standard output.
- Add tests for success, failure, interruption, and recovery paths when changing stateful behavior.
- Keep real OpenCode and GitHub integration tests opt-in. Default tests must not require credentials, model usage, or network access.
- Avoid speculative support for multi-repository workflows, other CI providers, custom dashboards, or multiplexers in V1.
- Be sure to document public API methods with docstrings.
- Update documentation in the same change as user-visible behavior, CLI options, configuration, schemas, state transitions, recovery behavior, or external integration requirements.
- Keep examples, command output, and JSON fragments consistent with the implemented contracts and covered by tests where practical.
- Document operational limitations and failure behavior explicitly. Do not present planned or speculative behavior as implemented.

## Git Submodule Workflow

Changes under `opencode_sprint_loop.lua/` belong to the plugin repository. Commit and push plugin changes from inside that repository first, then update the parent repository's submodule pointer in a separate parent commit. Do not mix unrelated controller and plugin changes.

## Verification

Run the narrowest relevant tests while developing, followed by the full available test suite before declaring work complete. Also inspect repository status in both the parent and plugin repositories when a change touches the submodule.
