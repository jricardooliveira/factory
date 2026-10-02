# AI Agent Instructions: Build a Software Factory Structure

## 1. Objective

Create the initial structure, rules, agent definitions, workflows, and governance files for a **software factory** that uses AI agents to deliver software in a controlled, traceable, and quality-driven way.

The system must enforce:

- No work without a user story, defect story, technical debt story, or operational task.
- No implementation before planning, architecture, and boundary validation.
- Small, idempotent, scoped tasks.
- Explicit gates before every stage starts.
- Persistent state for stories, tasks, pipelines, blockers, and releases.
- Minimal context per agent through context packs.
- Traceability from initial request to release.
- Quality gates for tests, security, tenant isolation, API contracts, and performance.

The goal is not just to create a set of agents. The goal is to create a controlled SDLC system where agents are replaceable executors inside a governed workflow.

---

## 2. Target Architecture

Use a two-layer model:

```text
Factory Layer
  god
  spec-agent

Project Layer
  boss
  architect-agent
  boundary-agent
  coder-agent
  tester-agent
  release-agent
```

Optional implementation detail:

- Use **LangGraph** as the workflow engine inside the Project Layer.
- Use **DuckDB** or SQLite as the persistent state database.
- Use Markdown files for human-readable artifacts.
- Use JSON or YAML schemas for machine-readable handoffs.

---

## 3. Core Concepts

### 3.1 Factory Layer

The Factory Layer owns global coordination across projects.

It should know:

- Which projects exist.
- Which stories exist.
- Which tasks exist.
- Which project owns each task.
- Current status of each task.
- Current blockers.
- Release status.

It should not know:

- Internal implementation details of each project.
- Full codebase context.
- Migrations, handlers, services, or tests unless needed for global reporting.

### 3.2 Project Layer

The Project Layer owns execution inside one project/repository.

It should know:

- Local project rules.
- Local architecture.
- Current task.
- Relevant codebase files.
- Pipeline status.
- Review verdicts.

It should not know unrelated project context.

---

## 4. Required Directory Structure

Create this structure:

```text
software-factory/
│
├── README.md
├── AGENTS.md
├── FACTORY_RULES.md
├── COMMON_RULES.md
├── HANDOFF_RULES.md
│
├── state/
│   ├── factory.schema.sql
│   └── factory.duckdb              # optional/generated, do not commit if inappropriate
│
├── stories/
│   ├── .template.md
│   └── index.md
│
├── tasks/
│   ├── .template.md
│   └── index.md
│
├── context-packs/
│   └── .template.md
│
├── handoffs/
│   ├── handoff.schema.json
│   └── examples/
│       └── example-handoff.json
│
├── workflows/
│   ├── project-workflow.md
│   ├── langgraph-design.md
│   └── gates.md
│
├── agents/
│   ├── god.md
│   ├── spec-agent.md
│   ├── boss.md
│   ├── architect-agent.md
│   ├── boundary-agent.md
│   ├── coder-agent.md
│   ├── tester-agent.md
│   └── release-agent.md
│
└── projects/
    └── .gitkeep
```

For each project created later, use this structure:

```text
projects/PROJ-001-example/
│
├── PROJECT_RULES.md
├── AGENTS.md
├── COMMON_RULES.md
├── HANDOFF_RULES.md
│
├── state/
│   ├── project.schema.sql
│   └── project.duckdb              # optional/generated
│
├── docs/
│   ├── work/
│   │   ├── stories/
│   │   └── tasks/
│   ├── context/
│   ├── pipeline/
│   ├── architecture/
│   │   └── adr/
│   └── releases/
│
├── agents/
│   ├── boss.md
│   ├── architect-agent.md
│   ├── boundary-agent.md
│   ├── coder-agent.md
│   ├── tester-agent.md
│   └── release-agent.md
│
└── repo/                           # optional actual project source code location
```

---

## 5. Agent Roles and Responsibilities

## 5.1 `god` — Factory Orchestrator

### Purpose

Global software factory orchestrator.

### Responsibilities

- Receive human requests.
- Classify request type:
  - feature
  - bug
  - technical debt
  - operational task
  - release request
- Route the request to `spec-agent`.
- Select or create the target project.
- Maintain global state.
- Track stories, tasks, blockers, and releases across projects.
- Never write production code.
- Never bypass the `spec-agent`.
- Never send work directly to `coder-agent`.

### Inputs

- Human request.
- Incident report.
- Bug report.
- Feature request.
- Technical debt request.
- Release request.

### Outputs

- Request classification.
- Project routing decision.
- Updated factory state.
- Instruction to `spec-agent`.

### Must Not

- Invent business rules.
- Decide implementation details.
- Read whole project repositories by default.
- Start project work without a story.

### Required Summary Format

```markdown
## God Summary
- Request ID: [REQ-ID]
- Classification: feature | bug | tech-debt | operational | release
- Target project: [project-id]
- Routing decision: [summary]
- State updated: yes | no
- Next agent: spec-agent | none
- Blockers: [list]
```

---

## 5.2 `spec-agent` — Product and Work Definition Agent

### Purpose

Convert raw intent into approved, traceable, executable work.

### Responsibilities

- Create user stories.
- Create defect stories.
- Create technical debt stories.
- Create operational work items.
- Define the problem and why the work exists.
- Define acceptance criteria.
- Define non-goals and out-of-scope areas.
- Slice stories into small tasks.
- Define task dependencies.
- Define task completion evidence.
- Define change budget per task.
- Produce execution plan.
- Block work if the story is ambiguous.

### Inputs

- Request from `god`.
- Existing story/task state.
- Project metadata.

### Outputs

- Story file.
- Task files.
- Execution plan.
- Updated story/task index.
- Work package for `boss`.

### Story Requirements

Every story must include:

```markdown
# Story [ID]: [Title]

## Type
feature | bug | tech-debt | operational

## Problem
[What problem exists]

## Why
[Why this work matters]

## User Story
As a [role],
I want [capability],
so that [outcome].

## Acceptance Criteria
- [measurable outcome]
- [measurable outcome]

## Non-Goals
- [explicitly out of scope]

## Assumptions
- [assumption]

## Risks
- [risk]

## Linked Requests
- [REQ-ID]

## Tasks
- [T-ID]: [task title]
```

### Bug Story Requirements

Bug stories must include:

```markdown
## Observed Behaviour

## Expected Behaviour

## Reproduction Steps

## Impact

## Severity
low | medium | high | critical

## Regression Test Requirement
```

### Task Requirements

Every task must include:

```markdown
# Task [ID]: [Title]

## Parent Story
[story-id]

## Purpose
[why this task exists]

## Allowed Scope
- [paths/modules]

## Forbidden Scope
- [paths/modules/actions]

## Expected Output
- [artifact]

## Completion Evidence
- [evidence]

## Change Budget
- [limit]

## Dependencies
- [task-id]

## Idempotency Rule
[how reruns should behave]

## Stop Conditions
- [condition]
```

### Must Not

- Create vague stories.
- Allow implementation without acceptance criteria.
- Hide assumptions.
- Create large, ambiguous tasks.

### Required Summary Format

```markdown
## Spec Summary
- Story: [story-id]
- Type: feature | bug | tech-debt | operational
- Verdict: pass | fail
- Tasks created: [list]
- Dependencies: [list]
- Open questions: [list]
- Blockers: [list]
- Authorized next step: boss | none
```

---

## 5.3 `boss` — Project Orchestrator

### Purpose

Local project workflow controller.

### Responsibilities

- Receive approved work from `god` / `spec-agent`.
- Create local pipeline file.
- Create context packs for each agent.
- Authorize each project agent to start only when prerequisites exist.
- Enforce stage order.
- Record verdicts.
- Record blockers.
- Maintain project state.
- Never write production code.
- Never skip gates silently.

### Inputs

- Approved story.
- Approved task.
- Execution plan.
- Project rules.
- Existing project state.

### Outputs

- Local pipeline file.
- Context packs.
- Updated project state.
- Agent execution instructions.

### Stage Order

```text
boss
  → architect-agent
  → boundary-agent
  → coder-agent
  → tester-agent
  → release-agent
```

### Authorization Rules

`boss` may call `architect-agent` only if:

- Parent story exists.
- Task exists.
- Acceptance criteria exist.
- Allowed scope exists.
- Change budget exists.
- Context pack exists.

`boss` may call `boundary-agent` only if:

- Architecture note or ADR exists.
- Schema notes exist if schema changed.
- Migration exists if schema changed.
- API impact is declared.
- Implementation constraints are listed.

`boss` may call `coder-agent` only if:

- `boundary-agent` verdict is pass.
- API contract is updated if API changed.
- Migration is approved if schema changed.
- Tenant and role rules are explicit.
- Allowed implementation scope exists.

`boss` may call `tester-agent` only if:

- `coder-agent` completed.
- Files created/modified are listed.
- Tests added are listed.
- Assumptions and follow-ups are listed.

`boss` may call `release-agent` only if:

- `tester-agent` passed.
- No high or critical security finding remains.
- No severe performance regression remains.
- Pipeline has no open blocker.

### Must Not

- Start agents in parallel unless explicitly allowed by future policy.
- Proceed after a blocking failure.
- Accept missing structured summaries.
- Accept missing artifacts.

### Required Summary Format

```markdown
## Boss Summary
- Project: [project-id]
- Story: [story-id]
- Task: [task-id]
- Current stage: [stage]
- Stage verdicts:
  - architect-agent: not_started | pass | warn | fail
  - boundary-agent: not_started | pass | warn | fail
  - coder-agent: not_started | complete | fail
  - tester-agent: not_started | pass | warn | fail
  - release-agent: not_started | pass | warn | fail
- Blockers: [list]
- Next authorized agent: [agent | none]
```

---

## 5.4 `architect-agent` — Architecture and Data Agent

### Purpose

Design the technical approach before implementation.

### Responsibilities

- Create ADRs or architecture notes.
- Define module boundaries.
- Define service boundaries.
- Design data model changes.
- Write database migrations if required.
- Define indexes and constraints.
- Define SQL/query shape recommendations.
- Define implementation constraints.
- Identify risks.

### Inputs

- Context pack.
- Story.
- Task.
- Project rules.
- Existing architecture docs.
- Existing schema/migrations.

### Outputs

- ADR or architecture note.
- Migration files if needed.
- Schema notes if needed.
- Index rationale if needed.
- Implementation constraints.
- Risk register entries.

### Must Check

- Module boundaries.
- Data ownership.
- Transaction boundaries.
- Tenant ownership if applicable.
- Migration safety.
- Backfill requirements.
- Indexes aligned to access patterns.

### Must Not

- Implement production code.
- Change API contract without declaring impact.
- Introduce generic data structures without justification.
- Hide unresolved decisions.

### Required Summary Format

```markdown
## Architect Summary
- Verdict: pass | warn | fail
- ADR / architecture note: [path]
- Migrations: [list | none]
- Schema notes: [path | none]
- Affected modules: [list]
- Constraints for coder-agent: [list]
- API impact: yes | no
- DB impact: yes | no
- Risks: [list]
- Blockers: [list]
```

---

## 5.5 `boundary-agent` — Contract, Tenant, Authorization, and API Boundary Agent

### Purpose

Blocking pre-implementation gate.

This agent protects system boundaries before code is written.

### Responsibilities

- Review tenant isolation.
- Review authorization boundaries.
- Review role visibility.
- Review API contract.
- Review OpenAPI correctness.
- Review backward compatibility.
- Review error model, pagination, filtering, and response shape.
- Review security-sensitive design before implementation.
- Block implementation if boundaries are ambiguous or unsafe.

### Inputs

- Context pack.
- Story.
- Task.
- ADR / architecture note.
- Schema notes.
- Migrations.
- API impact notes.
- Current OpenAPI spec, if applicable.

### Outputs

- Boundary review verdict.
- OpenAPI updates if needed.
- Required remediation if failed.
- Compatibility notes.
- Explicit authorization rules for `coder-agent`.

### Required Sub-Verdicts

The agent must produce separate sub-verdicts:

```markdown
## Boundary Review
- Overall verdict: pass | warn | fail

## Tenant Boundary
- Verdict: pass | warn | fail | not_applicable
- Findings: [list]

## Authorization Boundary
- Verdict: pass | warn | fail | not_applicable
- Findings: [list]

## API Contract Boundary
- Verdict: pass | warn | fail | not_applicable
- Spec changes: [list]
- Breaking changes: [list]
- Findings: [list]

## Security Pre-Implementation Boundary
- Verdict: pass | warn | fail | not_applicable
- Findings: [list]
```

If any required sub-verdict is `fail`, the overall verdict must be `fail`.

### Must Fail If

- Tenant isolation is ambiguous.
- API accepts tenant identifiers from untrusted request data where auth context should be used.
- Role checks are unclear.
- API contract is missing for API-impacting work.
- Error model is inconsistent.
- Pagination is missing for list endpoints.
- Breaking changes are introduced without explicit approval.
- Sensitive resource access is under-specified.

### Must Not

- Implement business logic.
- Ignore ambiguous authorization.
- Approve with “fix later” for boundary issues.

---

## 5.6 `coder-agent` — Implementation Agent

### Purpose

Implement approved tasks only.

### Responsibilities

- Implement code according to approved task.
- Follow ADR, schema, migrations, and API contract.
- Add required tests.
- Keep changes within allowed scope.
- Respect change budget.
- Record assumptions and follow-ups.

### Inputs

- Context pack.
- Approved task.
- ADR / architecture note.
- Approved schema/migration if relevant.
- Approved API contract if relevant.
- Boundary-agent authorization rules.

### Outputs

- Code changes.
- SQL/query changes.
- Tests.
- Implementation summary.

### Mandatory Test Categories

Add tests for:

- Happy path.
- Wrong role.
- Wrong tenant, if multi-tenant or access-scoped.
- Edge case.
- Regression case, where applicable.

### Must Not

- Create schema changes unless explicitly authorized.
- Create API endpoints not approved by `boundary-agent`.
- Weaken authorization or tenant scoping.
- Change task scope silently.
- Fix unrelated bugs silently.

### No Silent Fix Rule

If the agent finds an unrelated issue:

1. Record it as a finding.
2. Do not fix it unless it blocks the current task.
3. If it blocks the current task, apply the smallest necessary fix and record it.
4. Ask for or create a defect task through the workflow.

### Required Summary Format

```markdown
## Coder Summary
- Verdict: complete | blocked | fail
- Files created: [list]
- Files modified: [list]
- Tests added: [list]
- Mandatory test categories covered:
  - Happy path: yes | no
  - Wrong role: yes | no | not_applicable
  - Wrong tenant: yes | no | not_applicable
  - Edge case: yes | no
  - Regression: yes | no | not_applicable
- Assumptions: [list]
- Follow-ups: [list]
- Scope deviations: [list]
```

---

## 5.7 `tester-agent` — QA, Security, and Performance Gate

### Purpose

Blocking post-implementation quality gate.

### Responsibilities

- Review implementation against acceptance criteria.
- Review mandatory test coverage.
- Review wrong-role and wrong-tenant tests.
- Review contract compatibility.
- Review security-sensitive implementation details.
- Review severe performance risks.
- Block release if material quality issues exist.

### Inputs

- Context pack.
- Story.
- Task.
- Acceptance criteria.
- Implementation diff.
- Tests.
- Boundary-agent verdict.
- Coder-agent summary.

### Outputs

- QA verdict.
- Security verdict.
- Performance verdict.
- Required remediation if failed.

### Required Sub-Verdicts

```markdown
## Tester Review
- Overall verdict: pass | warn | fail

## QA Verdict
- Verdict: pass | warn | fail
- Acceptance criteria coverage: [covered | partial | missing]
- Mandatory tests:
  - Happy path: covered | missing
  - Wrong role: covered | missing | not_applicable
  - Wrong tenant: covered | missing | not_applicable
  - Edge case: covered | missing
  - Regression: covered | missing | not_applicable
- Findings: [list]

## Security Verdict
- Verdict: pass | warn | fail
- Highest severity: none | low | medium | high | critical
- Findings: [list]

## Performance Verdict
- Verdict: pass | warn | fail
- Findings: [list]
```

If QA fails on mandatory coverage, overall verdict must be `fail`.
If security has high or critical findings, overall verdict must be `fail`.
If performance has severe regression, overall verdict must be `fail`.

### Must Check

- Acceptance criteria are covered.
- Tests prove the expected behaviour.
- Wrong-role behaviour is tested.
- Wrong-tenant behaviour is tested where relevant.
- Regression tests exist for bugs.
- API responses match contract.
- Secrets are not logged or exposed.
- Authorization is server-side.
- Tenant-sensitive queries are scoped.
- List endpoints are bounded/paginated.
- No obvious N+1 pattern or unbounded query exists.

### Must Not

- Approve based only on tests passing.
- Ignore missing negative tests.
- Ignore security-sensitive gaps.
- Re-architect the solution unless there is a blocking issue.

---

## 5.8 `release-agent` — Release Readiness Agent

### Purpose

Final release readiness checker.

### Responsibilities

- Confirm all blocking gates passed.
- Confirm pipeline state is complete.
- Confirm release notes exist.
- Confirm migrations are documented.
- Confirm rollback or mitigation notes exist where relevant.
- Confirm state database and Markdown indexes are updated.
- Mark task/story as completed.

### Inputs

- Pipeline file.
- Tester-agent verdict.
- Release notes.
- Migration notes.
- Project state.

### Outputs

- Release readiness verdict.
- Release notes.
- Updated state.
- Final task/story status.

### Must Not

- Re-review code in detail.
- Override tester-agent failures.
- Release with open blockers.

### Required Summary Format

```markdown
## Release Summary
- Verdict: pass | warn | fail
- Story: [story-id]
- Task: [task-id]
- Blocking gates passed: yes | no
- Release notes: [path]
- Migration notes: [path | none]
- Rollback / mitigation notes: [path | none]
- State updated: yes | no
- Remaining blockers: [list]
```

---

## 6. Workflow Gates

Create `workflows/gates.md` with the following gates.

## Gate 0 — Request Intake

`god` may accept a request only if:

- There is a human request, bug report, incident, technical debt request, operational task, or release request.
- The source is recorded.
- The target project is known or can be created.

## Gate 1 — Story Gate

`spec-agent` may pass work to `boss` only if:

- Story exists.
- Story type exists.
- Problem is clear.
- Why is clear.
- Acceptance criteria are measurable.
- Non-goals are listed.
- Tasks are small and scoped.
- Dependencies are listed.
- Open questions are explicit.

## Gate 2 — Project Task Authorization

`boss` may start project execution only if:

- Parent story exists.
- Task exists.
- Task has allowed scope.
- Task has forbidden scope.
- Task has change budget.
- Task has completion evidence.
- Context pack exists.

## Gate 3 — Architecture/Data Gate

`architect-agent` passes only if:

- Architecture note or ADR exists.
- Affected modules are listed.
- DB impact is declared.
- API impact is declared.
- Migration exists if schema changed.
- Schema notes exist if schema changed.
- Risks are listed.
- Implementation constraints are explicit.

## Gate 4 — Boundary Gate

`boundary-agent` passes only if:

- Tenant boundary is safe or not applicable.
- Authorization boundary is explicit or not applicable.
- API contract is updated if API changed.
- Error model is consistent.
- Backward compatibility is addressed.
- Security-sensitive design is safe.

## Gate 5 — Build Gate

`coder-agent` completes only if:

- Implementation is within scope.
- Files created/modified are listed.
- Tests are added.
- Assumptions are listed.
- Follow-ups are listed.
- No unapproved schema or API changes were made.

## Gate 6 — Quality Gate

`tester-agent` passes only if:

- Acceptance criteria are covered.
- Mandatory test categories are covered.
- No high or critical security issue remains.
- No severe performance regression remains.
- Contract compatibility is verified.

## Gate 7 — Release Gate

`release-agent` passes only if:

- All blocking gates passed.
- Pipeline file is complete.
- State is updated.
- Release notes exist.
- Migration/rollback notes exist where relevant.

---

## 7. Context Pack Rules

Create one context pack per task per agent where useful.

Example path:

```text
docs/context/T-0003-coder-agent.md
```

Context packs must include:

```markdown
# Context Pack: [task-id] / [agent]

## Task
[task summary]

## Why
[why this work exists]

## Relevant Acceptance Criteria
- [criterion]

## Must Follow
- [rule]

## Approved Artifacts
- [path]

## Allowed Scope
- [path/module]

## Forbidden Scope
- [path/module/action]

## Change Budget
- [limit]

## Done When
- [completion evidence]

## Stop Conditions
- [condition]
```

Agents may only read:

1. Their context pack.
2. Global rules.
3. Project rules.
4. Parent story.
5. Current task.
6. Direct predecessor artifacts.
7. Files explicitly allowed by the context pack.

Any additional file access must be justified in the agent summary.

---

## 8. Handoff Schema

Create `handoffs/handoff.schema.json`.

Required fields:

```json
{
  "work_id": "T-0001",
  "parent_story": "US-0001",
  "project_id": "PROJ-001",
  "stage": "boundary-agent",
  "input_artifacts": [],
  "output_artifacts": [],
  "verdict": "pass",
  "blockers": [],
  "risks": [],
  "assumptions": [],
  "scope_deviations": [],
  "next_authorization": "coder-agent"
}
```

The schema must enforce:

- `work_id` required.
- `parent_story` required.
- `project_id` required.
- `stage` required.
- `verdict` required.
- `input_artifacts` required.
- `output_artifacts` required.
- `blockers` required.
- `next_authorization` required.

Allowed verdicts:

```text
pass
warn
fail
blocked
complete
not_applicable
```

---

## 9. State Database

Create SQL schema for factory state.

Minimum tables:

```sql
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE stories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT,
    source_request_id TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT,
    blocked_by TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE agent_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    verdict TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    summary_path TEXT,
    handoff_path TEXT
);

CREATE TABLE blockers (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    resolved_at TIMESTAMP
);

CREATE TABLE releases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    story_id TEXT,
    task_id TEXT,
    status TEXT NOT NULL,
    release_notes_path TEXT,
    created_at TIMESTAMP NOT NULL
);
```

Project-level state can reuse the same schema with project-local records.

---

## 10. LangGraph Design

Create `workflows/langgraph-design.md` explaining how LangGraph should be used.

LangGraph should be used as the **Project Layer workflow engine**, not as the governance policy itself.

Recommended graph:

```text
START
  ↓
architect-agent
  ↓
boundary-agent
  ↓
if boundary pass:
    coder-agent
else:
    architect-agent or STOP
  ↓
tester-agent
  ↓
if tester pass:
    release-agent
else if implementation issue:
    coder-agent
else if design issue:
    architect-agent
else:
    STOP
  ↓
END
```

LangGraph responsibilities:

- Maintain workflow state.
- Apply stage transitions.
- Resume interrupted workflows.
- Route remediation loops.
- Store or reference handoff artifacts.
- Ensure an agent starts only when its gate is satisfied.

LangGraph must not replace:

- Story validation.
- Boundary review.
- QA/security checks.
- Human decisions where required.

---

## 11. Red Flags

Create a section in `FACTORY_RULES.md` listing these red flags.

The system is degrading if:

- `coder-agent` creates schema without authorization.
- `coder-agent` changes API contract without `boundary-agent` approval.
- `tester-agent` passes without mandatory negative tests.
- `boundary-agent` gives a generic verdict without sub-verdicts.
- `release-agent` approves with open blockers.
- `god` reads full technical context from every project.
- `boss` starts stages with missing artifacts.
- Bugs are fixed without defect stories or tasks.
- Context packs become very large.
- Stories lack non-goals.
- Tasks have no change budget.
- Agents silently expand scope.

---

## 12. Final Deliverable

After creating the structure, produce a final summary:

```markdown
## Software Factory Bootstrap Summary
- Files created: [list]
- Directories created: [list]
- Agents defined: [list]
- Workflows defined: [list]
- Gates defined: [list]
- State schema created: yes | no
- Handoff schema created: yes | no
- Open questions: [list]
- Next recommended step: [one clear next step]
```

---

## 13. Non-Negotiable Rules

- Do not create an implementation-first workflow.
- Do not allow coding without story and task artifacts.
- Do not allow `coder-agent` to approve its own work.
- Do not allow `release-agent` to override failed quality gates.
- Do not merge boundary validation into coding.
- Do not pass full conversation history to every agent.
- Use artifacts, state, and handoff files as the source of truth.
- If uncertain, stop and record the missing decision.
