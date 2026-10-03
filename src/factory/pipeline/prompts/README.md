# factory.pipeline.prompts

## Responsibility

Assembles every agent prompt from `PipelineState`. Prompt TEXT is production behaviour: it is byte-pinned by golden fixtures, so edits are deliberate agent-configuration changes. Prompt builders only read state/files/git; they never call an agent.

## Modules

| Module | Contents |
|---|---|
| `spec.py` | `build_spec_prompt` (public API): project spec + memory, operator answers when `triggered_by == "spec-rejected"`, then the request. |
| `architect.py` | `build_architect_prompt`: project context, memory, repo inventory, reviewer feedback, story, request. |
| `boundary.py` | `build_boundary_prompt`: story, design under review, and why (`boundary_review_reasons`). |
| `coder.py` | `build_coder_task_prompt` (one task, retry-aware) and `build_remediation_prompt` (tester findings or rejected release). |
| `context_pack.py` | `build_task_pack`, `build_remediation_pack`: the rendered per-task / remediation packs the coder prompts wrap. |
| `tester.py` | `build_tester_prompt` (public API): embeds `agents/policies/REVIEW.md` (`review_policy.policy_block()`), the real git diff from `base_commit`, boundary rules, the gate-build result. |
| `release.py` | `build_release_prompt(state, evidence_gaps)`: story, architecture, real diff, tester verdict, measured evidence gaps the notes must not contradict. |
| `blocks.py` | Shared blocks, each `""` when not applicable: `project_context_block`, `project_memory_block` (the approved `docs/work/BRIEF.md` first, then rules + ADRs), `repo_inventory_block`, `scope_files_block` (current text of the in-scope files — a task's scope, or every task's for a remediation pass; `repo/` stripped, a directory expanded under one size budget), `reviewer_feedback_block`, `retry_context_block`, `boundary_rules_block`. |

## How it works

Each builder concatenates blocks in a fixed order. `reviewer_feedback_block` keys off `triggered_by` (`architecture-rejected`, `design-infeasible`, `boundary-failed`) plus `prior_findings`; `retry_context_block` only appears for attempt > 1. Diffs start at `state["base_commit"]` and exclude `factory_owned_paths(state)` (the tester and release prompts), falling back to the coder's self-report off-git (tester only). Boundary rules reach the coder and tester only when `boundary_status == "reviewed"`.

**Golden pinning:** `tests/pipeline/prompts/test_prompt_golden.py` compares each assembled prompt byte-for-byte with `tests/fixtures/prompts/*.txt`. A refactor must leave them identical; a deliberate change regenerates them with `FACTORY_UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/pipeline/prompts/test_prompt_golden.py`, and the fixture diff is the review. `tests/pipeline/prompts/test_context_pack.py` covers the packs.

## Dependencies

Imports `pipeline.state`, `domain` (contracts, gates), `evidence.adr`, `evidence.brief`, `workspace` (repo_map, git), `agent_config.review_policy`. Imported by `nodes/*`; `pipeline/__init__.py` re-exports `build_spec_prompt` and `build_tester_prompt`. Prompts never import `agent_calls` or `nodes`.

## Gotchas

- Whitespace and ordering are behaviour: change a block, regenerate goldens, run `make evals`.
- New agent: add `prompts/<name>.py` composing `blocks`, a golden fixture/test, and keep the agent `.md` in step.
- Tester policy lives only in `agents/policies/REVIEW.md`, not in the agent `.md` or here.
