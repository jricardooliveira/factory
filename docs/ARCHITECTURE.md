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
.opencode/agents -> ../agents relative symlink; opencode resolves agents through it
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
`factory workspace` prints the resolved paths.

---

## The layers

```
                    ┌──────────────────────────────────────────────┐
                    │ interfaces   cli/  render.py  board/         │  argv, rich, Textual
                    └──────────────────────┬───────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │ runs         run / resume / retry / replay   │  application service
                    └──────────────────────┬───────────────────────┘
                                           ▼
  ┌──────────┬──────────────┬──────────┬───────────┬──────────┬──────────────┐
  │ pipeline │ verification │ evidence │ workspace │ selftest │ agent_config │  middle layers
  └────┬─────┴──────┬───────┴────┬─────┴─────┬─────┴────┬─────┴──────┬───────┘
       │            │  adapters (opencode, notify)  ·  state (db)     │          infrastructure
       ▼            ▼            ▼           ▼          ▼            ▼
                    ┌──────────────────────────────────────────────┐
                    │ domain       PURE: no I/O, no factory imports │  policy + contracts
                    └──────────────────────────────────────────────┘
```

| Package | Owns | Must not |
|---|---|---|
| `domain/` | Pydantic agent contracts (`contracts.py`), gate policy and every `MAX_*` budget (`gates.py`), threshold-term ambiguity detection (`ambiguity.py`), task ordering (`task_order.py`), AC ↔ tester traceability, `ProjectSpec`, agent-JSON parsing (`agent_output.py`) | do I/O or import anything else from `factory` |
| `agent_config/` | the code side of `agents/`: `tiers.py` loads and validates `tiers.toml` (`FACTORY_TIER_*` wins), `review_policy.py` loads `REVIEW.md` | choose a model anywhere but `tiers.toml` |
| `pipeline/` | the LangGraph orchestrator: `state.py`, `graph.py` (edges, resume routing, graph builders), `nodes/` (one per stage + gate nodes + evidence), `prompts/` (every prompt, byte-pinned), `agent_calls.py` (the single agent-call boundary). `__init__` is its public API | be imported past `factory.pipeline.__all__` from outside |
| `verification/` | non-LLM build checks: `python.py`, `go.py`, `typescript.py`, `scope.py` (declared-scope + manifest exemptions); `verify_changes` in `__init__` dispatches by file extension | judge with an LLM; run git plumbing (that is `workspace/git.py`) |
| `evidence/` | `artifacts.py` (INTENT → SPEC → PLAN), `adr.py` (decision memory), `trust_package.py` + `schemas/`, `metrics.py`, `progress.py` (per-run stage flow) | overstate evidence (see CLAUDE.md) |
| `workspace/` | `layout.py` (the `$FACTORY_HOME` resolver, `EVIDENCE_PATHS`), `projects.py`, `templates.py`, `git.py` (checkpoint + evidence commits, baseline, real diff), `materialize.py` (the one write chokepoint), `repo_map.py`, `legacy.py` | resolve state relative to the CWD |
| `adapters/` | `opencode.py` (the only place a model is called), `notify.py` | depend on anything but `domain` |
| `state/` | `db.py`: the SQLite schema and every accessor; additive migrations | default a DB path (the caller passes `workspace.db_path()`) |
| `selftest/` | `evals.py` (agent-configuration regression), `simulate.py` (scenario matrix), `doctor.py` (preflight) | import `interfaces` |
| `runs/` | `service.py` (run / resume / retry / replay), `context.py` (resume context, decision recovery), `events.py` (`on_event` callback types, `RunError`) | print, or import `interfaces` |
| `interfaces/` | `cli/main.py` (argv dispatch + usage), one module per command group, `render.py` (every rich print helper; takes data, never reads the DB), `board/` (`tui.py`, `data.py`, `html_report.py`) | be imported by anything (except the console script) |

---

## The one-way dependency rule

```
interfaces -> runs -> {pipeline, verification, evidence, workspace, selftest, agent_config} -> domain
```

- **`domain` imports nothing from `factory`.** Policy stays pure and unit-testable.
- **`adapters` and `state` serve the middle layers** and may depend only on `domain`.
- **The middle layers never import `runs`** — `runs` sits above them.
- **Nothing imports `interfaces`**, except the console script
  (`factory.interfaces.cli.main:main` in `pyproject.toml`). `runs` and `selftest`
  report through return values and callbacks, never by printing.
- `interfaces/render.py` is presentation only: it may not reach `state`,
  `pipeline`, `adapters` or `workspace`.

Enforced by [`tests/integration/test_layout.py`](../tests/integration/test_layout.py),
which walks every import (including function-level ones) with `ast`. Its
`KNOWN_VIOLATIONS` allowlist is empty and may only shrink. Inside `pipeline/`,
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
(`selftest/doctor.py`) if the operator needs it on `PATH`.

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
   `selftest/evals.OUTPUT_MODELS` (and `NESTED_MODELS` for nested shapes) so the
   JSON example in the definition is diffed against it.
4. Its prompt builder in `pipeline/prompts/<name>.py` with a golden fixture in
   `tests/fixtures/prompts/`, its node in `pipeline/nodes/<name>.py`, and its
   edges in `pipeline/graph.py`. Calls go through `pipeline/agent_calls.py` only.
5. A replay fixture under `tests/fixtures/agent_outputs/` and a row in
   [AGENTS.md](contract/AGENTS.md). Then `make check`.

### A new CLI command

1. The behaviour in the layer that owns it — orchestration in `runs/`, a
   measurement in `evidence/`, a workspace operation in `workspace/` — returning
   data, never printing.
2. A `<verb>_command(args)` in the matching `interfaces/cli/<group>.py` (`run`,
   `review`, `project`, `selftest`, `board`, `workspace`), any output helper in
   `interfaces/render.py`.
3. Register the verb in `COMMANDS` and `print_usage` in `interfaces/cli/main.py`.
   Tests in `tests/interfaces/cli/`; they patch the name where the command looks it
   up (e.g. `patch("factory.runs.run_project_pipeline")`).

If the TUI needs the same behaviour, it calls the same `runs/` function with no
callback — never the CLI module.
