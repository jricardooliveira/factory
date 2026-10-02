# Review Queue & Checkpoints

The factory runs unattended but parks at three checkpoints and on escalated failures. This doc defines the **async + notify** contract: how work is parked, how the operator is told, and how they act on it. Checkpoints 1, 2 and 3 (with `gate-release`), failure parking, the queue, the board, approve/reject/retry and the opt-in desktop ping are `✅ built` (EFFECTIVENESS §8). Parking and resume are orchestrated by `src/factory/runs/` (`service.py`), shared by the CLI and the board.

---

## Principle

The factory **never blocks on the operator in real time.** It produces a reviewable, parked artifact and pings the operator, who acts on their own schedule. Effectiveness = **trust per interruption**: interrupt rarely, and when you do, hand over everything needed to decide quickly.

---

## What parks a task

1. **Checkpoint reached** — one of the three mandatory sign-offs:
   - **Checkpoint 1 — spec sign-off** (after `gate-1`): "is this the right work?" Package: story, acceptance criteria, sliced tasks, non-goals.
   - **Checkpoint 2 — architecture sign-off** (after `gate-2`): "is this the right design?" Package: ADR/notes, affected modules, risks, and any `breaking_changes` / `external_dependencies` / `sensitivity` flags.
   - **Checkpoint 3 — release sign-off** (after `gate-release`): "ship it?" Package: the full **trust package** (tests+AC coverage, real git diff, ADR, security/boundary verdict).
2. **Failure escalation** — auto-remediation exhausted its budget (2 attempts / ~$1) and stopped. Package: the failing gate, the findings, and what was attempted.

---

## Queue states

```
running → parked(checkpoint_1) → approved → running
                               → rejected(feedback) → re-attempt
        → parked(checkpoint_2) → …
        → parked(failure)      → rejected/aborted | re-attempt
        → parked(checkpoint_3) → approved → released
```

A parked task carries: `run_id`, `project_id`, `stage`, `reason` (checkpoint vs failure), the package payload, and accumulated cost so far. Backed by `pipeline_runs.status` (`waiting_human`) + `gate_results` (`needs_human`, `human_questions`, `human_response`).

---

## Notification

When a task parks, `runs/service.py` fires a **desktop notification** (macOS) summarizing: project, story, why it parked, and the `factory queue` command to review. The mechanism is `osascript` in `src/factory/adapters/notify.py`, **opt-in** via `FACTORY_NOTIFY=1` (it was observed launching Script Editor on some setups, and the queue/board already provide visibility). Best-effort: it never raises or blocks the pipeline. No external accounts required.

---

## Operator commands

- `factory board` — **interactive** Textual dashboard: live table of runs, select a parked one to read its questions, type feedback, and approve/reject in place (resume runs in a background worker so the UI stays responsive). `--once` for a static snapshot, `--plain` for the non-interactive live view.
- `factory queue` — list parked tasks with reason + cost + a one-line package summary.
- `factory review <run_id>` — full package for one task, including the assembled trust package (at Checkpoint 3 it is written to `docs/releases/`, next to the story's `RELEASE.md`). `--raw` adds the verbatim agent output.
- `factory approve <run_id> [note]` — sign off; the line resumes from where it parked (`runs.resume_run`, entry chosen by `pipeline.resume_entry_for`).
- `factory reject <run_id> <feedback>` — bounce it back. **Rejection is not a dead end:** the feedback becomes `prior_findings` on a new attempt that re-enters at the appropriate stage, not a terminal `rejected` row.
- `factory retry <run_id>` — re-drive a run whose process died after you answered (`runs.retry_run`).
- `factory dismiss <run_id>` — archive a run off the board. `factory reconcile [--older-than S]` — fail runs stuck `running` by a dead process (`runs.reconcile_stale` → `state.db.reconcile_stale_runs`).

---

## Resume semantics

- **Approve at Checkpoint 1/2:** continue to the next stage using the already-produced artifacts (no re-run of the approved stage).
- **Approve at Checkpoint 3:** the boss runs `release` only with your approval on record; it merges the story's pull request (a GitHub PR, or the `factory/<story>` branch locally) and the story is completed only once the merge landed — a merge that cannot land blocks the run with the reason; the final trust package is written. Approving a release `gate-release` judged NOT READY is allowed — the gaps were named — and is recorded as accepted risk in `PIPELINE.md`.
- **Reject at Checkpoint 3:** your feedback becomes the findings of a remediation coder pass (frontier tier), then the tester, then release notes and Checkpoint 3 again (`resume_entry_for` → `remediation`).
- **Reject anywhere:** increment `attempt_number`, attach the operator's feedback as `prior_findings`, and re-enter at spec (Checkpoint 1) or architecture (Checkpoint 2) or coder (failure), bounded by the same remediation budget. The routing is `resume_entry_for` in `src/factory/pipeline/graph.py`; the state handed to the re-entered stage is rebuilt from the LATEST agent output by `runs.build_resume_context`.

This makes the human a first-class node in the graph rather than a blocking dead-end — the missing half of the current `waiting_human` path.
