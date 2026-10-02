# CLAUDE.md — AI Software Factory

A LangGraph pipeline that drives opencode agents (spec → architect → coder → tester) through
deterministic gates to build software projects. **The factory is an agent configuration**, so
changes to `.opencode/agents/*.md`, `gates.py`, `model_tiers.py`, or prompt assembly in
`pipeline.py` are behaviour changes and must be regression-tested like code.

Governing contract: `mvp/docs/factory/EFFECTIVENESS.md`. When code and that doc disagree, one of
them is wrong — fix the mismatch, don't ignore it.

## Commands

All commands run from `mvp/`. There is no global `pytest` — always use the venv.

| Command | Expected output |
|---|---|
| `make check` | `304 passed` + `9/9 scenarios behaving as expected` + `41/41 checks green`. **Run before claiming done.** |
| `.venv/bin/python -m pytest -q` | `304 passed` (~30s, offline, zero tokens) |
| `.venv/bin/python -m pytest tests/test_verify.py -q` | single file, for the TDD loop |
| `.venv/bin/factory simulate` | 9/9 scenario matrix, offline, zero tokens |
| `.venv/bin/factory evals` | 41/41 agent-configuration checks; exits non-zero below 100% |
| `.venv/bin/factory evals capture <run_id> <name>` | freeze a real run (or incident) as a permanent eval case |
| `.venv/bin/factory metrics` | SDLC indicators over the factory's own history, plus what is NOT measurable |
| `.venv/bin/factory replay <run_id>` | re-drives a past run's orchestration on frozen agent outputs, zero tokens |
| `.venv/bin/factory --help` | full CLI verb list |

`ruff` is configured in `pyproject.toml` (line-length 100, `E,F,I,W`) but **is not installed** in
`.venv`; `make lint` skips with a notice rather than failing.

## Architecture

```
mvp/src/factory/
  pipeline.py      LangGraph nodes + conditional edges. The orchestrator. Owns routing/remediation.
  gates.py         Deterministic gate policy + every budget constant (MAX_*). No LLM calls.
  verify.py        Non-LLM build verification (py_compile/pytest, go build/vet/test,
                   node --check, tsc -p <project>), git diff + declared-scope check.
  context_pack.py  Per-task prompt assembly + dependency-ordered task list (Kahn sort).
  memory.py        ADR write/read — the factory's decision memory across runs.
  artifacts.py     The committed artifact chain: INTENT.md → SPEC.md → PLAN.md.
  evals.py         Regression tests for the AGENT CONFIGURATION (config + replay cases).
  review_policy.py Loads docs/factory/REVIEW.md into the tester's prompt.
  traceability.py  Deterministic AC ↔ tester-claim cross-check (catches silently dropped criteria).
  trust_package.py Release evidence assembled on demand from stored state.
  metrics.py       The playbook's leading/lagging indicators, computed from SQLite.
  state/db.py      SQLite schema + every accessor. Additive migrations via _ensure_column.
mvp/.opencode/agents/*.md   The 4 agent definitions (write/edit/bash/patch all disabled).
mvp/evals/cases/*.json      Behavioural eval corpus (frozen agent outputs + expected outcome).
mvp/docs/factory/           EFFECTIVENESS.md (contract), GATES.md, AGENTS.md, REVIEW.md, REVIEW_QUEUE.md.
```

**Design principles.** Policy is deterministic Python, never delegated to an LLM. Agents are
replaceable executors; the pipeline and gates own the governance. Every agent's verbatim
input/output is stored, so any run replays offline for free. Evidence is version-controlled
(the artifact chain + ADRs), not only DB rows.

## Conventions

- Python 3.12+, `from __future__ import annotations`, full type hints, `pathlib` over `os.path`.
- Pydantic models for every agent output — a malformed response is `blocked`, never guessed at.
- Tests in `mvp/tests/test_<area>.py`. **TDD is mandatory** (`.claude/skills/tdd`): write the
  failing test, watch it fail, then implement.
- New deterministic checks go in `verify.py` / `gates.py` with a unit test — not into an agent prompt.
- Budget/threshold constants live as named constants in `gates.py`, never inline in node logic.
- Comments explain *why* a non-obvious decision was made, not what the line does.

## Things Claude gets wrong here

- **Never make a live `opencode` call from a test.** Orchestration is tested offline via the
  replay-fixture pattern in `tests/test_pipeline_replay.py` + `tests/fixtures/agent_outputs/`.
  Reaching for mocks to test the graph is the wrong instinct — seed `agent_logs` and set
  `replay_run_id`.
- **`projects/*/repo/` is factory *output*, not source.** Never hand-edit it, never let pytest
  collect it (`testpaths = ["tests"]` exists for this reason). It is gitignored.
- **Never commit `*.db`.** All SQLite state is gitignored and regenerable.
- **Don't overwrite `state["story_id"]` with the agent's `spec.story_id`** — the agent invents its
  own numbering; the DB row id is canonical, and clobbering it orphans later story updates.
- **Agents cannot write files.** Code reaches disk only via `code_blocks` → `materialize_code_blocks`.
  Any file that appears in the repo undeclared is an out-of-band write and *blocks* gate-build.
  Don't "fix" that by enabling agent write tools.
- **`model_tier` in each agent's `.md` frontmatter must match `model_tiers.AGENT_TIERS`** —
  `tests/test_model_tiers.py` fails on drift. Update both.
- **The `factory:` commit-message prefix is load-bearing.** `verify._factory_baseline()` finds the
  pre-factory baseline by locating the oldest commit whose subject starts with it.
- Agents prefix paths with `repo/`; `materialize.normalize_block_path` strips it. Don't double-prefix.
- A gate may **pass and still need a human** (`GateResult.needs_human`). Passing ≠ crossing a checkpoint.
  Open questions from the spec-agent PARK the run (Checkpoint 1); they are not a failure.
- **Every terminal state must call `finish_run`.** Returning `{"status": "failed"}` in graph
  state only is how a run ends up stuck `running`, invisible to `factory queue`, and later
  mislabelled by `reconcile` as a dead process.
- **Resume must read the LATEST agent log** (`build_resume_context` / `get_agent_log`, not
  `get_run_logs` + `next(...)` which is ascending). Otherwise the operator approves one
  design and the coder builds an earlier one.
- **`agent_logs.cost_usd` is NULL in every row** — opencode usage harvesting is broken, so
  the `$1` budget in `gates.py` has never bound. Don't write code that trusts it; see
  `factory metrics` → NOT MEASURABLE.
- Editing an agent `.md`, `gates.py`, `model_tiers.py`, `context_pack.py`, `pipeline.py`, or
  `docs/factory/REVIEW.md` is an **agent-configuration change** — run `make evals`.
- The trust package must never overstate evidence. `tests.passed` requires an executed
  suite; `diff.source` must be git-measured or `"unavailable"`.
- **A verification check must earn its verdict.** Two bugs of this shape were shipped:
  `tsc --noEmit` with no `-p` printed its HELP TEXT and exited 1 (a false FAIL on every
  monorepo), and Go was unknown to `verify.py` entirely (a false PASS on any Go repo).
  A new toolchain needs three things together: the check in `verify.py`, its
  `<name>_run` marker in `trust_package._TEST_RUN_MARKERS`, and any structurally
  required manifest in `verify._MANIFEST_NAMES` — or the scope check cries wolf.
