# Factory workflow handoff for the next agent

## User intent and decisions

The operator approved the design in `docs/plans/2026-10-03-factory-workflow-design.md`
and implementation here. Preserve the Anthropic AI-native SDLC: artifact chain, gate
authorization, approved write scope, independent verification/review, replay and human
release authority. Product/story refinement may proceed independently of builds.
The scheduler proposes a compatible batch; the operator chooses exactly which stories
launch. Routine work may run automatically within saved policy. Meaningful uncertainty,
release and material exceptions still require a person. The board is the control surface.

The operator asked us to stop feature work, merge the current state to `main`, push it,
and leave a clear task list for a new agent. A tmux/asciinema walkthrough was requested
after that, but the operator then said stop; no recording was made.

## Current repository state

- Feature branch: `feature/factory-workflow` in `.claude/worktrees/workflow`.
- Feature implementation checkpoint: `c54d046 feat: add durable refinement and isolated batch workflow`.
- This branch began at `b23c0d9`, merged the freshly fetched `origin/main` (`8226cc4`),
  and contains merge resolutions and board test edits not committed yet.
- The automatic merge has a resolved but not yet staged conflict in
  `src/factory/verification/python.py`. The worktree reports `UU`; its conflict markers
  were resolved in the working file by keeping the upstream short traceback, the
  configured timeout, and both branches' failure excerpt code. Stage the file to mark
  the conflict resolved.
- More staged upstream merge edits, unstaged handoff fixes, and a new board test
  conftest are present. Review `git status` before staging. Do not drop staged changes.
- The original `main` worktree still has the basic overview edits from before this
  isolated worktree was made. Those same edits are already included in commit `c54d046`.
  Preserve them while switching/integrating; stashing those duplicate dirty changes,
  merging, and then checking the stash is one safe option.
- `origin/main` was fetched as `8226cc4`. It adds design-wide scope enforcement and
  failed-test retries. Those upstream changes are in the feature merge resolution.
- A prior offline test run was terminated after failures appeared. It had inadvertently
  started two detached workers in disposable test homes; both worker processes and the
  test parent were terminated. A UI test conftest now stubs worker startup. Confirm no
  temporary workers remain before running tests. No live model call was intended.
- Static AST parsing and `git diff --check` passed before the latest merge-resolution
  edits. They have not been repeated after all edits. No test suite passed.

## Immediate integration steps

1. In the feature worktree inspect the resolved `python.py`, all unstaged test edits,
   and upstream integration. Stage the resolved file, then run `git status` and
   `git diff --cached --check`.
2. Continue with small offline focused tests before retrying `make check`. Ensure the
   board test guard stubs both `tui.start_worker` and `workflow_screen.start_worker`.
   The earlier run started worker processes from existing UI tests, so verify that no
   test can reach adapters or spawn a real worker. Tests should use temporary DBs and
   disposable repositories only.
3. Fix all failures, and check architectural layering and migration behavior. There
   are no independent code review results; prior subagents stopped because of an
   account usage limit.
4. Update this file with actual checks and risks. The current README says the board
   flow is implemented more completely than it has been verified; qualify any steps
   that remain unfinished.
5. Commit the merge resolution and test fixes on `feature/factory-workflow`.
6. Preserve the dirty overview files in the original checkout, merge the feature into
   `main`, inspect the resulting diff, then push `main` to origin. Do not force-push.
7. Only after the merge and push, check `asciinema` availability. If installed and a
   useful, safe walkthrough can be created with synthetic data (no live models or real
   product mutations), record it for the operator. If not available, skip it.

## Important code review targets

- **Feature still needs end-to-end validation.** The new durable job, refinement,
  decision, budget, plan, proposal, and resource-lock tables are in
  `src/factory/state/workflow.py`. Confirm all records migrate idempotently from an
  existing DB, answer/start transactions are single-use, unknown usage holds its
  reservation, and lost workers never repeat an uncertain write.
- **Worker / board:** `runs/worker.py` currently launches a detached process. Confirm
  worker lease ownership and shutdown/restart behavior, UI refresh while a worker acts,
  no duplicate submissions, useful recovery for stale jobs, and reachability at narrow
  terminal widths. Ensure old board approval paths do not silently bypass queueing or
  report success after a failed resume.
- **Refinement:** `runs/refinement.py` saves interview answers and story plans, but
  review whether technical questioning is genuinely resumable and records the saved
  recommendation/delegation correctly. Make sure rejections/revisions do not loop on
  paid calls or strand sessions. Verify backlog approval preserves refined stories.
- **Prepared pipeline:** plans now carry spec and architecture used as initial prepared
  output. Ensure story spec/design decisions are immutable and logged/evidenced under
  the existing playbook; gate-1/gate-2 and the boundary agent must still execute their
  normal deterministic and independent checks. An accepted batch must not silently
  grant new authority when a model revises scope.
- **Concurrency:** check `runs/service.py` legacy exclusion and every resume path. Ensure
  isolated batch runs do not share product checkouts and locks cover code publication,
  integration, review, and release. Validate duplicate launch races across two DB
  connections/processes and leases on SQLite.
- **Integration/release:** `runs/batches.py` merges story heads into a candidate
  worktree and verifies it, then asks for approval before ff-only integration. Check
  crash recovery between member approvals, merged candidate verification, DB decision
  recording, and Git fast-forward. Stale approvals, merge conflicts, dirty candidate
  worktrees, failed/missing test scripts and pre-existing test opt-out must never be
  described as success. Deployment is not implemented.
- **Scope incident:** upstream now allows files in the accepted architecture design,
  including design scope during remediation. Keep the `.prettierignore` regression case
  and do not add global filename exemptions.
- **Settings merge:** retain `FACTORY_SETTINGS` compatibility while supporting
  `FACTORY_CONFIG`, and verify the merged upstream settings/test behavior.
- **Board:** the original overview screen coexists with the new workflow screen. Decide
  whether to remove dead/duplicated overview code only after checking old board callers.
  Make sure UI actions are nonblocking, preserve draft answers, and show failures as
  failures. Existing test behavior was changed but is not yet green.
- **Checks and docs:** run AST/syntax, formatting, focused tests, then `make check` once
  offline isolation is proved. Review the new CLI help and README examples. Preserve
  Claude CLI selection through `FACTORY_RUNNER=claude` and the `[runner]` config.

## Recording constraint

Do not record a walkthrough until the requested feature branch has been validated,
committed, merged into `main`, and pushed. Use only fake/synthetic data in the recording.
