# evidence/

## Responsibility

Everything the factory can prove about a run without calling an LLM: the committed artifact chain
(INTENT -> SPEC -> PLAN, plus ADRs and release notes), the per-run `PIPELINE.md` record, the trust
package assembled for the release sign-off, per-run progress/timeline derived from stored rows, and
cross-run SDLC metrics. Documents are rendered from data the factory already has, written under the
product repo's `docs/`, and are meant to be committed by the caller. Nothing here calls a model.

## Modules

| Module | What it does |
|---|---|
| `artifacts.py` | Renders and writes `docs/work/<story>/` files: `render_intent`/`write_intent` (INTENT.md, the raw request + git author), `render_spec`/`write_spec` (SPEC.md), `render_plan`/`write_plan` (PLAN.md, task order via `domain.task_order.order_tasks`), `render_release_notes`/`write_release_notes` (RELEASE.md). `planned_scope` = union of task scopes; `work_dir_for`. |
| `adr.py` | Decision memory: `render_adr`, `write_adr` (`docs/architecture/adr/ADR-<story>-<slug>.md`, deterministic name), `load_project_memory` (PROJECT_RULES.md + last 5 ADRs as a prompt block, optional `exclude_story`), `adr_dir_for`. |
| `trust_package.py` | `assemble(db_path, run_id)` builds the sign-off package from stored logs/gates/git; `validate` (schema + evidence bars), `schema_errors` (shape only, fails closed if the schema is unreadable). |
| `schemas/trust-package.schema.json` | The JSON schema the package is validated against (package data). |
| `pipeline_record.py` | `render_pipeline_record` / `write_pipeline_record`: `docs/work/<story>/PIPELINE.md` (next authorized step, blockers, trail table, warnings). |
| `progress.py` | `run_pipeline_progress` -> `StageStatus` list over the 9 canonical stages, `run_timeline` -> `TimelineEvent` list, plain-text `plain_flow`/`stage_text`/`hms`. |
| `metrics.py` | `compute(db_path)` -> `FactoryMetrics` (completion, first-pass rate, gate pass rates, checkpoints, cost coverage, `not_measurable`); `render_markdown`. |

## How it works

- **Artifact chain**: the pipeline writes INTENT, SPEC, PLAN and RELEASE via `pipeline/evidence_writers.py`
  and the ADR via `pipeline/nodes/architect.py`. `_write` returns `None` when there is no `project_dir`
  (ad-hoc runs have nowhere durable to put evidence). Filenames are deterministic, so a replay overwrites.
  `planned_scope` is the same list the trust package checks the real git diff against.
- **Trust package**: assembled on demand (no extra storage). Tests count as executed only when a
  `gate-build` reason contains `pytest_run:pass|fail` or `go_test:pass|fail`, taking the newest per
  toolchain. The diff is measured with `git.git_changed_files(..., exclude=EVIDENCE_PATHS, end=candidate_commit)`;
  if that is impossible, `diff.source` is `"unavailable"` and `validate()` reports it. Unmet bars become
  named `blockers` (failed run, tests not executed/failed, unmeasured diff, scope violations, existing tests that
  lost lines (`git_line_stats`: a file that only gained tests is not named) or were deleted/renamed, missing ADR, unassessed acceptance criteria). `next_authorization` is `none` / `release` /
  `operator-review`. Acceptance criteria are cross-checked with `domain.traceability.trace_criteria`.
- **PIPELINE.md**: rendered only from the stored record (`run_timeline`, run row, authorizations), so it
  never claims more than the DB can back. `runs.service._finish` writes and commits it at every stop.
- **Progress** is evidence, not a view: the CLI review, TUI and `selftest.simulate` share it; rich-markup
  renderings live in `interfaces/render/review.py`.
- **Metrics** excludes replays from outcome rates. `not_measurable` is a first-class output: it names the
  missing cost/token data, the `$0` subscription-login case, response latency and post-production defects.

## Imports / imported by

- Imports: `factory.domain` (`contracts`, `agent_output`, `traceability`, `task_order`, `gates`),
  `factory.state` (`db`, `reports`), `factory.verification.scope`, `factory.workspace` (`git`, `layout.EVIDENCE_PATHS`,
  `sandbox.run_repository`). Never `runs`, `pipeline` or `interfaces`.
- Imported by: `pipeline` (`evidence_writers`, `nodes/architect`, `prompts/blocks`), `runs` (`service`, `queries`),
  `selftest/simulate`, `interfaces/render/review`, `interfaces/board/data`, `interfaces/cli/selftest`.

## Gotchas / where to add things

- Evidence paths the factory owns are listed in `workspace.layout.EVIDENCE_PATHS`; a new evidence directory
  must be added there or it shows up as agent work in scope checks and diffs.
- A new test toolchain needs its marker in `_TEST_RUN_MARKERS` and `_TEST_RUN_COMMANDS` here, otherwise
  the package understates its evidence (see `docs/ARCHITECTURE.md`, "A new toolchain").
- Changing trust package fields means editing `schemas/trust-package.schema.json` too; `validate` enforces
  `const` and `enum` entries, not only key presence.
- The package must never overstate evidence: do not derive `tests.passed` from gate booleans.
- New stage in the flow: add it to `_PIPELINE` in `progress.py`.
- `__init__.py` is empty; import submodules directly.
