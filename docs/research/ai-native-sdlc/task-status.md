# Improvement tasks — current status

Status of each task in [improvement-tasks.md](improvement-tasks.md), checked against the
code at commit `82771ca` (2026-10-02). Each status rests on the commits and tests named
here, run with `make check` at that commit: 717 tests, 12/12 scenarios, 56/56 agent
configuration checks. "Live" means one paid model call was made to confirm the result.
The original [assessment](assessment.md) reviewed `de5fe87` and stays as it was written.

| Task | Status | What changed, and where it is proven |
|---|---|---|
| T00 Reconcile | **Done** | This file. |
| T01 Release checkpoint | **Done** | A passing tester no longer completes a run. The run parks at Checkpoint 3, and only the operator's approval on record releases it (`8cc3c21`, `tests/integration/test_release_checkpoint.py`). Missing tests, a missing ADR, missing notes, a schema error or a failed save is a named gap, never READY (`54b6b41`, `tests/pipeline/test_release_evidence.py`). The package says `next_authorization: none` once released and agrees with the DB (`1790233`). Release means the operator approved the committed candidate; nothing deploys, and no text claims a deployment. |
| T02 Evidence bound to the candidate | **Mostly done** | A `skip` or `warn` test status is no longer read as a pass. That was a real overstatement bug, introduced in `54b6b41` and fixed in `05a5cc8`. Each toolchain is judged by its newest result. Checkpoint 3 pins the candidate commit, the package names it, and the diff is measured up to it (`1790233`). An unreadable schema fails closed. **Still open:** structured per-check results (command, exit status, test count), and mapping each acceptance criterion to the test that proves it. Both need T04's result format. |
| T03 Isolation | **Partly done; needs your decision** | Default verification now executes nothing the coder wrote. Test collection, which imports test modules, is opt-in, and a static import check replaces it. Proof: a planted import-time marker is never written (`a703f83`). **Open:** an OS-level sandbox for opt-in test runs and for the agents' own read tools. The mechanism (container, VM, macOS sandbox) is your decision. |
| T04 Meaningful verification | **Partly done; needs your decision** | A missing `go` / `gofmt` / `node` / `tsc` now fails its files instead of skipping them (`a703f83`). **Open:** a per-product verification profile (install / build / test commands, including for config-only changes). Which stack goes first is your decision. |
| T05 Existing-code context | **Partly done** | Reviews, release notes and remediation now diff from the run's own base commit, so earlier stories no longer fill the review budget. Go exported declarations appear in the repo inventory (`1790233`). **Open:** retrieving full relevant files, and hash-guarded edits. This is a larger design change, best done when a brownfield story needs it. |
| T06 Scope and regression tests | **Needs your decision** | Not changed. Turning the boss's warnings (task with no scope, no completion evidence) into blocks, refusing out-of-scope writes before they happen, and protecting existing tests are all autonomy-policy choices with real false-positive risk. |
| T07 Approvals bound to artifacts | **Partly done** | A release approval is bound to the reviewed code: changed code is refused (`1790233`). **Open:** fingerprinting the approved spec and plan, and a per-project policy for which decisions may proceed automatically (your decision). Live evidence so far: a passed boundary review answered the architect's reflexive "security" flag correctly on the real SupportFlow design. |
| T08 Honest usage and budgets | **Partly done; needs your decision** | Root cause fixed. opencode reports usage on `step_finish` events, which the parser ignored, so every row stored NULL. Tokens, model and provider cost are now recorded; live check: 575 in / 51 out (`56de71b`). Metrics state that a $0 subscription login cannot bind a $ budget. **Open:** a token allowance and a pre-call limit. The amount is your decision. |
| T09 Live behavioural evals | **Needs your decision** | Not started. It needs an approved spend allowance (T08) and isolation (T03). |
| T10 Story isolation and PR handoff | **Partly done** | Only one live run per project is allowed at a time, and the check runs before any token is spent (`1790233`). **Open:** a branch or worktree per story, and a PR. What "release" should mean (local candidate, merged PR, deployment) is your decision. |
| T11 Docs and checks agree | **Done for now** | The CI file no longer claims there is no remote, or that offline replays detect model drift. Cost, verification, diff-base, candidate and lock statements were updated (`82771ca`). Lint stays advisory: `ruff` is configured but not installed, and `make lint` says so. |
| T12 Failures into improvements | **Partly done** | Metrics count live work only, so replays no longer inflate throughput, gate rates or tokens (`56de71b`). **Open:** a deduplicated diagnosis/proposal loop. Add it when there is enough live history to feed it. |

## Decisions that would unblock the rest

1. **Isolation (T03).** Which sandbox for running generated tests: a container, a VM, or a macOS sandbox?
2. **First verification profile (T04).** Go backend first (SupportFlow), or something else?
3. **Autonomy policy (T06/T07).** Which warnings become blocks? May a passed boundary review answer the architect's sensitivity flag without parking you?
4. **Budget (T08/T09).** A token allowance per task/run, and a live-eval allowance.
5. **What "release" means (T10).** An approved local candidate (today), a merged PR, or a named deployment?
