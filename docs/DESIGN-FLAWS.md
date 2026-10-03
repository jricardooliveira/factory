# Design flaws — what the factory gets wrong by construction

Written 2026-10-03 at commit `e4c346c`, after building a real product ("habits", a
local habit tracker) through the factory: 9 pipeline runs, 4 stories released, about $26
of story spend and $1.43 of intake spend. Every figure below was read from
`~/.factory/factory.db` or from the product repository on that date.

This is **not** a bug list. `docs/IMPROVEMENTS.md` lists defects and small improvements.
This file lists things that are wrong **in how the factory is designed**, so that fixing
one symptom leaves the cause in place. Most of them were patched today at the point where
they hurt; each entry says what the patch was and why it is not the fix.

It is written to be handed to an agent that will redesign these parts. For each flaw:

- **By design** — what the factory does, and why that is structurally wrong.
- **Evidence** — what happened on real runs.
- **Patched so far** — the symptom fix already in the code.
- **Real fix** — the direction of a proper redesign.
- **Done when** — an observable check.

Flaws 1–5 are the ones that cost the most money and operator time; start there.

---

## A. The order of work is wrong

### 1. Tasks and their file lists are decided before the design exists

**By design.** The pipeline is spec → architect → coder. The spec-agent writes the
acceptance criteria **and** the task breakdown, including which files each task may
write (`tasks[].scope`). Only afterwards does the architect decide the modules, layers
and files. So the unit of work the coder is held to is drawn by an agent that has not
seen the design, and the design is then forced into task boundaries that predate it.

**Evidence.** Run 8, task 4: the architect's design put part of the work in
`app/routes.py`; the spec had given that file to task 3, which left it undone. Task 4 was
not allowed to touch it and could only refuse — twice, on the escalation model, $1.82 and
$2.97. Run 8 cost $7.75 and released nothing.

**Patched so far.** A task may now write any source file the approved design lists
(`verification.scope.design_scope`). The task split itself is still made before the
design.

**Real fix.** Split the spec-agent's job. The spec states the problem, the acceptance
criteria and the non-goals. The task plan (order, files per task, which criterion each
task delivers, which existing tests each task must change) is produced **after** the
design, by the architect or a planning step that reads the design and the code. PLAN.md
already exists as the artifact; it should be the source of the tasks, not a rendering of
tasks made earlier.

**Done when.** No task in a run names a file the design does not, and a task that needs a
file outside its list is a planning error caught before any coder call.

### 2. Tests are a separate last task, written after the code they test

**By design.** Nothing tells the spec-agent to pair each task with its tests, and it
consistently makes "tests" the final task. In every released story the last task was the
test task: run 1 "Automated tests for page, health check…", run 5 "Add route and
end-to-end tests…", run 6 "Acceptance tests and README…", run 7 "End-to-end acceptance
test…", run 9 "Acceptance tests and README…". The factory's own rule for its developers
is test-first (`CLAUDE.md`: "TDD is mandatory"); the products it builds are test-last.

**Consequences.** The per-task build gate has little to run for the early tasks, so a
wrong task 2 is found at task 5 or by the tester. The tester then judges coverage by
reading a diff rather than by tests that were written against the criteria first.

**Patched so far.** Nothing.

**Real fix.** Each task carries the tests for the behaviour it adds, and the plan says
which acceptance criterion each test proves. The gate for a task runs that task's tests
plus the existing suite. A closing "acceptance tests" task is allowed only for
cross-cutting flows.

**Done when.** For a released story, every task's commit contains test changes (or the
plan says why not), and each acceptance criterion maps to a named test.

### 3. A story that changes behaviour is set up to fail its own gate

**By design.** With tests on, the gate runs the whole suite after each task, and a task
may only write its own files. A story that deliberately changes existing behaviour breaks
tests that pinned the old behaviour, and those tests are outside the task.

**Evidence.** Run 8: the amount row gained a number box and a +1 button; tests from the
previous story expecting `"Water 0 / 8"` failed; no attempt at task 4 could pass.

**Patched so far.** On a retry the coder may change the test files that just failed
(`state["retry_scope"]`). That means the first attempt is always wasted, and the second
runs on the most expensive model.

**Real fix.** Planning (flaw 1) should name the existing tests a story will invalidate,
and put their update in the task that changes the behaviour. A test that fails for a
reason the plan predicted is expected, not a failure.

**Done when.** A behaviour-changing story passes each task on the first attempt.

---

## B. Limits and failures are discovered after the money is spent

### 4. A gate that the agent could satisfy ends the run instead of asking again

**By design.** Gates are terminal. When a story has too many tasks, or a design too many
files, the run fails after the spec and the design were paid for. The agents were not
told the limits they were judged against.

**Evidence.** Runs 2, 3 and 4 failed on size ($0.18, $0.60, $0.63). The backlog had been
approved with stories that could not fit.

**Patched so far.** The limits are now written in the three agent definitions
(`spec-agent.md`, `architect-agent.md`, `backlog-agent.md`), and an oversized story or
design is sent back once (`slot="resize"`).

**Real fix.** Make "fixable by the agent" a first-class gate outcome: the gate returns
its reason to the stage that can fix it, within a small budget, and only fails when that
budget is spent. Check size at the cheapest point: when the backlog is proposed, not
after a story's design. A limit that exists in `domain/gates.py` and not in the prompt of
the agent it constrains should be impossible (a test over all `MAX_*` constants).

**Done when.** No run fails on a size limit without the responsible agent having been
given the limit and one chance to fit.

### 5. A retry is the most expensive call in the system, and often cannot help

**By design.** The second attempt at a task escalates to the top model
(`[escalate_on_retry] coder-agent = "special"`), with the same prompt plus the failure.
The factory does not ask **why** the first attempt failed. If the cause is the plan, the
scope, a truncated input or the environment, a stronger model can only explain that it
cannot proceed.

**Evidence.** Five escalated calls so far: $2.05, $1.14, $1.04, $1.82, $2.97 — $9.02,
about a third of all story spend. Three of the five produced no code: one "remediation"
of code that was not broken (run 5, the tester had been shown a truncated diff) and two
refusals (run 8). The two that worked fixed small test failures the coder had caused
itself.

**Patched so far.** Nothing; which model a retry uses is listed as an operator decision
in `docs/IMPROVEMENTS.md`.

**Real fix.** Classify the failure before retrying: environment (do not retry, tell the
operator), plan or scope (go back to planning), truncated or missing input (fix the
input), code (retry — on the same model first, escalate only on a second failure). The
coder already says which it is in `design_feedback`; the factory should act on that
instead of counting it against a re-architecture budget.

**Done when.** No escalated call ends in "blocked" or "design-infeasible", and the share
of story spend on retries falls well below today's third.

### 6. The budget cap does not bind the thing it is named after

**By design.** The cap is $10 "per user story", keyed on the story row. Every re-run of a
backlog story creates a **new** story row, so the cap starts again.

**Evidence.** The backlog story "create yes/no habits and list them" ran as US-0004 and
US-0005 ($0.63 + $6.42 = $7.05). "Add to today's amount" is US-0008 ($7.75) and now
US-0009 ($1.11 so far): together $8.86 with the second run only at Checkpoint 2, so the
story will pass $10 without any single story row reaching it. Intake calls (interview, stack, backlog: $1.43 over 36 calls) are outside
any cap.

**Patched so far.** Nothing.

**Real fix.** One identity for a backlog story across all its runs; the cap and the
spend are per that identity and per project. A re-run continues the same story.

**Done when.** `factory status` shows one spend figure per backlog story, and a third
attempt at a story is refused when its total passes the cap.

---

## C. Work and decisions are thrown away

### 7. Re-running a story starts from nothing

**By design.** The only re-drive for a failed run without an answered checkpoint is
dismiss → `factory next`, which creates a new story and runs the story interview, the
spec and the design again. The operator's answers to the story interview are appended to
the request text of one run and stored nowhere else.

**Evidence.** The story interview was answered twice for "create yes/no habits and tick
them off" (runs 2 and 3), twice for "create yes/no habits and list them" (runs 4 and 5)
and twice for "add to today's amount" (runs 8 and 9). The spec, and the design where the
run got that far, were paid for again each time.

**Patched so far.** A returned story keeps its failed run's base commit, so earlier code
is at least reviewed.

**Real fix.** Store the story clarifications, the approved spec and the approved design
with the backlog story. A re-run reuses whatever is still valid and asks only what is
new.

**Done when.** Re-running a failed story asks no question that was already answered.

### 8. Decisions made in a story interview never reach the brief

**By design.** The brief is the product's definition, but product-level decisions taken
during a story interview are only in that story's request.

**Evidence.** "The day ends at 4:00 am" was decided in a story interview. The approved
brief (`docs/work/BRIEF.md`) does not contain it. The question came back in the next
story's interview.

**Patched so far.** Nothing.

**Real fix.** After a story interview, decisions that are about the product rather than
the story are proposed as an amendment to the brief, which the operator approves once.

**Done when.** A product-level decision is asked once per project.

### 9. Every prompt carries the whole history

**By design.** Project memory injects every earlier decision record into every agent
prompt. It grows with each story and is not selected by relevance. It includes records
of designs that Gate 2 rejected.

**Evidence.** The coder's average input grew from about 4,100 tokens on run 1 to 35,000
on run 5 and 57,500 on run 8. There are 8 decision records in the product after 4
released stories. Fixed cost per story rose with it.

**Patched so far.** Nothing.

**Real fix.** Retrieve what is relevant to the task (the files it touches, the terms it
uses), cap the block, and never include a rejected design.

**Done when.** The coder's prompt size is roughly flat from story 1 to story 10.

---

## D. The evidence cannot be trusted as shown

### 10. The reviewer judges from a lossy text view, and coverage from prose

**By design.** The tester receives a text diff with a character budget and returns
free-text claims. Whether each acceptance criterion is "covered" is then derived by
matching that prose against the criteria text.

**Evidence.** Run 5: the diff was cut at 16,000 of 35,000 characters, the test files were
lost, and the tester failed all 13 criteria. Runs 5, 6 and 7: the gate reported 2, 4 and
3 criteria "never assessed" that the tester had in fact covered.

**Patched so far.** A larger diff budget with fair truncation; matching by "AC number".

**Real fix.** Criteria have stable ids from the spec onward. Tests declare the ids they
prove. Coverage is computed from executed tests, not from what a model wrote. The tester
receives test results and reviews what the tests do not show: design conformance,
security, missed edge cases.

**Done when.** "AC covered" in the trust package is a function of executed tests and
criterion ids, with no text matching.

### 11. By default nothing is executed, so every release is "NOT READY"

**By design.** Default verification compiles and checks imports. Running tests needs an
environment variable on each command and a product environment the operator creates by
hand. There is no sandbox.

**Evidence.** All four releases were approved over "NOT READY for release". For runs 1
and 5 the only test evidence was a manual run outside the factory. One real failing test
(run 5) was invisible to the factory.

**Consequence.** The operator learns to approve "NOT READY". A warning that is always
present carries no information, and the checkpoint stops being a decision.

**Patched so far.** Tests run after every attempt when switched on; the run header says
when they will not run.

**Real fix.** Creating the product's environment is part of the scaffold and of the
story that adds a dependency. Executing tests in an isolated environment is the default,
recorded with the run. "NOT READY" then means something is actually missing.

**Done when.** A story with passing tests reaches Checkpoint 3 as READY with no operator
setup.

### 12. Checkpoint 2 stops on every story

**By design.** A sensitivity tag from the architect parks the run for the operator even
when the boundary review passed every dimension.

**Evidence.** All 6 runs that reached Gate 2 parked on "security" or "security, pii",
each with a fully passing boundary review. Each was approved without change.

**Consequence.** As with flaw 11: a stop that always fires and is always approved is not
a control.

**Real fix.** This is the operator's policy, but the design should make the stop
proportional: park when a review dimension did not pass, when the story touches
something new in a sensitive area, or on a sample. Record the rest as reviewed.

**Done when.** The operator is stopped at Checkpoint 2 only when there is something to
decide.

---

## E. The mechanics work against the agents

### 13. Whole-file replacement, with inputs that can be cut

**By design.** The coder returns complete files, never edits. To change a file it must
be shown the whole file, and its cost grows with file size rather than with the size of
the change.

**Evidence.** Run 8: `tests/test_app.py` was 14,812 characters and was shown to 12,000.
The coder correctly refused to return a file it could not see.

**Patched so far.** The cap is 40,000 characters and a cut file says "do not return it".
The mechanism is unchanged.

**Real fix.** An edit format (search and replace, or a patch) checked against the file's
current hash, with whole-file output only for new files.

**Done when.** Changing three lines of a large file costs about three lines of output.

### 14. Routing reads leftover state instead of the record

**By design.** What happens next is decided from values in the graph's in-memory state,
which earlier steps leave behind. The database is the record, but not the source of the
decision.

**Evidence.** Run 1: a blocked run still held `next_action: next_task` from the previous
task and looped about 20 times until LangGraph's step limit. `factory retry` used to
rebuild every task because nothing derived progress from the record.

**Patched so far.** The router ends on a terminal status; resume derives built tasks
from gate rows and logs.

**Real fix.** One place that computes the next step from the durable record (the same
way the boss authorises a stage), used for a fresh run, a resume and a retry alike.

**Done when.** Killing the process at any point and resuming gives the same next step as
if it had not been killed.

### 15. How a run behaves depends on the shell it was started from

**By design.** The runner and test execution are environment variables on each command.
The same project can be run with different models and with or without tests depending on
who typed the command, and the run does not record which.

**Evidence.** The board started without the variable used a different runner from the
terminal. Tests were on for runs 6–9 only because each command carried the variable.

**Real fix.** Settings belong to the project (or `factory.toml`), are shown by
`factory status`, and are stored with each run.

**Done when.** Two operators running the same command on the same project get the same
configuration, and a run's record says what it was.

### 16. The interview asks in batches that cannot see each other

**By design.** The interview agent returns up to four questions per call. The second
question is written before the first is answered.

**Evidence.** The first product interview asked "who uses it?" right after the operator
wrote "just for me". An unsure answer in a batch could not be followed up until the next
call.

**Patched so far.** The first turn asks one question; unsure answers are followed up
later and re-asked at the end.

**Real fix.** One question per call for anything that depends on an earlier answer; batch
only questions that are independent. The product interview should get the same handling
of unsure answers that the story interview has.

**Done when.** No question in a transcript is answered by an earlier answer.

---

## What these have in common

1. **Decisions are taken before the information they need exists** (1, 2, 3, 16).
2. **The cheapest place to catch a problem is not where it is caught** (4, 5, 6).
3. **Paid work is discarded instead of reused** (7, 8, 9).
4. **Signals that always fire stop being signals** (11, 12).
5. **Evidence is inferred from model prose instead of measured** (10).

A redesign should be judged against these five, not against the individual patches.

## Not verified

- Whether a cheaper model would have fixed the failures the escalated retries fixed
  (flaw 5). There is no comparison run.
- Flaw 2's consequence that early tasks have "little to run" is inferred from the task
  titles; the per-task test counts were not measured.
- Flaw 9's claim that rejected designs are included in project memory comes from the
  review in `docs/IMPROVEMENTS.md` and was not re-checked here.
