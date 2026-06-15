# Review Queue & Checkpoints

The factory runs unattended but parks at three checkpoints and on escalated failures. This doc defines the **async + notify** contract: how work is parked, how the operator is told, and how they act on it. `⛔ to build` — this describes the target; the human-needs detection at gate-2 is the only piece that exists today.

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

When a task parks, fire a **desktop notification** (macOS) summarizing: project, story, why it parked, and the `factory queue` command to review. *(Mechanism — `osascript`/`terminal-notifier` — is an open implementation question.)* No external accounts required.

---

## Operator commands

- `factory board` — **interactive** Textual dashboard: live table of runs, select a parked one to read its questions, type feedback, and approve/reject in place (resume runs in a background worker so the UI stays responsive). `--once` for a static snapshot, `--plain` for the non-interactive live view.
- `factory queue` — list parked tasks with reason + cost + a one-line package summary.
- `factory review <run_id>` — full package for one task *(exists today; extend to render the trust package at checkpoint 3)*.
- `factory approve <run_id> [note]` — sign off; the line resumes from where it parked *(exists; resume flow is Phase 3.3)*.
- `factory reject <run_id> <feedback>` — bounce it back. **Rejection is not a dead end:** the feedback becomes `prior_findings` on a new attempt that re-enters at the appropriate stage, not a terminal `rejected` row.

---

## Resume semantics

- **Approve at Checkpoint 1/2:** continue to the next stage using the already-produced artifacts (no re-run of the approved stage).
- **Approve at Checkpoint 3:** mark released; assemble final release notes.
- **Reject anywhere:** increment `attempt_number`, attach the operator's feedback as `prior_findings`, and re-enter at spec (Checkpoint 1) or architecture (Checkpoint 2) or coder (failure), bounded by the same remediation budget.

This makes the human a first-class node in the graph rather than a blocking dead-end — the missing half of the current `waiting_human` path.
