# Factory workflow and board redesign

Status: approved for implementation. This document specifies behavior; it does not describe
features already implemented. The existing project-overview edits remain in the
working tree and have not yet been behaviorally verified.

## Agreed direction

- Preserve the Anthropic AI-native SDLC practices recorded in
  [the repository research](../research/ai-native-sdlc/README.md) and the
  [factory contract](../contract/EFFECTIVENESS.md).
- Refine future stories and products while other work progresses.
- Interview the operator about product, experience, technical choices, and
  execution authority. Recommend defaults and permit explicit delegation.
- Automate routine planning, implementation, verification, and bounded repairs
  within those decisions. Ask for material unresolved decisions, not every stage.
- Propose compatible story batches with reasons; the operator selects and launches
  a batch. This is the currently chosen scheduling policy. Launch is not release
  approval, and it does not authorize later unreviewed batch members.
- Preserve the release approval boundary. Distinguish a verified candidate,
  integration into the product, authorization to release, and deployment.
- Make the board useful to someone returning after an absence: what is happening,
  what needs them, what can start, and what prevented progress.

## What the code currently constrains

1. `runs/service.py::_refuse_if_project_busy` prevents two running stories sharing
   one product checkout. Removing it alone would permit cross-story commits and
   misleading review evidence. The database query guards `running`; parked runs
   are not covered by the same lock.
2. `state/backlog.py::next_approved` chooses the first approved row by position.
   Stories have no declared inter-story dependencies or assessed conflict data.
3. `runs/interview.py::run_story_interview` accumulates clarification answers in
   memory and returns an augmented request immediately before running the story.
   Agent calls are logged, but there is no independently resumable refinement item.
4. The board's interview callbacks push modal questions and keep worker threads
   waiting for replies. Interview workers share an exclusive group. `_busy` also
   suppresses board refresh during a run resume.
5. Backlog regeneration deletes and replaces all unstarted rows. Durable refinement
   needs stable story identities and explicit supersession instead.
6. Resume reconstructs paths from the project checkout. Isolated execution needs
   a persisted workspace identity used by every start/resume/retry/evidence path.
7. Git helpers assume `.git` is a directory in some paths. Worktree support must
   handle its `.git` file and shared Git metadata before concurrency is enabled.

## Approach

The recommendation is to evolve the existing Textual board and application
services. A UI-only refresh would leave blocking interviews and shared checkouts
underneath. A new web application would add a second interface before fixing those
workflow constraints. Durable workflow state and a better existing board address
both problems while reusing the boss, gates, agent adapters, CLI, and evidence.

## Board experience

Default to an Overview that works for one selected project or all projects. Use a
visible project selector, with keyboard search, instead of requiring repeated
cycling through project names. Keep navigation stable across views.

The following is illustrative, not live project status:

```text
FACTORY   Project: Checkers v      Workers: active      Pause new starts
Overview | Needs you (2) | Stories | Activity | Project settings

Building 2       Ready 3       Refining 2       Blocked 1

NEEDS YOU                         SELECTED ITEM
Technical decision: hosting       Why you are needed
Batch proposal: 2 stories         Recommendation + alternatives
                                  What this choice affects
IN PROGRESS                       Evidence / plan / changes
Board rendering  - building       [Answer / Review / Launch batch]
Move rules       - verifying

READY / WAITING
Sound controls   - compatible     Recent meaningful events
Persistence      - needs decision Last update; spend and configured limits
```

### Overview

Show decisions first, active work second, and the next available opportunity third.
An empty run list should instead show interview progress, draft/ready stories, and
the actual next action. Counts must have a clear project/global scope. Show current
stage, last progress time, and actual usage; never manufacture percentage complete,
time remaining, or a zero price for unknown usage.

### Needs you

One persistent inbox for product questions, technical choices, story clarifications,
batch launch proposals, escalated failures, and release decisions. Each entry says:

- What decision is needed, in plain language.
- Why the factory cannot decide under the agreed policy.
- Its recommendation, alternatives, affected work, and supporting evidence.
- What answering will do and which work can continue independently.

Actions match the decision: Answer, Use recommendation, Request changes, Launch
batch, or Review release. No general Approve button on failures or unanswered
questions. Deferring a decision keeps it visible and blocks only its dependents.
One answer can resolve several questions only when they reference the same recorded
decision. Existing accepted-risk release exceptions remain explicit and separate
from meeting verification requirements.

### Stories

Show Draft, Refining, Ready, Scheduled, Working, Needs input, Awaiting integration,
and Done. Stage (spec/design/code/test) is separate from work state. Blocked work
names its blocker and the valid recovery action. A selected story shows its
requirements, dependencies, planned change areas, interview, decisions, attempts,
and evidence. Offer Refine now without starting a pipeline run. Preserve history
when a story is split, amended, superseded, or retried.

### Activity

A chronological feed of meaningful events: decisions answered, stages completed,
checks failed, retries attempted, work queued, and candidates integrated. Raw model
output is available on demand. Messages must describe the actual resulting state:
an approval that resumes into a failed run cannot remain a green “approve done”.

### Controls and interaction

- Pause new starts prevents scheduling additional jobs; it does not pretend to
  interrupt an in-flight model call. A per-job stop request takes effect at a
  documented safe boundary and records what happened.
- Questions and response drafts survive navigation and restarting the board.
- Long-running work does not take focus or repeatedly open dialogs. Notify once,
  update the inbox, and let the operator choose which item to address.
- Closing the board detaches from durable workers; it does not cancel accepted jobs.
  Worker absence, interruption, and stale heartbeats are visible states.
- Two board/CLI clients cannot answer a decision twice or launch a story twice.
- At narrow terminal widths, show a list and open the selected detail as its own
  view. Keep the action reachable without horizontal scrolling. Use text/icons as
  well as color, stable keyboard focus, and explicit Enter/Escape actions.

## Interview and decision memory

Product intake has four resumable sections: product purpose and scope; user
experience; technical approach; and execution agreement. Technical discussion
includes the existing system, stack, architecture, data, integrations, security,
hosting, operational limits, and verification commands as relevant to the product.
The existing `ProjectSpec` is the initial technical artifact, not a replacement for
the discussion. A static page should not be forced to choose an unnecessary database.

Known answers and repository facts are reused. The factory presents consequential
tradeoffs, offers a recommendation, and records “you decide” as delegated authority.
Uncertainty is kept as an open question; it is not silently recorded as approval.

Each refinement session has a stable subject, input revision, phase, pending
questions, saved answers, proposals, and state. Answering a question records an
event and schedules the next bounded step. No worker must remain blocked waiting
for a human to respond. Preparing a story produces a saved draft/spec/design; it
does not automatically begin coding.

Approved brief/spec/decision revisions form the context snapshot for a story.
Amendments create a new revision and mark affected plans stale. Existing runs keep
their recorded context until reassessment explicitly authorizes a change. Unknown
impact pauses dependent work rather than silently replacing its requirements.

## Compatible batch proposals

Planning records story dependencies and expected reads/writes of files, components,
API contracts, data schemas, shared configuration, and product decisions. Model
analysis proposes this information with reasons; deterministic rules enforce it.

Only sufficiently prepared stories enter a launch proposal. Exclude cycles,
unresolved prerequisite decisions, unmet dependencies, shared write areas,
read/write contract conflicts, stale plans, and uncertain compatibility. Different
file names alone do not establish independence. Show excluded stories and reasons.
An operator may deselect stories; overriding a real dependency or conflict requires
a revised plan, not an unchecked “force parallel” switch.

A proposal names exact story/plan revisions, baseline, compatibility explanation,
concurrency cap, and spending limit. Revalidate when launching and when scopes
change. A first default cap of two building stories is conservative and configurable.
Waiting for human input frees compute capacity while retaining the story's resource
claims. Unrelated approved members can continue; unapproved stories are not added
automatically. Sequential execution remains a valid outcome when no safe pair exists.

## Isolation, execution, and integration

Every concurrent story has a persisted branch/worktree and pinned base revision.
Code and story-specific evidence writes go to that workspace. Resume, retry,
verification, diff construction, and release review resolve the same workspace.
Product-wide brief/backlog publication is serialized and versioned independently.
Worktree isolation does not imply operating-system sandboxing: execution policy for
generated code remains separately enforced and represented honestly.

Durable jobs have atomic claims, owner identity, heartbeat/lease, and bounded state
transitions. Slow model calls happen outside database transactions. Completion and
human responses are idempotent. A lost worker's potentially partial operation is
reconciled against its workspace and recorded stage before retrying; a stale lease
is not permission to duplicate an unknown in-flight write.

The scheduler reserves budget for active calls so parallel workers cannot each
spend the same remaining allowance. Per-story and batch caps coexist; unresolved
usage cannot be treated as free. A question pauses the relevant job, not the engine.

Integration is serialized per project into an isolated candidate checkout. Merge
approved story candidates there, execute required checks against the combined
revision, and bind review evidence and release authorization to that revision.
Conflicts or failed integration checks trigger bounded repair/review or an explicit
decision. They do not mutate the reviewed candidate under an existing approval.
Story verification, integrated verification, release authorization, and deployment
remain distinct visible facts. Dependencies become available only at their declared
integration point, not because another agent said its code was finished.

## Gates and the playbook

Keep INTENT -> SPEC -> PLAN -> ADR -> implementation -> executed verification ->
independent review -> release evidence. Keep deterministic stage authorization,
protected write scope, regression protections, replay, evals, and an audit trail.
Do not turn a passing model review or missing test tool into verification success.

Routine work may proceed under a recorded project policy. Human intervention is
needed for unresolved material choices, departures from accepted constraints,
unavailable authority/access, budget increases, exhausted repair attempts, selected
batch launch, and release approval. Facts already settled by an applicable decision
must not be asked again. A model may suggest that a decision is routine, but cannot
grant itself authority. Existing gate behavior must be mapped and regression-tested
before changing any human trigger.

The Checkers `.prettierignore` incident is a concrete regression scenario: a file
required by the accepted architecture was absent from every task's allowed scope.
Preparation must reconcile design scope and task scope before a story becomes
ready. Remediation must request an explicit plan revision or use scope already
authorized by the accepted design; it cannot silently broaden writes to pass a gate.

## Architecture and migration

- `domain`: pure workflow transitions, dependency/conflict checks, typed actions,
  and policy decisions.
- `state`: additive persistence for stable refinement sessions, questions and
  responses, revisions, assessments, proposals, jobs/leases, and run workspace IDs.
- `runs`: common board/CLI operations, scheduling, decision submission, and read
  models. Interfaces do not implement another scheduler or write SQL.
- `workspace`: worktree lifecycle and serialized integration using checked Git
  operations. Git failures cannot silently count as a successful integration.
- `pipeline`, `verification`, `evidence`: preserve existing orchestration while
  accepting pinned contexts and run workspaces; bind evidence to exact candidates.
- `interfaces/board`: focused overview, inbox, stories, and activity components;
  avoid growing the existing single `tui.py` for every new feature.

Existing projects and runs remain readable. Legacy single-checkout runs keep their
current resume path and project exclusion until finished. New concurrent runs opt
into explicit isolated workspace metadata. Preserve existing story IDs and history;
backlog amendments must not delete independently refined stories. Reuse approved
interview answers and ask only for missing decisions relevant to the next action.

## Delivery order and acceptance

1. Establish a passing baseline including the earlier unverified overview and merge
   changes. Add decision-oriented read models and reorganize the board using real
   existing states. Preserve the CLI and human approval semantics.
2. Persist refinement sessions, technical interview phases, and questions. Prove
   that one story can be refined while another progresses, and that navigation,
   process restart, and duplicate submissions preserve the correct answers.
3. Add plan readiness and dependency/conflict assessment with reviewable batch
   proposals. Prove unknown/shared areas and stale plans cannot be launched as
   compatible. Reproduce the Checkers scope mismatch as a regression case.
4. Implement and verify isolated workspaces, job claims, budget reservations, and
   integration/release evidence before enabling concurrent story execution.
5. Connect approved batch launch to durable workers and the board. Prove two
   independent stories progress, shared-area stories wait, and an unanswered
   question leaves unrelated work running.

Use offline fixtures and disposable project repositories for automated checks.
Essential checks include double launch/answer races; restart after a committed
step; worker loss; parked-run scope reservations; merge conflict; stale release
approval; failed/missing product checks; and combined-revision verification.
Inspect the board at normal and narrow terminal sizes with representative states,
including no runs and several simultaneous pending questions. Run `make check` and
agent configuration evals for affected behavior. Live model calls or product runs
are not required to prove scheduler and UI correctness.

Success is measured by fewer unnecessary human interruptions, explainable waiting,
independent work progressing concurrently, accurate release evidence, and the
operator being able to find and answer a decision without reconstructing run logs.
