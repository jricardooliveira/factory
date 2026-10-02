# runs/

## Responsibility

The application service for pipeline runs. It starts, replays, resumes and retries runs, curates them
(dismiss / reconcile) and provides the read side (run lists, queue, board, review bundle) that the
CLI and the Textual board render. It owns every DB write that starts, reopens or re-drives a run and
decides which compiled graph to stream. It **never prints**: progress goes out through an `on_event`
callback and refusals are raised as `RunError`.

## Modules

| Module | What it does |
|---|---|
| `__init__.py` | Public API; re-exports everything below except `queries` (imported as `factory.runs.queries`). |
| `service.py` | `run_pipeline`, `run_project_pipeline`, `replay_run`, `resume_run`, `retry_run`; private `_resume_state`, `_finish`, `_commit_pipeline_record`, `_notify_if_parked`, `_refuse_if_project_busy`, `_require_agents_link`, `_unresumable`. |
| `events.py` | Event dataclasses `RunStarted`, `NodeCompleted`, `RetryStarted`, `ResumeEntered`, `RunFinished`; `RunOutcome`; the `RunEvent` union and `OnEvent` alias; `RunError`; `ignore_events` (default callback). |
| `context.py` | Rebuilds resume inputs from the DB: `decision_from_response`, `build_resume_context` (latest spec + architecture), `last_boundary_review`, `park_unresumable`, `load_project_spec_text`. |
| `lifecycle.py` | `dismiss_run` (archive; refuses `waiting_human` and `running`), `reconcile_stale` (mark runs stuck `running` past `[timeouts] stale_run` (factory.toml, default 3600) as failed). One policy for CLI and TUI. |
| `queries.py` | Read side: `all_runs`, `run_record`, `queue`, `board`, `board_entries` / `BoardEntry`, `review_bundle` / `RunReview`, `run_stages`, `factory_report` / `FactoryReport`, `factory_metrics`. |

## How it works

- **Fresh run** (`run_pipeline`): inits the DB, creates a story (`next_story_id`, `create_story`), pins
  the repo's HEAD as the run's `base_commit`, opens the run (`start_run`), emits `RunStarted`, then
  streams `compile_pipeline()` and emits a `NodeCompleted` per node. `_finish` builds the `RunOutcome`,
  commits `PIPELINE.md`, emits `RunFinished` and notifies the operator when the run parks or fails.
- **Replay**: `replay_run` calls `run_pipeline(replay_run_id=...)`. The replay reuses the original story
  and `base_commit`, and works in a scratch clone from `workspace.sandbox.prepare_replay_sandbox`, never in
  the product. `opencode_cwd` of a replay is only the clone's source. State carries `replay_run_id`.
- **Resume** (`resume_run`): requires `waiting_human` and a pending gate. It records the decision with
  `respond_to_gate` as `APPROVED: ...` / `REJECTED: ...`, reopens the run, rebuilds context, then picks
  the graph from `pipeline.resume_entry_for(gate_name, action)`: `spec`, `architect`, `release`,
  `remediation` (Checkpoint 3 rejected: coder-only graph with `remediation=True` and
  `attempt_number=2`) or the default coder-only graph. A parked replay resumes as a replay.
- **Retry** (`retry_run`): for a `blocked`/`failed` run whose checkpoint was already answered (e.g. a
  provider error after `reject`). `requeue_answered_gate` reopens the answered gate, then `resume_run`
  replays the same decision via `decision_from_response`.
- **Event delivery**: callbacks run synchronously on the run's thread, in order. The CLI renders every
  event (`interfaces.render.run` / `cli.run.RunPrinter`); the TUI passes no callback and re-reads the DB.

Invariants and non-obvious reasons:

- Resume context uses the **latest** parseable log per agent (`get_agent_logs_for`, newest first).
  The oldest one made the operator approve one design while the coder built another. A provider-error
  row is skipped, not trusted (`_last_usable`).
- An unrecognised `human_response` is treated as an approval, never a guess at a rejection.
- Every early exit of a resume goes through `_unresumable` -> `park_unresumable` -> `finish_run(blocked)`;
  otherwise the run stays `running`, invisible to `factory queue`.
- One live run per project: `_refuse_if_project_busy` (the coder checkpoint stages the whole tree).
  Replays are exempt.
- `_require_agents_link` refuses a live project run whose `.opencode` symlink points at another checkout.
- `_commit_pipeline_record` is best-effort (swallows `OSError`/`sqlite3.Error`); evidence never fails a run.
- `queries` is how interfaces reach the DB; they must not open it themselves.

## Imports / imported by

- Imports: `factory.pipeline` (compile_* graphs, `resume_entry_for`, `PipelineState`), `factory.state`
  (`db`, `projects`, `reports`), `factory.evidence` (`pipeline_record`, `metrics`, `trust_package`,
  `progress`), `factory.workspace` (`git`, `projects`, `sandbox`), `factory.adapters.notify`, `factory.domain`.
- Imported by: `interfaces/cli/{run,review,board,selftest}.py`, `interfaces/board/{tui,data,html_report}.py`,
  `interfaces/render/run.py`, and `selftest/simulate.py` (`run_pipeline`).
- Layering (`tests/integration/test_layout.py`): interfaces -> runs -> middle layers. Middle layers other
  than `selftest.simulate` must not import `runs`; `runs` must not import `interfaces`.

## Gotchas / where to add things

- A new run-level action goes in `service.py`, emitting events rather than printing. A new event type goes
  in `events.py` and into the `RunEvent` union, then gets a renderer in `interfaces/render/run.py`.
- A new read model for a screen goes in `queries.py`. Never add a printing call here (a test forbids it).
- A new resume entry needs `graph.resume_entry_for`, a compiled graph in `pipeline`, and a branch in `resume_run`.
- Tests patch the name where it is looked up, e.g. `patch("factory.runs.run_project_pipeline")`.
- Every terminal path must reach `_finish` (and `finish_run` in the node), or the run is mislabelled later.
