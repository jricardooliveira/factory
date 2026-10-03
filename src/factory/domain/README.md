# factory.domain

## Responsibility

The pure core of the factory: the Pydantic contracts every agent must satisfy, the deterministic
gate policy (with every budget constant), the ambiguity rules behind Checkpoint 1, the "boss"
authorization rules, task ordering and acceptance-criteria traceability. No I/O, no LLM calls, no
database, and no import of any other `factory` package. Policy lives here as plain Python so two
runs with the same record always get the same answer.

## Modules

| Module | What it does |
|---|---|
| `contracts.py` | Pydantic models for agent output: `TaskDef`, `SpecOutput`, `ArchitectOutput`, `CodeBlock`, `CoderOutput`, `TestCoverage`, `TesterOutput`, `ReleaseOutput`, `BoundaryOutput` (+ `BoundaryVerdict`, `ApiContractVerdict`). Verdict fields are plain `str` with defaults. |
| `gates.py` | `GateResult` and the gate functions `gate_after_spec` (gate-1-spec), `gate_after_architect` (gate-2-architect), `gate_after_tester` (gate-test), `gate_after_release` (gate-release); boundary helpers `boundary_review_reasons`, `boundary_overall`, `boundary_findings`; `changes_schema`. Holds the budget constants `MAX_TASKS_PER_STORY`, `MAX_MODULES_PER_STORY`, `MAX_CODER_ATTEMPTS`, `MAX_STORY_COST_USD`, `MAX_TESTER_REMEDIATIONS`, `MAX_REARCHITECT_LOOPS`, `MAX_BOUNDARY_REDESIGNS`. |
| `ambiguity.py` | `THRESHOLD_TERMS` (words like "overdue", "large" that force an implementation to invent a number), `is_bound`, `defined_threshold_terms`, `unasked_request_terms`, `unbound_criteria`. |
| `authorization.py` | `GateRecord` / `latest_gates` (recorded verdicts), `Authorization`, and `authorize_architect / _task / _remediation / _tester / _release_notes / _release / _boundary`: may this stage START? |
| `budget.py` | `ModelPrice`, `Spend`, `call_cost` (provider cost, else tokens × list price, else unknown), `story_spend`, `budget_refusal`: the $10-per-story cap's arithmetic. |
| `task_order.py` | `order_tasks` (forgiving Kahn sort) and `dependency_problems` (strict reading: duplicates, self-deps, unknown deps, cycles). |
| `traceability.py` | `trace_criteria` classifies each acceptance criterion as `covered`, `flagged_missing` or `unassessed` against the tester's claims; `unassessed_criteria`. |
| `agent_output.py` | `parse_agent_json` (fenced ```json block first, then outermost braces; `None` if nothing parses) and `parse_date` (currently has no caller outside `domain`). |
| `project_spec.py` | `ProjectSpec` model (stack, conventions, infra limits, existing modules/APIs) and `to_architect_context()` rendering. |

## How it works

- **Gates judge output after a stage; authorization judges inputs before it.** A `GateResult`
  carries `passed` and, separately, `needs_human` + `human_questions`. A gate can pass and still
  park the run (Checkpoints 1-3). `gate_after_release` always sets `needs_human=True`: `passed`
  only means the evidence bar is met, never "released".
- **Gate-1 has two independent park triggers**: the spec-agent's own `questions` (a `fail`
  verdict *with* questions is honoured as a question, not a rejection) and the deterministic
  `unbound_criteria` check, so a threshold nobody authorised cannot slip through on model luck.
  Structural failures (no title, <2 AC, no tasks, too many tasks, a broken task graph) reject
  outright instead of becoming a checkpoint.
- **Ambiguity is deliberately narrow.** Quality adjectives ("appropriate") are excluded from
  `THRESHOLD_TERMS` so the gate does not park every story. A term the *request* used without a
  number needs sign-off even if the agent later writes a number into a criterion (an invented
  value is visible, not authorised). Terms already present in committed project context are
  "defined" and not re-asked. Number detection matches whole words, not substrings.
- **Boundary verdict is recomputed, never trusted**: `boundary_overall` fails if any dimension
  (`tenant`, `authorization`, `api_contract`, `security`) fails, regardless of the agent's `overall`.
  `boundary_review_reasons` decides from the architect's declarations whether a review is needed.
- **`order_tasks` never crashes** (unknown deps ignored, cyclic leftovers appended); gate-1 uses
  `dependency_problems` to be strict, so forgiveness cannot hide a mis-ordered build.
- **Authorization** works on `GateRecord`s built from DB rows (`GateRecord.from_row`); the newest
  row per gate wins. A rejection counts only when `human_response` starts with `REJECTED:`.
  `authorize_release` also refuses if the reviewed candidate changed (`candidate_changed`).
  Blocking gaps go in `Authorization.missing`; non-blocking ones in `warnings`.
- **Traceability**: a claim that opens with the criterion's number (`AC2 ...`, `AC10 – ...`, `3. ...`)
  speaks for that criterion only; an unnumbered claim is matched by token overlap (>= 0.5 of the
  smaller set) so paraphrases still match. An AC is `unassessed` only when the tester ignored it entirely.

## Dependencies

Imports nothing from other `factory` packages (only intra-package imports: `gates` -> `ambiguity`,
`contracts`, `task_order`; `authorization` -> `contracts`, `gates`). Imported by `pipeline`
(gates, boss, nodes, prompts), `evidence`, `runs`, `selftest`, `workspace` (`ProjectSpec`) and
`interfaces`. `tests/integration/test_layout.py` enforces the one-way rule.

## Where to add things

- A new deterministic check or budget: a named constant and function in `gates.py` (or
  `ambiguity.py`), with a test in `tests/domain/`. Not in an agent prompt.
- A new acting agent: a contract in `contracts.py`, an `authorize_*` rule here, and register the
  stage in `pipeline/graph._AUTHORIZED_STAGES`.
- Editing `gates.py` or `ambiguity.py` is an agent-configuration change: run `make evals`.
