# CLAUDE.md — AI Software Factory

A LangGraph pipeline that drives opencode agents (spec → architect → coder → tester) through
deterministic gates to build software projects. **The factory is an agent configuration**, so
changes to `agents/*.md`, `agents/policies/REVIEW.md`, `domain/gates.py` + `domain/ambiguity.py`,
`agent_config/tiers.py`, or prompt assembly in `pipeline/` are behaviour changes and must be
regression-tested like code.

Governing contract: `docs/contract/EFFECTIVENESS.md`. When code and that doc disagree, one of
them is wrong — fix the mismatch, don't ignore it.

## Commands

All commands run from the repo root (there is no `mvp/` wrapper any more). There is no global
`pytest` — always use the venv (`uv sync --dev` creates it).

| Command | Expected output |
|---|---|
| `make check` | `365 passed` + `9/9 scenarios behaving as expected` + `43/43 checks green`. **Run before claiming done.** |
| `.venv/bin/python -m pytest -q` | `365 passed` (~40s, offline, zero tokens) |
| `.venv/bin/python -m pytest tests/verification/test_verify.py -q` | single file, for the TDD loop |
| `.venv/bin/factory simulate` | 9/9 scenario matrix, offline, zero tokens |
| `.venv/bin/factory evals` | 43/43 agent-configuration checks; exits non-zero below 100% |
| `.venv/bin/factory evals capture <run_id> <name>` | freeze a real run (or incident) as a permanent eval case |
| `.venv/bin/factory metrics` | SDLC indicators over the factory's own history, plus what is NOT measurable |
| `.venv/bin/factory replay <run_id>` | re-drives a past run's orchestration on frozen agent outputs, zero tokens |
| `.venv/bin/factory --help` | full CLI verb list |

`ruff` is configured in `pyproject.toml` (line-length 100, `E,F,I,W`) but **is not installed** in
`.venv`; `make lint` skips with a notice rather than failing.

## Architecture

```
agents/                 The agent configuration: the 4 agent .md definitions (write/edit/bash/patch
                        all disabled) + policies/REVIEW.md (injected into the tester prompt).
.opencode/agents        -> ../agents (relative symlink; opencode resolves agents through it).
src/factory/
  domain/               PURE, no I/O, imports nothing else from factory.
    contracts.py        Pydantic models for every agent output.
    gates.py            Deterministic gate policy + every budget constant (MAX_*). No LLM calls.
    ambiguity.py        Threshold-term detection behind Checkpoint 1 (THRESHOLD_TERMS, unbound_criteria).
    task_order.py       Dependency-ordered task list (Kahn sort), shared by pipeline + PLAN.md.
    traceability.py     Deterministic AC ↔ tester-claim cross-check (catches silently dropped criteria).
    agent_output.py     parse_agent_json & friends.   project_spec.py  ProjectSpec model.
  agent_config/         tiers.py (model tiers; must match agent frontmatter), review_policy.py.
  pipeline/             LangGraph nodes + conditional edges. The orchestrator. Owns routing/remediation.
    prompts/            context_pack.py (per-task prompt assembly).
  verification/         Non-LLM build verification: python.py, go.py, typescript.py, scope.py
                        (declared-scope check); verify_changes in __init__.
  evidence/             artifacts.py (INTENT → SPEC → PLAN chain), adr.py (decision memory),
                        trust_package.py (+ schemas/), metrics.py.
  workspace/            projects.py, templates.py, git.py (checkpoint commits, baseline, real diff),
                        materialize.py, repo_map.py.
  adapters/             opencode.py (the only place a model is called), notify.py.
  state/db.py           SQLite schema + every accessor. Additive migrations via _ensure_column.
  selftest/             evals.py (agent-configuration regression), simulate.py (scenario matrix).
  runs/                 Application service (run/resume/retry/replay) — being extracted from the CLI.
  interfaces/           cli/, board/ (tui.py, data.py, html_report.py). Nothing imports interfaces.
evals/cases/*.json      Behavioural eval corpus (frozen agent outputs + expected outcome).
examples/specs/         Sample project specs.
docs/contract/          EFFECTIVENESS.md (contract), GATES.md, AGENTS.md, REVIEW_QUEUE.md.
docs/design/            Historical plans/specs + the original brief.
tests/                  Mirrors src/factory/; tests/test_layout.py pins paths + the layering rule.
```

**Layering** (enforced by `tests/test_layout.py`): `interfaces → runs → {pipeline, verification,
evidence, workspace, selftest, agent_config} → domain`; adapters and state serve the middle layers.

**Design principles.** Policy is deterministic Python, never delegated to an LLM. Agents are
replaceable executors; the pipeline and gates own the governance. Every agent's verbatim
input/output is stored, so any run replays offline for free. Evidence is version-controlled
(the artifact chain + ADRs), not only DB rows.

## Conventions

- Python 3.12+, `from __future__ import annotations`, full type hints, `pathlib` over `os.path`.
- Pydantic models for every agent output — a malformed response is `blocked`, never guessed at.
- Tests in `tests/<package>/test_<area>.py`, mirroring `src/factory/`. **TDD is mandatory** (`.claude/skills/tdd`): write the
  failing test, watch it fail, then implement.
- New deterministic checks go in `verification/` / `domain/gates.py` with a unit test — not into an agent prompt.
- Budget/threshold constants live as named constants in `domain/gates.py`, never inline in node logic.
- Comments explain *why* a non-obvious decision was made, not what the line does.

## Things Claude gets wrong here

- **Never make a live `opencode` call from a test.** Orchestration is tested offline via the
  replay-fixture pattern in `tests/pipeline/test_pipeline_replay.py` + `tests/fixtures/agent_outputs/`.
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
- **`model_tier` in each agent's `.md` frontmatter must match `agent_config.tiers.AGENT_TIERS`** —
  `tests/agent_config/test_tiers.py` fails on drift. Update both.
- **The `factory:` commit-message prefix is load-bearing.** `workspace.git._factory_baseline()` finds the
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
  the `$1` budget in `domain/gates.py` has never bound. Don't write code that trusts it; see
  `factory metrics` → NOT MEASURABLE.
- Editing anything in `agents/`, `domain/gates.py`, `domain/ambiguity.py`, `agent_config/`, or
  `pipeline/` (incl. `prompts/`) is an **agent-configuration change** — run `make evals`.
  `agents/*.md` and `agents/policies/REVIEW.md` are prompt TEXT: keep edits deliberate.
- The trust package must never overstate evidence. `tests.passed` requires an executed
  suite; `diff.source` must be git-measured or `"unavailable"`.
- **A verification check must earn its verdict.** Two bugs of this shape were shipped:
  `tsc --noEmit` with no `-p` printed its HELP TEXT and exited 1 (a false FAIL on every
  monorepo), and Go was unknown to verification entirely (a false PASS on any Go repo).
  A new toolchain needs three things together: a `verification/<toolchain>.py` check, its
  `<name>_run` marker in `trust_package._TEST_RUN_MARKERS`, and any structurally
  required manifest in `verification.scope._MANIFEST_NAMES` — or the scope check cries wolf.
