# Factory workflow handoff

Status at the end of the 2026-10-03 session (supersedes the earlier version of this file,
which described the pre-merge state). Design: `2026-10-03-factory-workflow-design.md`.
UI critique and its loop: `2026-10-03-board-ui-critique.md`.

## Where things stand

- `feature/factory-workflow` is merged into `main` and pushed; the branch and its worktree
  are removed. `main` is green: `make check` = 1121 passed, 12/12 scenarios, 68/68 evals.
- The merge had been pushed red (25 failing tests). Fixed: `resume_run` committed inside
  `workflow_store.atomic()` (every approve/reject/resume/release raised "no such
  savepoint"); a retry never learnt which failed test it may change (macOS
  `/var` → `/private/var`); "unsure twice" is an assumption again (CLAUDE.md's rule); the
  project lock names a parked run; a regenerated backlog numbers after the visible rows.
- The board crashed on every workflow action (`Screen.call_from_thread`), on Enter in the
  workflow list (the run table's unscoped handler), and lost drafts. All fixed and pinned.
- The board UI was reworked in a loop (critique items 1–8 and 10 done): pick-list answers,
  Approve / Request changes, state-aware project actions, Retry for failed refinement and
  backlog jobs, one board with honest keys, Overview as a summary with the one next step,
  readable stories/events/batches, local times, tick-to-launch batches.
- `tests/simulated.py` drives real flows through the board with scripted agents and an
  in-process worker that never runs build jobs; the pipeline's agent boundary raises if
  reached. No live model call anywhere in the suite.

## Operator decisions taken this session

Fix forward on `main`; write tests for the new workflow code; keep both the direct "run
next story" path and refine → batch; one board (home + All runs); answers as a pick list +
Other; board tests simulate both sides (never the coding stage); Interview is state-aware
(Interview → Complete agreement → Amend brief…); Overview is a summary; the worker is
automatic; `feature/sandbox-and-pr-release` is reconciled later; no walkthrough recording.

## Open work, in order

1. **Verify the build half end to end, offline.** Launch → `dispatch_job` builds in
   worktrees → combined candidate verification → integration approval is not driven by any
   test. Extend the simulated factory with frozen pipeline outputs (the replay-fixture
   pattern, `tests/fixtures/agent_outputs/`) so a launched story builds without a model,
   then cover: two independent stories progress, a shared-area story waits, an unanswered
   question leaves unrelated work running (design acceptance 4–5).
2. **The original review targets still open:** worker lease ownership and restart, stale
   job recovery from the board, unknown usage holding its budget reservation, crash
   recovery between member approvals / combined verification / fast-forward, double
   launch and double answer across two processes, migration idempotency on an existing
   DB, `FACTORY_CONFIG` vs `FACTORY_SETTINGS`.
3. **Narrow terminals (< 100 columns):** list → detail as separate views (critique item 9).
   90 columns is usable now (pinned question heading, compact controls), 80 is not tested.
4. **Sandbox / PR release:** `feature/sandbox-and-pr-release` (generated tests in a
   container, release = merged PR — operator decisions of 2026-10-02) conflicts with the
   batch flow's ff-only local integration and host-run combined checks. Reconcile before
   anyone relies on batch integration.
5. Housekeeping: `stash@{0}` ("codex-pre-pull") holds the factory.toml settings refactor,
   which is already on `main` — drop it once the operator agrees.

## How to check a UI change

`make check`, then look at it: drive `FactoryBoard` with the Textual pilot on a copy of
`$FACTORY_HOME` (rewrite `projects.repo_path` in the copy; put stub `opencode`/`claude`
scripts first on `PATH` so no model is reachable), `app.save_screenshot()` to SVG, render
with headless Chrome, and read the PNG — at 140×45 and 90×34.
