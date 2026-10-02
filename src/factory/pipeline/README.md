# factory.pipeline

## Responsibility

The orchestrator: a LangGraph state machine that drives the agents (spec, architect, boundary, coder, tester, release) through the deterministic gates in `domain/gates.py`. It owns routing, bounded retry/remediation loops, the boss authorization wrapper, the single agent-call boundary (live or replay) and prompt assembly. It decides nothing by LLM: agents produce JSON, Python judges it. Application-level run lifecycle (start, resume, replay) lives one layer up in `factory.runs`.

## Modules

| Module | What it does |
|---|---|
| `__init__.py` | The PUBLIC API (`__all__`): `PipelineState`, `build_*`/`compile_*` graph builders, `resume_entry_for`, `settled_threshold_terms`, `build_spec_prompt`, `build_tester_prompt`. |
| `state.py` | `PipelineState` (a `total=False` TypedDict) and `factory_owned_paths(state)` (evidence paths to exclude from every code measurement; `()` off-project). |
| `graph.py` | Conditional edges (`should_continue_after_*`, `route_after_*`), `_AUTHORIZED_STAGES`, `build_pipeline(entry=...)`, `resume_entry_for`, the compile helpers. |
| `delivery.py` | Release = merged PR: `open_release_pr(state)` at Checkpoint 3 (GitHub PR when `origin` is on GitHub, else the story branch) and `merge_release(state)` on approval. |
| `boss.py` | `authorized(stage, node)` wrapper and `authorization_for(state, stage, gates)`; maps each stage to a rule in `domain/authorization.py`. |
| `agent_calls.py` | `run_agent_json` (run/replay + one JSON-repair retry), `_run_or_replay`, `ReplayGap`, `usage_kwargs`, `db_conn`. |
| `evidence_writers.py` | Best-effort, committed evidence: `write_chain_artifact` (intent/spec/plan/release), `commit_adr`, `write_trust_package`, `release_evidence_gaps`. |
| `nodes/` | One module per stage plus the gate nodes. See `nodes/README.md`. |
| `prompts/` | Every agent prompt. See `prompts/README.md`. |

## How it works

```
spec-agent -> gate-1 --(waiting_human: Checkpoint 1)--> END
                |
                v
   +-------> architect-agent -> [boundary-agent]* -> gate-2 --(waiting_human: Checkpoint 2)--> END
   |   ^            |                  | failed review
   |   |            +-- no boundary ---+--> back to architect (<= MAX_BOUNDARY_REDESIGNS)
   |   |                                          gate-2 pass
   |   | rearchitect                                  v
   |   +------------------------------------- coder-agent <---------------+
   |                                          | retry / next_task (self)   |
   |                                          | complete                   | remediation
   |                                          v                            | (gate-test failed)
   |                                     tester-agent -> gate-test --------+
   |                                                         | passed
   |                                                         v
   |                                              release-agent -> gate-release -> END
   |                                                         (ALWAYS waiting_human: Checkpoint 3)
   `- `release` (entry only) -> END
* only when `domain.gates.boundary_review_reasons(design)` is non-empty
```

- **One graph, many entries.** `build_pipeline(entry=...)` builds the whole line and sets the entry point; nodes before the entry are simply never reached. The `build_*_resume_*`/`compile_*` names are that graph entered at `spec-agent`, `architect-agent`, `coder-agent` or `release`.
- **Resume entry.** `resume_entry_for(gate_name, action)`: gate-1-spec approve -> `architect`, reject -> `spec`; gate-release approve -> `release`, reject -> `remediation`; anything else (gate-2) approve -> `coder`, reject -> `architect`. `runs/service.py` maps these to compiled graphs and seeds state (`prior_findings`, `triggered_by`, `remediation`, ...). `remediation` re-enters at the coder with `remediation=True`, then flows tester -> gate-test -> release again.
- **Checkpoints (what parks a run).** `gate-1` and `gate-2` park only when the gate result has `needs_human` (`finish_run(..., "waiting_human")`, status `waiting_human`; both edges then go to `END`). `gate-release` ALWAYS parks (Checkpoint 3); only `release` (operator approval) completes a story, and `node_release` is the only place a story becomes `completed`. A passing gate can still need a human.
- **Boss.** Every node in `_AUTHORIZED_STAGES` (architect, boundary, coder, tester, release-agent, release) is wrapped by `authorized`. It reads the newest verdict per gate from the DB (`latest_gates(get_run_gates)`), not graph state (resumed runs carry no gate dicts and the operator's answer lives in `gate_results.human_response`), logs the decision (`log_authorization`), and on refusal calls `finish_run("blocked")` and returns `next_action="give_up"` without calling the agent. spec-agent is the entry and unauthorised by design. The coder branch picks `authorize_remediation` vs `authorize_task` from `state["remediation"]`; `release` also checks `code_changed_since(candidate_commit)`.
- **State and status.** Nodes return partial updates. `status` in (`failed`, `blocked`) makes every later node pass the state through; the conditional edges route to `END`. Every terminal outcome a node decides must also be persisted with `finish_run` (a graph-state-only `failed` leaves the run `running`).
- **Agent-call boundary.** All nodes call `run_agent_json` -> `_run_or_replay`. With `replay_run_id` set it reads the frozen output of the same agent from the original run's `agent_logs` (`slot` = task id / `"remediation"` selects the per-task coder log via `stage_type`) and calls no model; a missing log raises `ReplayGap`. Live, it resolves the model via `agent_config.tiers.resolve_model(agent, attempt_number)` (escalates on retry) and calls `adapters.opencode.run_agent`. Unparseable output gets ONE repair re-ask (live only); if it still fails the caller sees `{"error": "Agent did not return valid JSON", ...}` and blocks the run rather than guessing.
- **Retry/remediation loops (budgets in `domain/gates.py`).** Coder: per-task retry on gate-build failure up to `MAX_CODER_ATTEMPTS`; no model call once the story has spent `MAX_STORY_COST_USD` (`agent_calls._check_budget`, and the boss before each stage); `design_feedback` -> `rearchitect` (only at task 0 / attempt 1, `MAX_REARCHITECT_LOOPS`); failed boundary review -> architect (`MAX_BOUNDARY_REDESIGNS`); failed gate-test -> coder remediation (`MAX_TESTER_REMEDIATIONS`, `attempt_number=2` forces the frontier tier). Out-of-band writes BLOCK the run (no retry).
- **Evidence.** The artifact chain (INTENT, SPEC, PLAN, release notes), ADR and trust package are written under the product repo and committed with the `factory:` prefix via `git_commit_paths`; writers swallow I/O errors.

## Dependencies

Imports: `factory.domain` (contracts, gates, authorization, task_order, traceability, ambiguity), `factory.state.db`, `factory.adapters.opencode`, `factory.agent_config`, `factory.verification`, `factory.evidence`, `factory.workspace`. Internal order (enforced by `tests/pipeline/test_public_api.py`): `graph -> {boss, nodes} -> {prompts, agent_calls, evidence_writers} -> state`.
Imported by: only `factory.runs.service` (via `factory.pipeline`); outside the package import only names in `__all__`.

## Gotchas / adding an agent node

1. Add `agents/<name>.md` and a tier in `agents/tiers.toml` (keep frontmatter in step).
2. Add a prompt module in `prompts/` and a golden fixture in `tests/fixtures/prompts/`; add a `nodes/<name>.py` that calls `run_agent_json`, validates with a Pydantic model from `domain/contracts.py`, and calls `finish_run` on every terminal state.
3. Register it in `graph._AUTHORIZED_STAGES`, add an edge/conditional edge, and add a rule to `domain/authorization.py` plus a branch in `boss.authorization_for` (it raises `ValueError` for an unknown stage).
4. If it did not exist when old runs were recorded, catch `ReplayGap` and log the stage `skipped`, or captured eval cases break.
5. Tests of mid-line graphs must seed the gate rows the boss reads from the DB. Patch `factory.pipeline.agent_calls._run_or_replay` (or `.run_agent`), where the name is looked up. Prompt/gate/graph edits are agent-configuration changes: run `make evals`.
