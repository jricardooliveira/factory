# factory.pipeline.nodes

## Responsibility

LangGraph node functions: one module per agent stage plus the deterministic gate nodes. A node takes `PipelineState`, calls an agent through `agent_calls.run_agent_json` (or applies a gate from `domain/gates.py`), logs to the DB, and returns a partial state update. Nodes never wire the graph (that is `graph.py`) and never print.

## Modules

| Module | Contents |
|---|---|
| `spec.py` | `node_spec_agent`: writes INTENT before the agent runs and SPEC after; sets the story title (never overwrites `state["story_id"]`). |
| `architect.py` | `node_architect_agent`: parses `ArchitectOutput`, writes the ADR (project runs, verdict != fail) and the PLAN; clears `boundary`/`boundary_status` since a new design voids the old review. |
| `boundary.py` | `node_boundary_agent`: pre-implementation boundary review; `boundary_overall` decides; a fail sets `boundary_redesign` (bounded). Off-script/crash -> `boundary_status="unavailable"`; `ReplayGap` -> `"skipped"`. |
| `coder.py` | `node_coder_agent`: one task per call (`_implement_task`) or a remediation pass (`_coder_remediation`); `_outside_scope`, `_materialize`, `_scope_diff`, `_route_after_build`, `_design_feedback`, `_block_out_of_band`. |
| `tester.py` | `node_tester_agent`: reviews the cumulative diff, returns `tester`. |
| `gates.py` | `node_gate_1`, `node_gate_2`, `node_gate_test`, `node_gate_release`, `settled_threshold_terms` (public). |
| `release.py` | `node_release_agent` (RELEASE notes; failure is an evidence gap, not a dead run) and `node_release` (operator approval taking effect; sets story + run `completed`, rewrites the trust package). |

## How it works

- **Stopped runs pass through.** Each node starts with `if status in ("failed","blocked"): return state` (coder only checks `failed`).
- **Coder, per call (working tree as edited):** build the task prompt, run the agent, then: off-script JSON -> run `blocked`; `design_feedback` -> `rearchitect` or fail; **scope pre-check** (`_outside_scope`, via `verification.scope.paths_outside_scope`) refuses out-of-scope `code_blocks` BEFORE anything is written, logs a failed gate-build and retries from a clean tree; otherwise `materialize_code_blocks` (evidence paths reserved), then `verify_changes` and the scope diff. Files that changed but were not declared (`unclaimed`) BLOCK the run; a failed verify retries the same task (`next_action="retry"`), a pass does `git_commit_all` (`factory: <task>`) and moves on (`next_task` / `complete`). Remediation uses `_story_scope` (union of all task scopes; unrestricted if any task has none) and `slot="remediation"`.
- **Gate nodes** persist their verdict with `log_gate` and `finish_run`: gate-1/gate-2 fail -> `failed`; `needs_human` -> `waiting_human` (Checkpoints 1/2). gate-test pass does NOT complete the story; fail -> remediation state (`remediation`, `prior_findings`, `tester_attempt+1`, `attempt_number=2`) or `failed` when the budget is spent. gate-release pins `candidate_commit` (`git_head`), saves the trust package, and always parks (Checkpoint 3).
- **`settled_threshold_terms`** counts only human-settled sources (project spec, PROJECT_RULES.md, the operator's checkpoint answers, ADRs whose Status is approved, never the current story's own ADR) so an agent-invented number cannot pass the ambiguity gate.

## Dependencies

Imports `domain`, `state.db`, `verification`, `workspace`, `evidence`, and (inside the package) `agent_calls`, `evidence_writers`, `prompts`, `state`. Imported only by `graph.py` (and `pipeline/__init__.py` for `settled_threshold_terms`).

## Gotchas

- Every terminal state must call `finish_run`; graph-state-only `failed` strands the run as `running`.
- An agent added after runs were recorded must catch `ReplayGap` (see `boundary.py`, `release.py`).
- Evidence paths are factory-owned: pass `exclude=factory_owned_paths(state)` to any new code measurement.
- New agent node: see "Gotchas / adding an agent node" in `../README.md` (also needs `graph._AUTHORIZED_STAGES` and a boss rule).
