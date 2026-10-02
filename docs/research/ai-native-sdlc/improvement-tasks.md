# Factory improvement tasks — proposal for independent review

**Created:** 2026-10-02  
**Purpose:** ask another agent to challenge, update, and prioritize these tasks before implementation.  
**Authorization:** review only. This document does not authorize code changes, paid model calls, deployment, commits, or pushes.

## Context for the reviewing agent

Factory is a Python/LangGraph application that coordinates specification, architecture, coding, verification, review, and release decisions. Agents run through an OpenCode adapter. Python code controls gates and file materialization; SQLite and product Git repositories store evidence. The intended operator is one developer, so improvements should reduce review effort without creating unnecessary infrastructure.

The original assessment reviewed commit `de5fe873ec4baece49035f1b6c11139d695aa7aa`. It passed 619 tests, 10 simulations, and 44 offline eval checks. Targeted probes identified gaps outside those existing checks. These results apply to that revision only.

Since then, two commits have landed:

- `8cc3c21`: adds the release agent and Checkpoint 3.
- `54b6b41`: changes trust-package judgments, schema checks, and handling of missing evidence.

This task file was drafted with **`54b6b41` as the latest observed commit**. There were also uncommitted edits to authorization, contracts, gates, and a new boundary test. A limited source inspection confirmed newer release/evidence work exists; its behavior was not independently retested while preparing this file. **Some original findings may already be resolved. Verify before proposing duplicate work.**

Background:

- [Source learnings](README.md): notes from all 14 course lessons.
- [Detailed assessment](assessment.md): findings F1–F8 and code references.
- [Original verification evidence](evidence.md): reproductions and confidence boundaries.
- [Playbook article](https://claude.com/blog/the-ai-native-sdlc-playbook).
- [Academy course](https://academy.claude.com/courses/ai-native-sdlc-playbook).

These are proposed engineering outcomes, not a demand to copy every enterprise practice in the playbook. Preserve the deterministic boss and existing regression suite unless concrete evidence justifies replacing them.

## Priority and status

- **P0:** establish a trustworthy verification/release boundary before increasing autonomy.
- **P1:** improve correctness and control before larger existing-project or live-model workloads.
- **P2:** improve delivery, maintenance, and operator experience after the foundation works.
- **Verify existing work:** inspect and test the implementation already added; create fixes only for remaining gaps.
- **Proposed:** based on the original assessment; confirm it is still needed on the current revision.

An unchecked box means the task has not been independently accepted as complete. It does not necessarily mean no implementation exists.

| ID | Priority | Task | Starting status |
|---|---|---|---|
| T00 | First | Reconcile this backlog with the current revision | Required review |
| T01 | P0 | Verify the release checkpoint and final evidence | Verify existing work |
| T02 | P0 | Bind verification evidence to the actual candidate | Verify existing work and remaining gaps |
| T03 | P0 | Isolate product execution | Proposed |
| T04 | P0 | Require meaningful product verification | Proposed |
| T05 | P1 | Give agents enough context for existing-code edits | Proposed |
| T06 | P1 | Enforce write scope and protect regression tests | Proposed; boundary work in progress |
| T07 | P1 | Bind approvals to the approved artifacts | Proposed; inspect new release flow |
| T08 | P1 | Represent unknown usage and enforce honest budgets | Proposed |
| T09 | P1 | Add bounded evaluations of current model behavior | Proposed |
| T10 | P2 | Isolate stories and introduce a reviewed PR handoff | Proposed |
| T11 | P2 | Make documentation and required checks agree | Proposed; some docs updated recently |
| T12 | P2 | Turn measured failures into reviewed improvement work | Proposed |

## Tasks

### T00 — Reconcile the backlog with the current revision

**Why:** acting on an outdated assessment wastes effort and can undo useful work from another agent.

**To do:**

- [ ] Record the current commit and relevant uncommitted changes; avoid mixing revisions in the review.
- [ ] Compare each task with the current implementation and its tests.
- [ ] Mark each item `already addressed`, `partially addressed`, `still needed`, `disagree`, or `defer`, with evidence.
- [ ] Re-run relevant original probes in a disposable checkout where needed; adapt expectations to the release checkpoint.
- [ ] Keep unrelated in-progress edits intact.

**Done when:** every task has a current status, supporting code/test references, and a reason. Previously passing test totals are not presented as verification of newer code.

**Start with:** the three background documents, commits `8cc3c21` and `54b6b41`, and `git status`/`git diff`.

**Depends on:** nothing. Do this before implementation planning.

### T01 — Verify the release checkpoint and final evidence

**Why:** a person needs to know whether a run merely finished its internal steps or actually met the requirements for approval. The original saved package could disagree with the final database state; new commits explicitly address this area.

**To do:**

- [ ] Verify that a passing tester alone cannot authorize release or mark a run completed.
- [ ] Verify that missing tests, decision records, required release artifacts, and malformed evidence produce explicit gaps.
- [ ] Check package creation before approval and after approval, rejection, retry, and replay/resume.
- [ ] Check what happens when saving or committing required evidence fails.
- [ ] Make CLI, board, database, and saved package agree on the run's state and next action.
- [ ] Distinguish approval to release from actual deployment; avoid calling an undeployed product deployed.

**Done when:** integration tests show only the intended approval route can complete the run; required missing evidence cannot appear as clean readiness; saved evidence agrees with committed state; failures have an actionable explanation. Any permitted human exception is explicit, recorded, and visibly different from meeting the normal evidence requirements.

**Inspect:** `src/factory/pipeline/nodes/gates.py`, `nodes/release.py`, `pipeline/evidence_writers.py`, `domain/gates.py`, `runs/service.py`; `tests/integration/test_release_checkpoint.py` and `tests/pipeline/test_release_evidence.py`.

**Depends on:** T00. Coordinate with T02; do not build another release agent just to satisfy this task.

### T02 — Bind evidence to the actual candidate being reviewed

**Why:** passing tests on an earlier revision do not prove the final code works. Old failures should remain visible without incorrectly invalidating a later successful repair. A later project change must not silently rewrite historical evidence.

**To do:**

- [ ] Identify the candidate with an immutable commit/content identifier and record the relevant configuration fingerprint.
- [ ] Store structured verification results: command, environment, revision, exit status, test count, and required check identity.
- [ ] Decide readiness from the final candidate's required checks; retain superseded attempts as history.
- [ ] Reject `skip`, `warn`, unknown status, and zero executed tests as proof of passing a required test check.
- [ ] Validate evidence types and nested values, including behavior when its schema is missing or unreadable.
- [ ] Map acceptance criteria to real executed tests or an explicitly approved alternative proof.
- [ ] Ensure later work cannot change the meaning of an old package or reuse stale verification for a new candidate.

**Done when:** fail→repair→pass, pass→skip, missing-tool, zero-test, mixed-stack, changed-code-after-test, and later-story scenarios all produce truthful results. In particular, inspect the newer `_test_execution` logic: recognition of a marker without `:fail` must not automatically prove it passed.

**Inspect:** `src/factory/evidence/trust_package.py`, `evidence/schemas/trust-package.schema.json`, `verification/base.py`, `state/db.py`, `workspace/git.py`; `tests/evidence/test_trust_package.py` and `test_trust_truthfulness.py`.

**Depends on:** T00. Coordinate the result format with T04 and approval binding with T07.

### T03 — Isolate execution of generated product code

**Why:** Python test collection can run import-time code even when test bodies are disabled. A separate working directory does not prevent a process from reading host credentials or writing elsewhere.

**To do:**

- [ ] Choose the smallest practical isolation mechanism for the actual host and first supported product stack.
- [ ] Run dependency installation, collection, builds, tests, and relevant agent tools under a defined filesystem/network/credential policy.
- [ ] Pass a minimal environment and impose time/resource limits.
- [ ] Inspect effective runtime tool permissions, including the adapter's permission-bypass flag and externally inherited configuration.
- [ ] Make unavailable isolation an explicit stop or approved mode; never silently claim isolation while falling back to host execution.

**Done when:** harmless fixtures cannot read a planted host-secret marker, write outside allowed paths, or reach a forbidden network endpoint. Hung commands stop predictably. Normal supported work succeeds inside the boundary.

**Inspect:** `src/factory/adapters/opencode.py`, `verification/base.py`, `verification/python.py`, `workspace/sandbox.py`. The `.claude/` hooks govern factory development and are not sufficient runtime enforcement.

**Depends on:** T00. This is a prerequisite for enabling routine execution of generated tests in T04 and live evals in T09.

### T04 — Define and enforce meaningful product verification

**Why:** compilation, test discovery, and model review answer different questions. None alone proves the product's required behavior. Extension-based dispatch can miss configuration changes or unsupported stacks.

**To do:**

- [ ] Choose one first-class product stack and define operator-approved install/build/check/test commands.
- [ ] Use the product's dependencies and runtime, with reproducible setup.
- [ ] Run the relevant checks for configuration and dependency changes as well as source changes.
- [ ] Report unsupported stacks, missing tools, and unexecuted checks as incomplete verification.
- [ ] Execute the full required verification set for the final candidate; include browser checks when UI behavior is part of acceptance.

**Done when:** a real regression fails a required test; a healthy product passes; missing tools and zero tests cannot satisfy the required suite; a config-only change receives appropriate verification. An unsupported stack gets an honest result.

**Inspect:** `src/factory/verification/`, `workspace/templates.py`, `domain/project_spec.py`, `pipeline/nodes/coder.py`; `tests/verification/test_verify.py`.

**Depends on:** T03 for execution isolation; T02 for the result contract. Expand to more stacks only after the first profile works end to end.

### T05 — Give agents enough context for safe existing-code edits

**Why:** replacing a file after seeing only its public signatures can erase discounts, permissions, error handling, or other behavior that is invisible in the interface map.

**To do:**

- [ ] Keep the small inventory for discovery, then supply relevant full source files, tests, and project rules.
- [ ] Add discovery coverage for supported languages, including Go if it remains a supported stack.
- [ ] Use patches or replacements guarded by the expected previous file hash.
- [ ] Stop or split work when necessary context cannot fit; make omitted context visible.
- [ ] Give the reviewer the current story's complete relevant diff rather than unrelated historical changes consuming the review budget.

**Done when:** a fixture changes one behavior while preserving neighboring behavior, stale writes are rejected, and missing context is surfaced. The review input contains the current candidate's relevant changes.

**Inspect:** `src/factory/workspace/repo_map.py`, `materialize.py`, `git.py`, `pipeline/prompts/context_pack.py`, `prompts/tester.py`, `agents/coder-agent.md`; tests under `tests/workspace/` and `tests/pipeline/prompts/`.

**Depends on:** T00. Test the resulting behavior through T04 and later T09.

### T06 — Enforce approved scope and protect regression tests

**Why:** recording an unauthorized edit after writing it is weaker than preventing it. A coder must not be able to make a failing check pass simply by removing or weakening the check.

**To do:**

- [ ] Require useful task scope and completion evidence before coding, with explicit handling of legacy tasks.
- [ ] Validate all proposed writes against approved scope before materialization.
- [ ] Distinguish adding coverage from changing an existing protected regression test.
- [ ] Require explicit review for changes to protected expectations and sensitive manifests.
- [ ] Inspect the in-progress boundary changes before proposing overlapping work.

**Done when:** an out-of-scope file is never written; a protected failing test cannot be deleted or relaxed to manufacture success; allowed test additions still work; approved dependency changes remain possible through a clear route.

**Inspect:** `src/factory/domain/authorization.py`, `verification/scope.py`, `workspace/materialize.py`, `pipeline/nodes/coder.py`; `tests/domain/test_authorization.py`, materialization safety tests, and any newer boundary tests.

**Depends on:** T00; coordinate approved scope versions with T07.

### T07 — Bind approvals to the artifacts the person actually approved

**Why:** approving one plan or code revision should not authorize a materially different plan or revision. Low-risk automatic progress can remain useful when the operator explicitly chose it.

**To do:**

- [ ] Define which decisions are automatic and which require a person for each project.
- [ ] Record approval identity, time, decision, and the relevant spec/plan/rules/candidate fingerprints.
- [ ] Invalidate affected approvals when those artifacts materially change.
- [ ] Recheck authorization at resume and immediately before the protected action.

**Done when:** an unchanged approved candidate can resume; a changed candidate cannot reuse stale approval; rejection and replay cannot bypass the boundary; low-risk automation follows a visible policy.

**Inspect:** `src/factory/domain/authorization.py`, `pipeline/boss.py`, `state/db.py`, `runs/service.py`, release nodes and checkpoint tests.

**Depends on:** T01/T02 for release evidence; coordinate with T06 for scope enforcement.

### T08 — Make usage accounting and spending limits honest

**Why:** unknown spend is not free spend. A limit cannot provide its claimed guarantee when missing usage is counted as zero or a run total is presented as a per-task total.

**To do:**

- [ ] Represent complete, partial, and unavailable cost/token information separately.
- [ ] Test parsing against representative provider usage events without requiring paid calls in unit tests.
- [ ] Track task and run budgets separately, including retries and repair calls.
- [ ] Define a pre-call allowance and an explicit policy when usage is unavailable.
- [ ] Label estimated or soft currency limits honestly; retain attempt bounds.

**Done when:** missing usage never appears as confirmed zero; exhausted allowance prevents the next call; retries are attributed correctly; reports show coverage and uncertainty. Any live calibration has a separately approved spending allowance.

**Inspect:** `src/factory/adapters/opencode.py`, `state/db.py`, `state/reports.py`, `pipeline/agent_calls.py`, `pipeline/nodes/coder.py`, `evidence/metrics.py`.

**Depends on:** T00. Required before increasing paid eval volume in T09.

### T09 — Evaluate current model behavior as well as frozen replays

**Why:** replay tells us how the factory handles a saved answer. It does not tell us whether a new prompt or model now produces a worse answer.

**To do:**

- [ ] Preserve cheap config checks and replay as the offline regression layer.
- [ ] Add a separate, explicitly budgeted behavioral suite using disposable product fixtures and current model calls.
- [ ] Begin with a small useful corpus covering existing-code edits, ambiguity, boundaries, and regressions; expand from observed incidents.
- [ ] Compare baseline and candidate configurations, account for variability, and save provenance/results.
- [ ] Decide which regressions require review before changing runtime configuration.

**Done when:** a deliberately weakened prompt produces a detectable behavioral regression while unchanged frozen replays still pass; budget and isolation controls hold; a reviewer can reproduce the configuration and inspect failure evidence.

**Inspect:** `src/factory/selftest/evals/`, `evals/cases/`, `pipeline/agent_calls.py`, `agents/`, `.github/workflows/check.yml`.

**Depends on:** T03, T04, and T08. Designing the fixtures can begin earlier; paid execution needs an approved allowance.

### T10 — Isolate live stories and introduce a reviewed PR handoff

**Why:** stories sharing a working tree can commit each other's edits. A reviewed candidate should enter the accepted branch through a controlled, reproducible process.

**To do:**

- [ ] Use a story branch/worktree and initially prevent conflicting concurrent writes per product.
- [ ] Stage only the intended story's changes and evidence.
- [ ] Prepare a PR with the accepted plan, candidate diff, test evidence, and release decision state.
- [ ] Reproduce required checks in product CI and verify actual remote branch/approval policies.
- [ ] Separate authoring from merge/release approval capabilities.

**Done when:** simultaneous stories cannot absorb each other's edits; rejected work leaves the accepted branch unchanged; a PR with missing required checks or approvals cannot merge. A workflow YAML alone is not accepted as proof of branch protection.

**Inspect:** `src/factory/runs/service.py`, `workspace/git.py`, `workspace/sandbox.py`, project setup, and product CI configuration.

**Depends on:** T01–T04 and T07 for the reviewed handoff. Workspace isolation itself can be designed earlier.

### T11 — Make documentation and required checks agree

**Why:** operators and agents make incorrect decisions when a README or success message promises a guarantee that the commands do not enforce.

**To do:**

- [ ] Reconcile status terminology and verification commands across README, `CLAUDE.md`, contracts, CLI, and saved artifacts.
- [ ] Distinguish implemented, advisory, planned, and intentionally unsupported behavior.
- [ ] Decide whether lint is required; make dependency installation, local commands, and CI match that decision.
- [ ] Remove remaining claims that frozen replay measures live model drift or that an existing remote is absent.
- [ ] Link the review backlog and record superseded findings without rewriting the historical assessment as if it reviewed newer code.

**Done when:** a new developer can follow documented checks, understand exactly what a passing result proves, and identify current gaps. Claims are supported by commands or current evidence.

**Inspect:** `README.md`, `CLAUDE.md`, `Makefile`, `.github/workflows/check.yml`, `docs/contract/`, and generated status text.

**Depends on:** T00. Update incrementally as other tasks land.

### T12 — Turn measured failures into reviewed improvement work

**Why:** metrics are useful when they lead to fixes and prevent repeated failures. Totals mixing live work with replay can give a misleading picture of delivery quality.

**To do:**

- [ ] Separate live runs from replay/simulation in outcome metrics and define their denominators.
- [ ] Measure a small useful set: accepted changes, review effort, evidence completeness, usage coverage, and later escaped defects.
- [ ] Add one deterministic trigger that creates a deduplicated diagnosis/proposal for human triage.
- [ ] Link accepted fixes to product regression tests and appropriate factory/behavioral eval cases.
- [ ] Record dismissals and reasons so the same noise does not repeatedly return.

**Done when:** a known failure produces one traceable proposal; a person can accept or dismiss it; an accepted fix leaves a lasting regression case. The initial loop proposes work and does not autonomously deploy changes.

**Inspect:** `src/factory/evidence/metrics.py`, `state/reports.py`, `selftest/evals/capture.py`, operator queue interfaces, and CI artifacts.

**Depends on:** reliable outcomes from T01/T02; product verification from T04. Add current-model regression cases when T09 exists.

## Suggested first slice

1. Complete T00 and verify the newer release/evidence work in T01/T02.
2. Implement only the unresolved release/evidence defects identified by that review.
3. Establish isolated execution and one complete verification profile through T03/T04.
4. Then select between existing-project reliability (T05–T07) and live-workload controls (T08/T09) based on actual use.

T10–T12 should follow the foundation, although small documentation corrections can happen throughout. No calendar estimates are assigned before scope and the current implementation are reconciled.

## Decisions needed before implementation

The reviewer can assess the tasks without these answers. Implementation planning should settle them:

- Which product stack should get the first complete verification profile?
- Which host/isolation mechanisms are acceptable, and what dependencies/network access do builds need?
- Which decisions may proceed automatically, and when may a person explicitly accept an evidence gap?
- What is the allowed live-eval spend and the policy when usage is unknown?
- Does “release” initially mean an approved local candidate, a merged PR, or deployment to a named environment?

## Defer unless actual usage justifies them

- More simultaneous coder agents before isolated workspaces and sufficient review capacity.
- Autonomous production deployment or rollback before a release target, gates, and practiced recovery exist.
- Statistical control bands before enough reliable operational data exists.
- Replacing the runtime or framework without evidence that it solves a concrete boundary or capability problem.

## Prompt to give the other agent

> Review this task proposal against the current repository. Do not implement or modify application code. First identify the commit and uncommitted changes you examined. The underlying assessment reviewed `de5fe87`; release and trust-package work landed afterward in `8cc3c21` and `54b6b41`, and further work may exist. For each task, say whether it is already addressed, partially addressed, still needed, something you disagree with, or something to defer. Cite code and test evidence, identify assumptions, and explain your reasoning in simple language. Challenge unnecessary complexity and duplicated work. Distinguish static inspection from executed tests. Recommend the smallest next implementation slice, its acceptance tests, and any decisions the owner must make. Do not call paid models or deploy anything for this review.

Suggested response table:

| Task | Current status | Evidence | Agree/change/reject | Revised priority and reason |
|---|---|---|---|---|
| T00–T12 | | | | |

Finish with the three most valuable next actions and any missing task that would materially change the plan.
