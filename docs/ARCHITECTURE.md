# Architecture

One page: how the factory's code is laid out, which way dependencies point, and
where a new piece goes. The *why* of the factory (checkpoints, trust package, red
flags) is the governing contract, [EFFECTIVENESS.md](contract/EFFECTIVENESS.md).

---

## The repository

```
agents/                       THE AGENT CONFIGURATION (data, not code)
  spec|architect|coder|tester-agent.md   definitions; write/edit/bash/patch all false
  policies/REVIEW.md          review policy, embedded verbatim in the tester prompt
  tiers.toml                  agent -> tier -> model; the ONLY place model ids are chosen
.opencode/agents/<name>.md    one relative symlink PER AGENT (-> ../../agents/<name>.md).
                              Not a link to agents/: opencode scans its agents dir
                              recursively, so policies/REVIEW.md would load as an agent
src/factory/                  the code side (layers below)
evals/cases/*.json            behavioural eval corpus (frozen agent outputs + expected outcome)
examples/specs/               sample project specs
tests/                        mirrors src/factory/; cross-cutting suites in tests/integration/
docs/contract/                EFFECTIVENESS, GATES, AGENTS, REVIEW_QUEUE, context-pack template
docs/design/                  historical plans/specs + the original brief
docs/challenges/              challenge briefs (e.g. SupportFlow)
```

Products are **not** in this repository. They live in `$FACTORY_HOME` (default
`~/.factory`): `factory.db` plus `projects/<slug>/`, each its own git repository
holding the product's code and the evidence the factory committed for it.
`factory workspace` prints the resolved paths. Project locations are stored in
`factory.db` relative to the home, so a copied or moved home governs its own
products. `factory replay` works in `replays/run-<id>/`, a scratch clone of the
product at the replayed run's baseline — a replay never touches the product.

A built wheel carries a copy of `agents/` as `factory/_agents` (hatch
`force-include`); `agent_config.location.agents_dir()` uses the checkout's
`agents/` when there is one, else that copy, and `FACTORY_AGENTS_DIR` overrides
both. `tiers.toml` loads on first use, so read-only verbs never need it.

---

## The layers

```
                    ┌──────────────────────────────────────────────┐
                    │ interfaces   cli/  render/  board/           │  argv, rich, Textual
                    └───────┬──────────────┬──────────────┬────────┘
                            ▼              ▼              ▼
                    ┌──────────────┐ ┌───────────┐ ┌─────────────┐
                    │ selftest     │ │ preflight │ │             │  offline evals + scenarios /
                    │ (evals, sim) │ │ (doctor)  │ │             │  live environment checks
                    └──────┬───────┘ └─────┬─────┘ │             │
                           ▼               │       │             │
                    ┌──────────────────────┴───────┴─────────────┐
                    │ runs     run/resume/retry/replay · dismiss  │  application service
                    │          · queries (the read side)          │
                    └──────────────────────┬──────────────────────┘
                                           ▼
        pipeline -> evidence -> verification -> workspace -> agent_config     middle layers
                 adapters (opencode, notify)  ·  state (ALL the SQL)          infrastructure
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │ domain       PURE: no I/O, no factory imports │  policy + contracts
                    └──────────────────────────────────────────────┘
```

| Package | Owns | Must not |
|---|---|---|
| `domain/` | Pydantic agent contracts (`contracts.py`), gate policy and every `MAX_*` budget (`gates.py`), threshold-term ambiguity detection (`ambiguity.py`), task ordering + task-graph validation (`task_order.py`), the boss's authorization rules (`authorization.py`), AC ↔ tester traceability, `ProjectSpec`, agent-JSON parsing (`agent_output.py`) | do I/O or import anything else from `factory` |
| `agent_config/` | the code side of `agents/`: `location.py` (checkout `agents/`, else the wheel's bundled copy, else `FACTORY_AGENTS_DIR`), `tiers.py` loads and validates `tiers.toml` lazily (`FACTORY_TIER_*` wins), `settings.py` loads the operator's `factory.toml` (budget, timeouts, switches; env > file > default), `review_policy.py` loads `REVIEW.md` | choose a model anywhere but `tiers.toml` |
| `pipeline/` | the LangGraph orchestrator: `state.py`, `graph.py` (edges, resume routing, ONE graph builder entered at any stage), `boss.py` (authorizes every agent stage before it runs), `nodes/` (one per stage + the gate nodes; `boundary.py` = the pre-implementation boundary review; `release.py` = the release-agent and the operator's release), `prompts/` (every prompt, byte-pinned), `agent_calls.py` (the single agent-call boundary), `evidence_writers.py` (chain/ADR/trust-package writes + their `factory:` commits). `__init__` is its public API | be imported past `factory.pipeline.__all__` from outside |
| `verification/` | non-LLM build checks: `base.py` (check/result types, the subprocess runner, timeouts), `python.py`, `go.py`, `typescript.py`, `scope.py` (BOTH scope policies: declared-vs-changed, which blocks gate-build, and changed-vs-task-scope, which the trust package reports); `verify_changes` in `__init__` dispatches by file extension | judge with an LLM; run git plumbing (that is `workspace/git.py`) |
| `evidence/` | `artifacts.py` (INTENT → SPEC → PLAN), `adr.py` (decision memory), `trust_package.py` + `schemas/`, `metrics.py`, `progress.py` (per-run stage flow + timeline, plain text), `pipeline_record.py` (each story's committed `PIPELINE.md`) | overstate evidence (see CLAUDE.md); render markup (that is `interfaces/render/`) |
| `workspace/` | `layout.py` (the `$FACTORY_HOME` resolver, `EVIDENCE_PATHS`, slugs, home-relative locations), `projects.py`, `templates.py`, `git.py` (checkpoint + evidence commits, baseline, real diff), `sandbox.py` (replay clones, a run's repository), `materialize.py` (the one write chokepoint), `repo_map.py`, `legacy.py` | resolve state relative to the CWD |
| `adapters/` | `opencode.py` + `claude_cli.py` + `claude_sdk.py` (the only places a model is called; a `claude/<model>` id routes `opencode.run_agent` to `claude -p`; the SDK one only for the interview), `result.py` (`AgentResult`), `notify.py` | depend on anything but `domain` |
| `state/` | EVERY SQL statement: `db.py` (schema, connections, runs / stories / logs / gates; additive migrations), `projects.py` (the projects table), `reports.py` (read-only aggregates) | default a DB path (the caller passes `workspace.db_path()`) |
| `runs/` | `service.py` (run / resume / retry / replay), `lifecycle.py` (dismiss + reconcile, one policy for CLI and TUI), `queries.py` (the read side the interfaces render), `context.py` (resume context, decision recovery), `events.py` (`on_event` callback types, `RunError`) | print, or import `interfaces` / `selftest` |
| `selftest/` | offline and zero-token: `evals/` (`config`, `cases`, `capture`, `report`), `simulate.py` (scenario matrix, driven through `runs.run_pipeline`) | import `interfaces`, or spend a token |
| `preflight/` | `doctor.py`: the live environment check (opencode, one probe per tier model, toolchains, tiers.toml, escalation, the workspace) | be part of the zero-token evals contract |
| `interfaces/` | `cli/main.py` (argv dispatch + usage + the typo guard), one module per command group, `render/` (one module per command group + `output.py`; takes data, never reads the DB), `board/` (`tui.py` app + All runs, `workflow_screen.py` home, `answer.py` pick list, `views.py` item text, `interview_screen.py` modals, `data.py`, `html_report.py`) | import `state`, `pipeline`, `adapters` or `verification` (go through `runs`); be imported by anything but the console script |

---

## The one-way dependency rule

```
interfaces -> {selftest, preflight, runs}      selftest -> runs
runs -> pipeline -> evidence -> verification -> workspace -> agent_config -> domain
adapters -> agent_config -> domain   (adapters and verification read settings.py)
state -> domain
```

- **`domain` imports nothing from `factory`.** Policy stays pure and unit-testable.
- **`adapters` and `state` serve the middle layers** and may depend only on `domain`.
- **`state` owns every SQL statement.** Nothing else calls `.execute`.
- **`interfaces` go through `runs`** for anything that touches a run or the
  database; they never import `state`, `pipeline`, `adapters` or `verification`.
- **`selftest` sits above `runs`**: the scenario matrix and every replay eval drive
  `runs.run_pipeline`, the same entry path the CLI and the TUI use.
- **Nothing imports `interfaces`**, except the console script
  (`factory.interfaces.cli.main:main` in `pyproject.toml`). `runs` and `selftest`
  report through return values and callbacks, never by printing.
- `interfaces/render/` is presentation only: it imports `domain`, `runs` (event
  types) and `evidence.progress` (stage data), nothing else.

Enforced by [`tests/integration/test_layout.py`](../tests/integration/test_layout.py):
an explicit `ALLOWED` table per package, every import resolved with `ast`
(function-level, relative and `importlib.import_module` ones included), a Tarjan
check that there is no import cycle (module-level or lazy), and a check that no
SQL runs outside `factory.state`. Its `KNOWN_VIOLATIONS` / `KNOWN_CYCLES`
allowlists are empty and may only shrink. Inside `pipeline/`,
[`tests/pipeline/test_public_api.py`](../tests/pipeline/test_public_api.py) pins the
public API and the internal order graph → nodes → prompts/agent_calls → state.

---

## Where a new piece goes

### A new toolchain (e.g. Java, Rust)

All three together, or the gate cries wolf or passes silently:

1. `src/factory/verification/<toolchain>.py` with the checks, dispatched from
   `verify_changes` in `verification/__init__.py` by file extension. Hard **fail**
   only on a real compile error; **warn** when the toolchain or a dependency is
   missing. A check must earn its verdict: prove it fails on broken code and passes
   on good code.
2. Its test-run marker (like `pytest_run`, `go_test`) in
   `evidence/trust_package._TEST_RUN_MARKERS` and `_TEST_RUN_COMMANDS`, so an
   executed suite is recognised and the package names the command that ran.
3. Any structurally required manifest (`pom.xml`, `Cargo.toml`, …) in
   `verification/scope._MANIFEST_NAMES`, so the scope check does not flag it.

Tests go in `tests/verification/`. Add the binary to `factory doctor`
(`preflight/doctor.py`) if the operator needs it on `PATH`.

### A new gate

1. The verdict logic in `domain/gates.py` (pure; returns a `GateResult`, sets
   `needs_human` when a person must decide), with any threshold as a named `MAX_*`
   constant there. Unit-test it in `tests/domain/`.
2. A gate node in `pipeline/nodes/gates.py` that gathers the inputs from state and
   persists the result with `state.db.log_gate` (the `gate_results` table).
3. The edges in `pipeline/graph.py`, plus `resume_entry_for` if it can park.
4. A row in [GATES.md](contract/GATES.md) and the §8 table of EFFECTIVENESS.md,
   then `make evals` (a gate change is an agent-configuration change).

### A new agent

1. `agents/<name>-agent.md` with `write`, `edit`, `bash`, `patch` all `false`, and
   frontmatter `model_tier:` + `model:` matching `agents/tiers.toml`.
2. Its tier in `agents/tiers.toml` `[agents]` (and `[escalate_on_retry]` if a retry
   should go one tier up).
3. Its output model in `domain/contracts.py`, registered in
   `selftest/evals/config.OUTPUT_MODELS` (and `NESTED_MODELS` for nested shapes) so the
   JSON example in the definition is diffed against it.
4. Its prompt builder in `pipeline/prompts/<name>.py` with a golden fixture in
   `tests/fixtures/prompts/`, its node in `pipeline/nodes/<name>.py`, and its
   edges in `pipeline/graph.py`. Calls go through `pipeline/agent_calls.py` only.
   Register the node in `graph._AUTHORIZED_STAGES` (so the boss wraps it) and give it an
   authorization rule in `domain/authorization.py` + `pipeline/boss.authorization_for`.
5. A relative link `.opencode/agents/<name>-agent.md -> ../../agents/<name>-agent.md`
   (the eval `opencode-loads-exactly-the-agents` fails without it).
6. A replay fixture under `tests/fixtures/agent_outputs/` and a row in
   [AGENTS.md](contract/AGENTS.md). Then `make check`.

### A new CLI command

1. The behaviour in the layer that owns it — orchestration in `runs/`, a
   measurement in `evidence/`, a workspace operation in `workspace/` — returning
   data, never printing.
2. A `<verb>_command(args)` in the matching `interfaces/cli/<group>.py` (`run`,
   `review`, `project`, `selftest`, `board`, `workspace`), any output helper in
   the matching `interfaces/render/<group>.py`. A read the command needs goes in
   `runs/queries.py`, never a `state` import in the interface.
3. Register the verb in `COMMANDS` and `print_usage` in `interfaces/cli/main.py`.
   Tests in `tests/interfaces/cli/`; they patch the name where the command looks it
   up (e.g. `patch("factory.runs.run_project_pipeline")`).

If the TUI needs the same behaviour, it calls the same `runs/` function with no
callback — never the CLI module.
