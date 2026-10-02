# Agent Roster & Handoff Contract

Agents are **replaceable executors inside a governed workflow** — the pipeline and gates own the policy; agents own the work. An agent reads only its context pack (+ rules, parent story, predecessor artifacts) and returns a structured, schema-validated output. No agent approves its own work, and no agent passes full conversation history to the next.

Agent definitions live in `agents/<name>.md` (resolved by opencode through one relative symlink per agent in `.opencode/agents/` — not a link to the whole directory, which opencode would scan recursively and register `policies/REVIEW.md` as a fifth, write-enabled agent) and are invoked via `opencode run --agent <name>` (`src/factory/adapters/opencode.py`). Each invocation records token/cost/model provenance and the **agent-definition hash**, so behaviour changes are attributable to a prompt edit vs. model drift.

---

## Roster

| Agent | Layer | Role | Status |
|---|---|---|---|
| `god` | factory | Request intake, classification, routing, cross-project state | ⛔ deferred (premature for solo) |
| `spec-agent` | project | Intent → story + acceptance criteria + sliced tasks | ✅ built |
| `boss` | project | Orchestration, context-pack assembly, gate authorization | ✅ built — as code, not an LLM (`domain/authorization.py`, `pipeline/boss.py`, `PIPELINE.md`) |
| `architect-agent` | project | Technical approach, ADR, data/API impact, risks | ✅ built |
| `boundary-agent` | project | Pre-impl gate: tenant/authz/API-contract/security sub-verdicts | ⛔ to build (Phase 5) |
| `coder-agent` | project | Implement approved task within scope; write tests | ✅ built |
| `tester-agent` | project | QA/security/performance verdicts; AC coverage | ✅ built (gate-test) |
| `release-agent` | project | Writes the release notes (`RELEASE.md`) read at Checkpoint 3 — decides nothing | ✅ built (fast tier) |

**`boss` and `god` are intentionally not LLM agents.** Their real responsibilities are code. The boss is built as such:

- **Stage order** — one LangGraph line (`pipeline/graph.build_pipeline(entry=…)`; every resume re-enters the same graph at a different node).
- **Authorization** — before every agent stage, `pipeline/boss.authorized` reads the run's *recorded* gate verdicts (newest per gate, including the operator's checkpoint answers) and applies the pure rules in `domain/authorization.py` — the brief's "boss may call X only if…": the architect needs an accepted story; each coder task needs an approved design and its dependencies built; a remediation pass needs a failed gate-test with findings; the tester needs every task built and a green last build. A refusal **blocks** the run (persisted, agent never called) and names what is missing. Every decision is stored in the `authorizations` table — kept apart from `gate_results`, since a gate judges output and an authorization judges inputs.
- **Context packs** — `pipeline/prompts/context_pack.py`; **gate checks** — `domain/gates.py`.
- **The local pipeline file** — `docs/work/<story>/PIPELINE.md` in the product repo, rewritten and committed each time a run stops (`evidence/pipeline_record.py`, written from `runs.service._finish`): the trail of authorizations, agent and gate verdicts, the blockers, warnings and the next authorized step. A run that failed or parked no longer leaves the product repo silent about why.

`god` stays deferred: it only earns its keep once multi-project coordination is a real need.

---

## Built agents — output contracts

Validated by Pydantic models in `src/factory/domain/contracts.py`:

- **`spec-agent` → `SpecOutput`**: `story_id, title, type, problem, why, acceptance_criteria[], non_goals[], tasks[TaskDef], verdict, questions[]`.
  - `TaskDef`: `id, title, purpose, scope[], completion_evidence, depends_on[]`. Tasks are now **executed individually** — `coder-agent` runs once per task (dependency-ordered via `domain.task_order.order_tasks`) against a scoped context pack (`pipeline.prompts.context_pack.build_task_pack`), each task gated by `gate-build` with its own bounded retry.
- **`architect-agent` → `ArchitectOutput`**: `verdict, architecture_notes, modules_affected[], data_model, api_design, implementation_constraints[], risks[], db_impact, api_impact, migration_needed, breaking_changes[], external_dependencies[], sensitivity[]`.
- **`coder-agent` → `CoderOutput`**: `verdict, files_created[], files_modified[], tests_added[], implementation_summary, code_blocks[CodeBlock], test_coverage, assumptions[], follow_ups[], design_feedback`.
- **`tester-agent` → `TesterOutput`**: `overall, qa_verdict, ac_coverage[], missing_coverage[], security_verdict, highest_severity, security_findings[], performance_verdict, performance_findings[], summary`.

- **`release-agent` → `ReleaseOutput`**: `verdict, summary, changes[], how_to_verify[], migration_notes, rollback_notes, known_limitations[], concerns[]`. Of the brief's release-agent duties, everything that is a *check* — gates passed, pipeline complete, state updated, migration/rollback notes present — is deterministic code in `gate-release` and the boss; only *writing* the notes is the agent's. Its `verdict` is a concern level surfaced at Checkpoint 3, never an approval. A run recorded before the agent existed replays with the stage `skipped` (`agent_calls.ReplayGap`), so the frozen corpus keeps working.

To-build agents (`boundary`) require new models with **separate sub-verdicts**, per the original spec — a single `verdict` field is insufficient for trust.

---

## Handoff contract

Each stage produces a handoff that the next stage and the gates consume. Today this is the Pydantic output + `agent_logs` rows, plus the boss's `authorizations` (rendered for humans in each story's committed `PIPELINE.md`); the target machine contract is [`trust-package.schema.json`](../../src/factory/evidence/schemas/trust-package.schema.json). Required fields on any handoff: `work_id`, `parent_story`, `project_id`, `stage`, `verdict`, `input_artifacts`, `output_artifacts`, `blockers`, `next_authorization`. Allowed verdicts: `pass | warn | fail | blocked | complete | not_applicable`.

**Replay:** because every agent's verbatim input/output is stored, any run can be re-driven through the orchestration with `factory replay <run_id>` at zero token cost — the basis for testing agent/gate interactions offline.

**Evals:** the roster above IS configuration, so it is regression-tested. `factory evals` (`src/factory/selftest/evals/`) asserts that opencode loads exactly the registered agents from `.opencode/agents/` and nothing else, and, per agent: the definition exists; `write`/`edit`/`bash`/`patch` are all explicitly `false` (the governance invariant — an agent with tools bypasses `materialize` and the out-of-band-write check entirely); `model_tier` and `model` match `agents/tiers.toml` (loaded by `agent_config.tiers`); the prompt demands JSON-only (the orchestrator parses it as JSON); and the JSON example in the definition validates against its Pydantic model with **no unknown keys** — a field in the prompt that the model lacks is silently discarded, so the agent obeys an instruction the code ignores. `make evals` gates at 100%, and an empty suite never passes.

**Model defaults:** `agents/tiers.toml` maps frontier and standard to `openai/gpt-5.5` and fast to `openai/gpt-5.5-fast` — the models the operator's opencode login (ChatGPT/Codex) accepts; before the restructure (commit f96e09a) they were `anthropic/claude-opus-4-8`, `anthropic/claude-sonnet-4-6` and `openai/gpt-5.4-mini`. The leverage split that matters today holds: the coder runs on the fast model and escalates to a different, stronger one on retry (`factory doctor` warns if an escalation ever becomes a no-op). **standard currently equals frontier**; it only backs agents the file does not list (none today). To run elsewhere, re-point a tier per run with `FACTORY_TIER_<TIER>=provider/model` rather than editing the file.

**Review policy:** the tester's passes, severity ladder, skip list and nit cap live in [REVIEW.md](../../agents/policies/REVIEW.md) and are injected into its prompt by `agent_config.review_policy.policy_block()` (called from `pipeline/prompts/tester.py`). `tester-agent.md` keeps only the role and the JSON contract, so review behaviour is tunable in one committed file instead of split between agent prose and `domain/gates.py`.

**Committed artifacts:** the spec-agent's story and the architect's plan are written to `docs/work/<story>/{INTENT,SPEC,PLAN}.md` (`evidence/artifacts.py`) alongside the ADR (`evidence/adr.py`) — so the requirements, the order of work and the declared scope are readable off disk without the (gitignored) database. They live inside the product's own git repository (`$FACTORY_HOME/projects/<slug>/`) and are committed by the factory as they are written; agents never author them.

---

## Rules every agent obeys

- Read only the context pack + global/project rules + parent story + current task + direct predecessor artifacts. Any extra access is justified in the summary.
- **No silent scope expansion** and **no silent fixes** of unrelated issues — record a finding and a follow-up instead.
- **Consult prior ADRs + `PROJECT_RULES.md`** (architect, coder) so decisions stay consistent.
- Return valid structured output; a malformed response is treated as `blocked`, not guessed at.
- Stay within the change budget; respect allowed/forbidden scope.
