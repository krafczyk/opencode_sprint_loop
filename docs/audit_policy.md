# Sprint Loop Controller Audit Policy

## Purpose

This policy defines how implementation audits are performed and how findings are prioritized. Audits compare code and tests against the authoritative specifications while applying the operating assumptions in `docs/threat_model.md`.

## Required Inputs

Before auditing, read:

- `AGENTS.md`.
- `docs/v1_final_software_specification.md`.
- `docs/multi_sprint_plan.md`.
- `docs/threat_model.md`.
- The current sprint's `sprint_spec.md` and `sprint_checklist.md`.

Use the current-sprint selection rules in `AGENTS.md`. Stop and ask if selection is ambiguous.

## Finding Standard

A finding must identify:

- A violated or demonstrably incomplete requirement.
- A concrete reachable failure scenario.
- The affected code or documentation with exact locations.
- The observable impact.
- The smallest expected correction or an explicit deferral rationale.

Do not report optional improvements, style preferences, speculative refactoring, future-sprint features, or excluded threat scenarios as sprint-blocking findings.

## Assessment Dimensions

### Impact Severity

Use the V1 audit-result vocabulary:

- `blocking`: Progress or safe ordinary use is impossible without correction.
- `high`: A material sprint requirement, user-work safety property, or correctness condition is unmet.
- `normal`: A concrete sprint-completion defect remains but does not make ordinary progress unsafe.

Severity describes consequence if the scenario occurs. It does not describe probability or implementation priority.

### Occurrence Likelihood

Estimate likelihood per normal controller run under `docs/threat_model.md`:

- `frequent`: Expected during ordinary documented use.
- `likely`: Reasonably expected over normal use of the current implementation.
- `possible`: Requires an uncommon but credible non-adversarial condition.
- `unlikely`: Requires an unusual operator action or timing coincidence.
- `remote`: Requires deliberate corruption, adversarial behavior, or an exceptionally narrow race.
- `out_of_scope`: Depends on an actor or condition explicitly excluded by the threat model.

Likelihood is a reasoned qualitative estimate, not an empirical percentage. State the assumptions that drive the estimate.

### Confidence

- `high`: Directly demonstrated by code, tests, or a deterministic reproduction.
- `medium`: Strong reasoning supports the finding, but an environmental assumption remains.
- `low`: The scenario is plausible but needs confirmation before implementation.

Low-confidence items should normally be investigated before becoming Builder work.

### Priority

- `P0`: Stop current work; ordinary operation is blocked or likely to lose user work or expose credentials.
- `P1`: Fix in the current sprint before its completion gate.
- `P2`: Valid issue assigned to a named follow-up sprint or backlog.
- `P3`: Accepted prototype limitation; document if operationally relevant.
- `none`: Out of scope under the current threat model.

Priority combines requirement importance, impact, likelihood, confidence, correction cost, and current sprint ownership. Only `P0` and `P1` findings block sprint completion by default.

## Default Prioritization

- Blocking or high-impact findings with frequent, likely, or possible occurrence are normally `P0` or `P1`.
- Normal-impact findings with likely or possible occurrence are normally `P1`.
- Unlikely findings are normally `P2` unless correction is small or an absolute safety invariant is involved.
- Remote findings are normally `P3`.
- Out-of-scope findings receive priority `none` and belong in limitations, not the actionable findings list.
- An explicit specification acceptance criterion may raise priority despite low likelihood.
- The user has final authority to accept, defer, or promote findings.

## Finding Format

Use this structure:

```text
ID: AUD-001
Requirement: Sprint specification section or checklist ID
Location: path:line
Problem: Concrete contract or correctness failure
Scenario: How it occurs under the threat model
Impact: blocking | high | normal
Likelihood: frequent | likely | possible | unlikely | remote | out_of_scope
Confidence: high | medium | low
Priority: P0 | P1 | P2 | P3 | none
Disposition: fix_now | investigate | defer | accept | out_of_scope
Expected: Smallest correction or documented limitation
```

Order findings by priority, then impact. Keep limitations and residual risks separate from actionable findings.

## Builder Handoff

- The Builder implements only findings marked `fix_now` or explicitly selected by the user.
- The Builder does not automatically implement `investigate`, `defer`, `accept`, or `out_of_scope` items.
- If a finding lacks a disposition, the Builder asks for clarification rather than assuming every audit observation must be fixed.
- The Builder preserves finding IDs in its summary so audit decisions remain traceable.
- After implementation, a fresh audit reassesses the code rather than treating prior findings as automatically closed.
