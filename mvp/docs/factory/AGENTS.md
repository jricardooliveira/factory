# Agent Roster & Handoff Contract

Agents are **replaceable executors inside a governed workflow** — the pipeline and gates own the policy; agents own the work. An agent reads only its context pack (+ rules, parent story, predecessor artifacts) and returns a structured, schema-validated output. No agent approves its own work, and no agent passes full conversation history to the next.

Agent definitions live in `mvp/.opencode/agents/<name>.md` and are invoked via `opencode run --agent <name>` (`src/factory/opencode_client.py`). Each invocation records token/cost/model provenance and the **agent-definition hash**, so behaviour changes are attributable to a prompt edit vs. model drift.

---

## Roster

| Agent | Layer | Role | Status |
|---|---|---|---|
| `god` | factory | Request intake, classification, routing, cross-project state | ⛔ deferred (premature for solo) |
| `spec-agent` | project | Intent → story + acceptance criteria + sliced tasks | ✅ built |
| `boss` | project | Orchestration, context-pack assembly, gate authorization | ⛔ to build (as code, not an LLM) |
| `architect-agent` | project | Technical approach, ADR, data/API impact, risks | ✅ built |
| `boundary-agent` | project | Pre-impl gate: tenant/authz/API-contract/security sub-verdicts | ⛔ to build (Phase 5) |
| `coder-agent` | project | Implement approved task within scope; write tests | ✅ built |
| `tester-agent` | project | QA/security/performance verdicts; AC coverage | ✅ built (gate-test) |
| `release-agent` | project | Assemble trust package, release readiness | ⛔ to build |

**`boss` and `god` are intentionally not LLM agents.** Their real responsibilities are code: gate checks (`gates.py`), context-pack assembly (a builder module), and routing (the pipeline graph). Build them only if multi-project coordination becomes a real need.

---

## Built agents — output contracts

Validated by Pydantic models in `src/factory/models.py`:

- **`spec-agent` → `SpecOutput`**: `story_id, title, type, problem, why, acceptance_criteria[], non_goals[], tasks[TaskDef], verdict, questions[]`.
  - `TaskDef`: `id, title, purpose, scope[], completion_evidence, depends_on[]`. Tasks are now **executed individually** — `coder-agent` runs once per task (dependency-ordered via `context_pack.order_tasks`) against a scoped context pack (`context_pack.build_task_pack`), each task gated by `gate-build` with its own bounded retry.
- **`architect-agent` → `ArchitectOutput`**: `verdict, architecture_notes, modules_affected[], data_model, api_design, implementation_constraints[], risks[], db_impact, api_impact, migration_needed, breaking_changes[], external_dependencies[], sensitivity[]`.
- **`coder-agent` → `CoderOutput`**: `verdict, files_created[], files_modified[], tests_added[], implementation_summary, code_blocks[CodeBlock], test_coverage, assumptions[], follow_ups[]`.

To-build agents (`tester`, `boundary`, `release`) require new models with **separate sub-verdicts** (e.g. tester: QA / security / performance), per the original spec — a single `verdict` field is insufficient for trust.

---

## Handoff contract

Each stage produces a handoff that the next stage and the gates consume. Today this is the Pydantic output + `agent_logs` rows; the target machine contract is [`trust-package.schema.json`](./trust-package.schema.json). Required fields on any handoff: `work_id`, `parent_story`, `project_id`, `stage`, `verdict`, `input_artifacts`, `output_artifacts`, `blockers`, `next_authorization`. Allowed verdicts: `pass | warn | fail | blocked | complete | not_applicable`.

**Replay:** because every agent's verbatim input/output is stored, any run can be re-driven through the orchestration with `factory replay <run_id>` at zero token cost — the basis for testing agent/gate interactions offline.

---

## Rules every agent obeys

- Read only the context pack + global/project rules + parent story + current task + direct predecessor artifacts. Any extra access is justified in the summary.
- **No silent scope expansion** and **no silent fixes** of unrelated issues — record a finding and a follow-up instead.
- **Consult prior ADRs + `PROJECT_RULES.md`** (architect, coder) so decisions stay consistent.
- Return valid structured output; a malformed response is treated as `blocked`, not guessed at.
- Stay within the change budget; respect allowed/forbidden scope.
