# selftest/

## Responsibility

Offline, zero-token checks that the factory itself behaves: the scenario matrix (`simulate`) and the
agent-configuration regression suite (`evals/`). Both drive the real orchestration over frozen agent
outputs, so a change to `agents/*.md`, gates, tiers or prompt assembly is regression-tested like code.
Nothing here calls opencode. (The live, paid environment check is `preflight/`, not this package.)

## Modules

| Module | What it does |
|---|---|
| `__init__.py` | Empty. |
| `simulate.py` | `Scenario`, `ScenarioResult` (`.passed`), builders `_task/_spec/_arch/_boundary/_coder`, `CATALOG` (12 scenarios), `run_scenario`, `simulate_all`, `render_markdown`, `_seed_original`. |
| `evals/__init__.py` | Public API and `run_all(agents_dir=None, cases_dir=None) -> EvalReport` (config checks + replay cases). |
| `evals/config.py` | `config_checks`: per-agent invariants over `agents/*.md` (definition exists, write/edit/bash/patch disabled via `FORBIDDEN_TOOLS`, tier and model match `agent_config.tiers`, demands JSON-only, JSON example matches the Pydantic model in `OUTPUT_MODELS`/`NESTED_MODELS`), `opencode-loads-exactly-the-agents`, review-policy checks. |
| `evals/cases.py` | `load_cases` (reads `evals/cases/*.json` at the checkout root via `default_cases_dir`), `run_case`. |
| `evals/capture.py` | `capture_case`: freezes a stored run's agent outputs into a case file. |
| `evals/report.py` | `EvalResult`, `EvalCase`, `EvalReport` (`pass_rate`, `failures`, `passed`), `PASS_THRESHOLD = 1.0`, `render_markdown`. |

## How it works

- **simulate**: `run_scenario` makes a throwaway SQLite DB, seeds an "original" run whose `agent_logs` hold the
  scenario's canned outputs (a passing tester is added if absent; coder outputs are keyed per task id), then
  calls `runs.run_pipeline(replay_run_id=..., notify_operator=False, prepare_workdir=scenario.setup)`. That is
  the same entry the CLI and TUI use, so sandbox, base-commit pin and finish logic are exercised. A scenario
  passes when the final status equals `expected_status`; any exception becomes status `ERROR`. Flow text comes
  from `evidence.progress.plain_flow`.
- **evals/config**: deterministic invariants over the agent definitions and their registries. They catch
  silent drift such as a re-enabled write tool (which bypasses `materialize` and the out-of-band-write gate),
  a stale `model_tier`, or a prompt JSON contract with fields the Pydantic model would silently drop.
- **evals/cases**: a case JSON holds `agent_outputs`, `expect.status`, optional `expect.gates`, `git`, `source_run_id`.
  `run_case` wraps it in a `Scenario` and reuses `simulate.run_scenario`, then compares status and gate verdicts.
- **capture**: `capture_case` takes the last output per agent (coder keyed by `stage_type`), skips empty or
  `skipped` rows, and writes `evals/cases/<name>.json`. For a `completed` run the expectation is
  `waiting_human` with `gate-test` passed, because release is the operator's act and a replay parks at Checkpoint 3.
- **Gate**: `EvalReport.passed` needs a non-empty suite at `PASS_THRESHOLD` (100%). An empty suite fails on
  purpose, since a vacuous gate rots into decoration.

## Imports / imported by

- Imports: `factory.runs` (`simulate` only: `run_pipeline`), `factory.evidence.progress`, `factory.state.db`,
  `factory.workspace.git` (`git_init`), `factory.agent_config` (`tiers`, `location`, `review_policy`),
  `factory.domain.contracts` / `agent_output`.
- Imported by: `interfaces/cli/selftest.py` (`simulate_command`, `evals_command`, lazy imports). The layering
  test explicitly allows `selftest.simulate -> runs.service` and forbids `selftest -> interfaces`.
- CLI: `factory simulate`, `factory evals`, `factory evals capture <run_id> <name>`; `make check` runs both.

## Gotchas / where to add things

- New behavioural regression: run `factory evals capture <run_id> <name>`, or add a `Scenario` to `CATALOG`
  (then update the "12/12" counts in CLAUDE.md and docs).
- New agent: register its output model in `OUTPUT_MODELS`, and its tier in `agents/tiers.toml`.
- A new agent must survive replay of old runs (`agent_calls.ReplayGap` -> stage `skipped`) or captured cases break.
- Cases and scenarios need the boss's gate rows to be real; they get them because they replay through the full pipeline.
- Do not mock the graph here; seed `agent_logs` and set `replay_run_id`.
