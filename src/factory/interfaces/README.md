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
| `board_app.py` | `BoardApp` / `BoardScreen`: the design handoff's direction 1a (`design_handoff_factory_board/`). Six rows of chrome (`chrome.py`) around a ContentSwitcher of views; the read model (`runs.board.board`) loads in a worker thread and arrives as a message; every action goes through `runs` in a worker thread (`act`) and speaks in the feedback line. Keys: o n t l, p menu, P pause, S stop, b batch, i interview, W restart (stuck only), ? help, esc. |
| `tui.py` | `FactoryBoard(BoardApp)` + `run_board_tui`: the operator's other verbs on ctrl+p (doctor, evals, simulate, metrics, tiers, reconcile, retry/replay a run, new project, the direct one-story runs), never in the footer. |
| `overview.py`, `needs.py`, `stories.py`, `batch.py`, `activity.py` (+ All runs), `modals.py` | One module per view: Overview (top of Needs you, the ONE next start, Working now, Since you left), Needs you (grouped inbox + a detail per kind, text-box mode), Stories (grouped list, detail, `v` board view), the batch proposal, Activity / All runs, and the overlays (project menu, amend in two steps, stop, help). |
| `texts.py` | Pure markup for everything the board says (`$accent`, `$warning`… theme roles, never hex); unit-testable without a terminal. |
| `chrome.py`, `theme.py` | Header, lifecycle, tabs with badges, feedback line (✓●! clear after 4.5 s, ✗ stays), key bar; the design's tokens as a Textual `Theme` + the shared TCSS. |
| `answer.py` | `AnswerPicker`: a question answered like Claude Code's AskUserQuestion; used by Needs you and by `interview_screen.QuestionScreen`. |
| `interview_screen.py` | Modals: `QuestionScreen` (the pick list, for the palette's direct story run), `ReviewScreen` (read a document), `PromptScreen` (one line). |
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
