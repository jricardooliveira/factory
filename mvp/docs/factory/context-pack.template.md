# Context Pack: {{task_id}} / {{agent}}

> The orchestrator assembles one of these per agent per task. It is the agent's
> ENTIRE world: minimal, task-scoped, no whole-repo dumps. Keeping it small is
> what controls cost, keeps scope tight, and makes runs deterministic/replayable.

## Task
{{one-paragraph task summary}}

## Why
{{why this work exists — the problem, not the solution}}

## Relevant acceptance criteria
- {{criterion}}
- {{criterion}}

## Must follow
- {{rule from PROJECT_RULES.md that applies here}}
- {{constraint from a prior ADR that applies here}}

## Approved artifacts (read-only inputs)
- {{path to predecessor artifact, e.g. the architect's ADR}}
- {{path to relevant existing source file IN SCOPE}}

## Prior decisions to honor
- ADR {{id}}: {{one-line decision}} — {{path}}
- {{any settled decision the agent must not re-litigate}}

## Allowed scope
- {{path/module the agent MAY change}}

## Forbidden scope
- {{path/module/action the agent MUST NOT touch}}

## Change budget
- {{limit, e.g. "≤ 3 files, ≤ 150 LOC"}}

## Done when
- {{completion evidence — concrete, checkable}}

## Stop conditions
- {{condition that should halt the agent and produce a finding instead of pressing on}}

---

**Enforcement:** after the agent runs, the real git diff is checked against
*Allowed/Forbidden scope*; out-of-scope changes are flagged as a scope violation
(see `gate-build` scope-mismatch note).
