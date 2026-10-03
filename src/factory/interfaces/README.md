# interfaces

## Responsibility

The delivery layer: everything an operator touches. The `factory` command line (`cli/`), the
rich-based presentation helpers (`render/`), and the interactive Textual board plus static HTML
report (`board/`). It is the top of the layering rule: it may import `runs`, `selftest`,
`preflight`, `evidence`, `workspace` and below, and NOTHING imports it except the console script
`factory = "factory.interfaces.cli.main:main"` (`pyproject.toml`). Orchestration is
`factory.runs`; this package only parses argv, calls it, and renders the result.

## cli/

`main.py` dispatches `sys.argv[1]` through `COMMANDS` to a `<verb>_command(args)` function (args =
argv after the verb). Anything that is not a verb is a free-form request that starts a run, unless
`request_refusal` rejects a typo or quoted verb before it spends tokens. Any `-h/--help` after a
verb prints only that verb's rows from `USAGE` (every verb needs a `USAGE` row; a test enforces it).

| Verb | Handler |
|---|---|
| `"<request>"` (no verb) | `run.request_command` |
| `run --project`, `approve`, `reject`, `retry`, `replay` | `run.py` (`run_command`, `approve_command`, `reject_command`, `retry_command`, `replay_command`); `RunPrinter` renders `factory.runs` events live |
| `review`, `list`, `queue`, `dismiss`, `reconcile` | `review.py` |
| `project` (`create`/`list`/`show`), `spec` (`init`) | `project.py` |
| `simulate`, `evals` (+ `capture`), `metrics`, `tiers`, `doctor` | `selftest.py` (heavy imports are lazy, inside the functions) |
| `board`, `visualize` | `board.py` (`--once`, `--plain`, `--interval`; falls back to a plain board with no TTY or no `textual`) |
| `workspace` (+ `import-legacy <dir> [--dry-run]`) | `workspace.py` |

`common.py` has `db_path` (re-exported from `factory.workspace`, resolved at call time), `fail`
(red message, exit 1), `run_id_arg`, `report_path`. `__init__.py` deliberately re-exports nothing
so `factory.interfaces.cli.main` stays the module and `patch("factory.interfaces.cli.main.x")` works.

## render/

Presentation only: functions take data and print it; they never open the DB or drive a run. The
command modules fetch, then call `render.print_*`. One module per command group: `run.py` (live
per-node lines, final status), `review.py` (`print_run_review`, `render_flow`, `render_timeline`),
`board.py` (`print_queue`, `board_renderable`, `print_runs`), `project.py`, `workspace.py`,
`selftest.py` (tiers, simulation, evals, doctor, metrics), `output.py` (the shared `console`,
`print_error`, `verdict_style`, `gate_icon`). `__init__.py` re-exports everything, and
`render.console` is resolved through a module `__getattr__` on each access, with modules using
`output.console` at call time, so a test that swaps `render.output.console` captures all output.
Its imports are limited to `domain`, `evidence.progress` and `runs` event/outcome types.
Output text is byte-for-byte tested; change it only deliberately.

## board/

| Module | What it does |
|---|---|
| `data.py` | TUI-free data layer: `BoardRun`, `load_board_runs`, `find_run`, `column_for`, `group_by_column`; columns `Needs You, Spec, Architect, Coder, Done, Blocked`. Reads only through `runs.queries.board_entries`. |
| `workflow_screen.py` | `WorkflowScreen`, the board's home: Overview (top of Needs you, what is working, the ONE next step, recent events), Needs you, Stories, Activity, Settings; state-aware project actions (Interview → Complete agreement → Amend brief…, Propose backlog, Propose batch, Pause). Every action writes a record through `runs` in a worker thread and starts the detached worker; success is a toast, failure stays on screen. |
| `answer.py` | `AnswerPicker`: a question answered like Claude Code's AskUserQuestion — options (recommended first), You decide, Other… (the only text field); posts what `domain.interview.resolve_answer` reads. Used by home and by `QuestionScreen`. |
| `views.py` | Pure text for every item (decisions, jobs, stories, events, batches, the next step, project actions): what the operator reads is unit-testable and never a JSON dump. |
| `tui.py` | `FactoryBoard` (Textual app), `run_board_tui(db_path)`; pushes home on mount. Underneath is "All runs" (Esc from home, `o` back): the run table/kanban, `a` approve `x` reject `d` dismiss `v` `p` — inert while home is on top (`check_action`). Approve/reject queue the decision (`runs.batches.queue_resume`) for the worker. |
| `interview_screen.py` | Modals: `QuestionScreen` (the pick list, for the menu's direct story run), `ReviewScreen` (read a document), `PromptScreen` (one line, e.g. "what changed?"). |
| `html_report.py` | `generate_factory_visualization(db_path, output_path)`: static HTML via `runs.queries`. |

## How it works / invariants

- Dependency direction: `interfaces -> runs -> {pipeline, verification, evidence, workspace, ...} -> domain`
  (`tests/integration/test_layout.py` allowlists what `interfaces` may import). `runs/` never
  prints and reports through an `on_event` callback; the CLI renders it, the board passes none.
- Do not import `interfaces.cli` from the board (the old console-swap hack). Policy such as the
  dismiss rule lives in `runs`, so CLI and TUI share it.
- `cli/workspace.py` is read-only for `factory workspace`: a fresh home is reported, not created.
- Tests that drive a command patch the name where the command looks it up, e.g.
  `patch("factory.runs.run_project_pipeline")`.

## Adding a CLI command

1. Write `<verb>_command(args: list[str])` in the matching `cli/<group>.py` (or a new module and
   import it in `main.py`); put business logic in `factory.runs` (or the layer below), not here.
2. Add it to `COMMANDS` and at least one row to `USAGE` in `main.py`.
3. Add its output as a `print_*` function in the matching `render/` module and export it from
   `render/__init__.py`; keep rich markup out of `cli/` where practical.
4. Test under `tests/interfaces/{cli,board}/`; a layering change is checked by `tests/integration/test_layout.py`.
