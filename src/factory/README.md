# factory (source package)

The AI Software Factory: a LangGraph pipeline that drives opencode agents
(spec → architect → boundary → coder → tester → release) through deterministic
gates. Policy is plain Python; agents are replaceable executors that emit JSON.
Every agent input/output is stored, so any run can be replayed offline.

This page is the map. Each folder has its own `README.md` with its modules,
mechanics and gotchas. Governing docs: `docs/ARCHITECTURE.md`,
`docs/contract/EFFECTIVENESS.md`.

## Packages

| Package | Role |
|---|---|
| [`domain/`](domain/README.md) | Pure rules, no I/O: agent-output contracts, gate policy and budgets, ambiguity detection, task ordering, the boss's authorization rules, AC↔claim traceability. |
| [`agent_config/`](agent_config/README.md) | Loads and validates the agent configuration: `agents/tiers.toml` (agent → tier → model) and the review policy injected into the tester prompt. |
| [`adapters/`](adapters/README.md) | Outside-world edges: `opencode.py` + `claude_sdk.py` (interview only) are the only places a model is called; `notify.py` sends notifications. |
| [`state/`](state/README.md) | SQLite schema and every accessor (runs, stories, gates, agent logs, reports). The only place SQL lives. |
| [`pipeline/`](pipeline/README.md) | The orchestrator: LangGraph graph, routing/remediation, the boss wrapper, the agent-call/replay boundary. Sub-packages [`nodes/`](pipeline/nodes/README.md) (one per stage) and [`prompts/`](pipeline/prompts/README.md) (prompt assembly, golden-pinned). |
| [`verification/`](verification/README.md) | Non-LLM build checks per toolchain (Python, Go, TypeScript) and the declared-scope check. |
| [`evidence/`](evidence/README.md) | Version-controlled paper trail: INTENT → SPEC → PLAN artifacts, ADRs, trust package, metrics, progress, `PIPELINE.md`. |
| [`workspace/`](workspace/README.md) | `$FACTORY_HOME` layout, per-product git repos, materializing `code_blocks` to disk, repo map, replay scratch clones, legacy import. |
| [`runs/`](runs/README.md) | Application service: run / replay / resume / retry, resume context, queries. Reports via an `on_event` callback; never prints. |
| [`preflight/`](preflight/README.md) | `factory doctor`: checks opencode, model reachability per tier, toolchains, `$FACTORY_HOME`. |
| [`selftest/`](selftest/README.md) | The factory testing itself: agent-configuration evals (`evals/`) and the offline scenario matrix (`simulate.py`). |
| [`interfaces/`](interfaces/README.md) | CLI (`cli/`), TUI/HTML board (`board/`) and rich output helpers (`render/`). The only entry point to users. |

## Layering

Imports point one way only. The rule is enforced by
`tests/integration/test_layout.py` (the `ALLOWED` table; `KNOWN_VIOLATIONS` is empty
and may only shrink):

```
interfaces
   └─> selftest, preflight, runs
          └─> pipeline
                 └─> evidence ─> verification ─> workspace ─> agent_config
                       adapters, state ───────────────────────────────────> domain
```

Precise edges, per package (what each may import besides itself):

| Package | May import |
|---|---|
| `interfaces` | runs, selftest, preflight, evidence, workspace, agent_config, domain |
| `selftest` | runs, pipeline, evidence, verification, workspace, agent_config, state, domain |
| `preflight` | agent_config, workspace, adapters, state, domain |
| `runs` | pipeline, evidence, verification, workspace, agent_config, adapters, state, domain |
| `pipeline` | evidence, verification, workspace, agent_config, adapters, state, domain |
| `evidence` | verification, workspace, agent_config, state, domain |
| `verification` | workspace, domain |
| `workspace` | agent_config, state, domain |
| `agent_config`, `adapters`, `state` | domain |
| `domain` | nothing |

Additional guards: no module-level import cycles, no SQL outside `state`, and nothing
outside `interfaces` imports `interfaces` (the console script
`factory.interfaces.cli.main:main` is the sole entry).

## How a run flows through the packages

1. `interfaces.cli` parses a command and calls `runs` (e.g. `run_project_pipeline`).
2. `runs` resolves the project (`workspace`, `state`), builds the initial
   `PipelineState`, and invokes the graph from `pipeline`.
3. Each stage node in `pipeline.nodes` is wrapped by the **boss**, which asks
   `domain.authorization` whether the stage may start given the gate verdicts
   recorded in the DB. A refusal blocks the run; the agent is never called.
4. Agents are called only through `pipeline.agent_calls` → `adapters.opencode`
   (or replayed from stored logs). Output is parsed into `domain.contracts` models;
   a malformed response is `blocked`, never guessed at.
5. Agents can't write files: code arrives as `code_blocks`, which `workspace`
   materializes; `verification` then checks the result, and `domain.gates` decides.
6. Evidence is written and committed by `evidence` / `workspace.git`; the run is
   recorded in `state`. Some gates pass but still need a human, so runs park at
   checkpoints (spec questions, design review, release) until `factory approve`.
7. `runs` always finishes the run (`finish_run`) and reports progress via events,
   which `interfaces` render.

## Where to add things

- New deterministic rule or budget → `domain/` (+ unit test), not an agent prompt.
- New agent node → `pipeline/nodes/` + `pipeline/prompts/` + the boss rule in
  `domain/authorization.py`; it must survive replay of runs recorded before it existed.
- New toolchain check → `verification/` (see `docs/ARCHITECTURE.md` → "A new toolchain").
- New CLI command → `interfaces/cli/` (+ `render/` for output); orchestration belongs in `runs/`.
- Model choice → `agents/tiers.toml` only.

Changes to `pipeline/`, `domain/gates.py`, `domain/ambiguity.py`, `agent_config/` or
`agents/` are agent-configuration changes: run `make evals` / `make check`.
