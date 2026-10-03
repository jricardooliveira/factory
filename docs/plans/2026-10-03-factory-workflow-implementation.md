# Factory workflow implementation plan

> Execute here using the subagent-driven-development skill. Design approved by the operator.

**Goal:** Independent durable refinement, conservative batch scheduling, isolated execution,
and a board that exposes decisions and progress while preserving the SDLC and release gates.

**Architecture:** Pure policy in domain; SQL in state; jobs/refinement/scheduling in runs;
isolated checked Git operations in workspace; Textual views call application services.

**Constraints:** Preserve prior work. No live model calls or writes to real products during
implementation. The session developer instruction prohibits adding/running tests unless
requested; use code review and static checks and explicitly report that testing was not run.
Do not weaken protected scope, verification, independent review, or release approval.

## Tasks

1. [ ] Persistence and policy: additive workflow tables; stable backlog identity; immutable
   context/plan revisions; decisions and drafts; atomic jobs/leases; deterministic dependency,
   uncertainty and semantic read/write conflict checks; batch snapshot validation.
   Files: domain/workflow.py, state/workflow.py, state/db.py, state/backlog.py.
2. [ ] Durable refinement and workers: bounded interview steps, technical/execution sections,
   async questions, claim/heartbeat/recovery, answer idempotency, independent preparation;
   save model I/O and strict output validation. Files: runs/refinement.py, runs/worker.py,
   runs/workflow.py and CLI worker commands.
3. [ ] Execution/integration: worktree identity for start/resume, legacy exclusion, exact
   approved plan/baseline checks, budget reservation, serialized combined candidate verification
   and explicit release decision. Files: workspace/worktrees.py, workspace/git.py,
   runs/batches.py, runs/service.py, pipeline integration boundaries.
4. [ ] Board: default project overview with project selector, Needs you, Stories, Activity;
   persistent question drafts; refine/propose/launch/pause/recovery controls; truthful outcomes;
   responsive views. Files: runs/dashboard.py, interfaces/board/workflow_screen.py, tui.py.
5. [ ] Scope incident and integration review: reconcile accepted design scope with tasks before
   coding; explain legitimate scope amendments; static review across changed call sites;
   document CLI/board flow, Claude runner, migration and any outstanding limitations.

## Review

Review each task against design and code quality. Perform syntax/format/diff inspections;
record any unexecuted checks honestly. Never count a proposed plan as verified compatibility
without concrete change areas and resolved dependencies. Unknown usage reserves budget.
