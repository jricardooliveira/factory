# Factory improvements — ranked, with evidence

## How to read this

Reviewed on **2026-10-03** at commit **`b7b165e`** ("the tester sees the whole story; a cut diff never drops a file"), with `make check` green (1030 passed, 1 skipped; 12/12 scenarios; 68/68 evals). Every claim below was checked against the code at that commit and against the factory database for the real product **habits** (`~/.factory/factory.db`, read-only: `pipeline_runs` 1–6, `agent_logs` 1–74, `gate_results` 1–48, `interview_turns`, `backlog_stories`) and the product repository (`~/.factory/projects/habits`, its `git log` and `docs/work/*/PIPELINE.md`). Dollar figures are the per-call `cost_usd` the Claude Code command-line runner reported (100% of calls instrumented, per `factory metrics`). Where a claim could not be verified it is marked **unverified**. Nothing else in the repository was changed; no model was called.

Vocabulary: AC = acceptance criterion; ADR = architecture decision record; CLI = command-line interface; Checkpoint 1/2/3 = the three operator sign-offs (spec, design, release); PII = personally identifiable information; "tier" = the model role in `agents/tiers.toml`.

The six runs in one table (from `agent_logs`, summed per run):

| Run | Story | Outcome | Cost | Where the money went |
|---|---|---|---|---|
| 1 | US-0001 walking skeleton | released | $1.27 | 21 zero-cost `blocked` coder calls (Claude CLI "session limit", one every 4 s, since fixed in `3d1686b`); `factory retry` then re-ran tasks T-0001..T-0004 that were already committed ($0.28) |
| 2 | US-0002 | failed at gate-1 | $0.18 | 9 tasks > max 6 |
| 3 | US-0003 | failed at gate-2 | $0.60 | 17 modules > max 12 (limit now 16) |
| 4 | US-0004 | failed at gate-2 | $0.63 | 16 modules > max 12 (limit now 16) |
| 5 | US-0005 create habits | released | $6.42 | tester saw a 16 000-char truncated diff: two failing tester passes ($0.84) + a remediation on the `special` tier ($2.05) + `factory retry` re-ran all six coder tasks ($0.99, four of them produced nothing) = **$3.89, 61% of the run** |
| 6 | US-0006 tick habits | released | $4.87 | two coder retries on the `special` tier ($1.14 + $1.04 = **$2.19, 45%**), each fixing a trivial test failure the first attempt caused |

Intake (product interview 8 turns $0.38, stack 1 turn $0.05, story interviews 18 turns $0.68) = $1.12, outside every story's cap. Released work cost $12.56 for three small stories; a clean pass (run 1) cost $1.27.

---

## Top recommendations

Ranked by value for effort. "Interruptions" counts separate times the operator must stop and act per story.

### 1. Run the product's tests after every attempt, whatever files it wrote

**Status: done** in `verification/__init__.py` (the suite run moved out of `if py_files:`; decided from the repository) and `verification/go.py` (`modules_with_tests`); tests in `tests/verification/test_verify.py` (`SuiteRunsWhateverTheAttemptWroteTests`). Still open: with tests OFF (or no suite), a coder returning zero `code_blocks` + `complete` still passes on `verify:skip` — a policy question, not this code path.

**Problem.** `verify_changes` (`src/factory/verification/__init__.py:44-55`) chooses checks by the extension of the files *this attempt* materialized, and the `pytest` run sits inside `if py_files:`. An attempt that writes only a template, a JavaScript file, or nothing at all gets a single `verify:skip` check, and `VerifyResult.passed` is "no check failed" (`verification/base.py:46-47`), so the task passes and is committed without the suite ever running.

Evidence: run 6, task T-0004. Attempt 1 failed `pytest_run:fail` on a template-content assertion (`gate_results` id 44). Attempt 2 changed only the template and was recorded `[T-0004] verify:skip`, passed (id 45) — the failing test was never re-run for that attempt. It happened to pass at T-0005 (id 46), but had T-0004 been the last task the story would have reached the tester and the trust package with `tests.passed: true`, because `_test_execution` (`evidence/trust_package.py:71-107`) takes the *newest* `pytest_run` marker, which would have been T-0003's pass. The same hole lets a coder that returns zero `code_blocks` with `verdict: complete` pass a task (run 5 retry, ids 30–34: four `verify:skip` passes).

Offline reproduction (done for this review): with `FACTORY_RUN_TESTS=1`, a repo containing `tests/test_x.py` that asserts `False` and `verify_changes([<root>/app/templates/index.html], root=<root>)` → `verify:skip`, `passed=True`; `verify_changes([], root=<root>)` → the same.

**Change.** Decide "run the suite" from the repository, not from this attempt's extensions: in `verify_changes`, after the per-toolchain checks, `if tests_enabled() and python.has_tests(root): result.checks.append(python.run_tests(root))` (and the Go equivalent per module), independent of `py_files`. Keep `verify:skip` only when the repo has no suite. Add the regression test in `tests/verification/test_verify.py`. The contract line in `docs/contract/GATES.md` ("Tests are run after any Python change") becomes "after any change".

**Benefit.** Closes a false PASS on the one gate that produces release evidence; zero extra cost on the happy path (the suite already runs per task). **Effort: S.** No operator decision.

### 2. `factory retry` (and any resume into the coder) continues at the first unbuilt task

**Status: done** in `runs/context.py` (`built_tasks`, `last_build`), `runs/service.py` (`resume_run` seeds `task_index`/`tasks_completed`) and `pipeline/graph.py` (`compile_tester_resume_pipeline`: every task built + newest build green → enter at the tester); tests in `tests/runs/test_retry_progress.py`. Built is read from the run's gate-build rows + coder verdicts, not from commits. Still restarts at task 1: every task built but the newest gate-build (a remediation pass) failed.

**Problem.** `resume_run` → entry `coder` → `compile_coder_only_pipeline()` with a state carrying no `task_index`/`tasks_completed` (`runs/service.py:390-394`, `_resume_state` lines 429-467), so the coder starts at task 0. Tasks already committed are re-run and re-paid.

Evidence: run 1 ids 29–33 re-ran T-0001..T-0004 after they were committed at 02:46 ($0.28 for nothing); run 5 ids 55–60 re-ran all six tasks after the tester failures ($0.99 — four returned no code and passed on `verify:skip`, two re-emitted files already in HEAD; `git log` shows the duplicated `factory: T-0004` / `T-0006` commits). Total waste $1.27, the price of story 1. A side effect the operator reported is also explained here: on a retry with tests on, the whole suite (including later tasks' committed tests) runs after task 1's re-attempt, so a failing later test fails an earlier task that is fine.

**Change.** In `resume_run`'s coder branch, rebuild progress from the record the boss already trusts: the `gate-build` rows that passed for `[T-xxxx]` (or the `factory: T-xxxx` commits since `base_commit`) give `tasks_completed`; set `task_index` to the first task not in it; if every task is built, enter at `tester-agent` instead (that is exactly what run 5 needed once the diff bug was fixed). `domain/authorization.authorize_coder` already requires dependencies built, so the rule has a home. Files: `runs/service.py`, `runs/context.py`, a test in `tests/runs/`.

**Benefit.** ~$0.3–1.0 per retry today, more as stories grow; removes the "retry fails on a later task's test" trap. **Effort: M.** No operator decision.

### 3. A provider error is "unavailable, try later", not the agent going off-script

**Status: done** in `pipeline/agent_calls.py` (`ProviderUnavailable`, raised before parsing, no repair call; the nodes' existing `except` ends the run `failed` with the provider's message); tests in `tests/pipeline/test_json_repair.py` and `test_coder_route_ends.py`.

**Problem.** `run_agent_json` (`pipeline/agent_calls.py:74-97`) never reads `AgentResult.success`/`returncode`; a failed call's output (`claude_cli.py:75,79`: `"ERROR: …"`) is parsed as non-JSON, a *second* paid repair call is made with the same prompt, and the node records `blocked` with "Agent went off-script: ERROR: You've hit your session limit · resets 4am". That ends the story as `blocked` and the operator must diagnose it from `factory review`.

Evidence: run 1, `agent_logs` ids 8–28: 21 identical `blocked` rows, 0 tokens, 2 s each, output `ERROR: You've hit your session limit · resets 4am (Europe/Lisbon)`. The infinite loop is fixed (`3d1686b`), the misclassification is not.

**Change.** In `run_agent_json`: if `not result.success`, raise a new `ProviderUnavailable` (sibling of `BudgetExhausted`) with the adapter's message; the boss/node ends the run `failed` with that exact text and **does not** count it as an attempt (`attempt_number` unchanged) nor make the repair call. `factory status` already offers `retry` for a failed run with an answered checkpoint; with recommendation 2 that retry resumes where it stopped. Files: `pipeline/agent_calls.py`, `pipeline/nodes/coder.py` (the `except` path), `runs/events.py` wording, a test seeding a failed `AgentResult`.

**Benefit.** No wasted repair call, no false "off-script" verdict in the evidence, one clear line for the operator. **Effort: S.** No operator decision.

### 4. Make the Checkpoint 3 evidence say only what is true (three small fixes)

**Status: done.** 4a in `domain/traceability.py` (a leading `ACn` / `n.` reference is an exact match; the tester prompt was NOT changed — testers already number by position); 4b in `workspace/git.py` (`git_line_stats`) + `evidence/trust_package.py` (`_test_change`, `diff.files[].additions/deletions`); 4c in `evidence/trust_package.py` (`security_boundary.notes`), the schema, and `interfaces/render/review.py`.

The operator signs off on gaps that are not gaps; that trains them to click through (`EFFECTIVENESS.md §10`).

**4a. "acceptance criteria never assessed" is a matching bug.** `domain/traceability.py` matches a criterion to the tester's claims by token overlap ≥ 0.5 of the shorter set. The tester writes `AC2 one click on the row ticks … (test_tick_then_untick)`; test names and paraphrase dilute the overlap. Evidence: run 6 the tester's `ac_coverage` has 12 entries labelled AC1…AC12, yet gate-test (id 47) and the trust package say "4 AC unassessed"; run 5 says 2. Reproduced offline: `trace_criteria([<spec AC2>], [<the tester's AC2 line>], [])` → `unassessed`. **Change:** number the criteria in the tester prompt (`pipeline/prompts/tester.py` — a golden update) and, in `trace_criteria`, honour a leading `AC<n>`/`<n>.` reference as an exact match before falling back to fuzzy matching. Effort S.

**4b. "Existing tests were changed or deleted" fires on pure additions.** `trust_package._blockers` (lines 207-219) flags any test file whose git status is `modified`. Evidence: run 5 changed `tests/test_app.py` with **582 insertions, 0 deletions** (`git diff --stat 1f92354c..48a38909 -- tests/`) and the blocker fired; run 6 (398+, 5−) had one genuinely narrowed assertion, which the AC itself demanded. **Change:** measure with `git diff --numstat` in `workspace/git.py` and flag a test file only when it has deleted/changed lines, naming the count. Effort S.

**4c. The security line dumps every boundary note.** `_print_trust_package` (`interfaces/render/review.py:197-198`) prints `sb['findings']` as a Python list; `_security_boundary` (`trust_package.py:264`) appends every finding of every dimension, including the informational notes of a `pass`. Evidence: run 6 package: `overall: pass`, 12 findings, all "boundary review: …" explanations of why something is fine. **Change:** include a dimension's findings only when its verdict is `warn`/`fail`; render one finding per line. Effort S.

**Benefit (together).** Checkpoint 3 goes from "3 gaps" to the real gaps (run 6 would have shown only "migration notes missing"). Trust in the evidence, no cost. No operator decision.

### 5. Checkpoint 2 parks on every story for a "sensitivity" tag the boundary review has already cleared — OPERATOR DECISION

**Problem.** `gate_after_architect` (`domain/gates.py:286-300`) parks whenever `architect.sensitivity` is non-empty, regardless of the boundary review. `agents/architect-agent.md:57` tells the architect to list *all* applicable tags; for a single-user loopback app it tagged `security` (the Host/Origin guard) and `pii` (habit names) on every story. Evidence: all three released runs parked at gate-2 with `SENSITIVE WORK detected [security(, pii)]` while the boundary review was `tenant: not_applicable, authorization: pass/not_applicable, api_contract: pass, security: pass` (gate ids 2, 20, 39). Checkpoint 1 never parked in six runs (the story interview pre-empts it), so Checkpoint 2 is the extra interruption, and the question carries nothing the operator can act on (339–349 characters; answered in ~25 s in runs 5 and 6).

**Options.**
- (a) Keep as is: a human confirms every sensitive design. Cost: one interruption per story, forever.
- (b) A boundary review whose four dimensions are all `pass`/`not_applicable` answers the sensitivity question: gate-2 passes without a park; a `warn`, `fail` or `unavailable` review still parks (those branches already exist, lines 262-276). One condition in `gates.py`, one eval case.
- (c) As (b) but per project: `project-spec.json` (or `factory.toml`) `checkpoint2 = "always" | "on_findings"`, default `always` — the "per-project autonomy level" `EFFECTIVENESS.md §9` already names as the transferable kernel.
- (d) Narrow the architect's tagging in the prompt. Rejected: a behavioural guarantee resting on an agent choosing to behave (`§10`).

**Recommendation:** (c) with default `always`, set `on_findings` for habits. Saves one interruption per story (≈15 for habits). **Effort: S.**

### 6. Retry on the `special` tier is the single most expensive line item — OPERATOR DECISION

**Problem.** `[escalate_on_retry] coder-agent = "special"` (`agents/tiers.toml`) sends every second attempt and every remediation to Fable at $10/$50 per million tokens — 5× the `build` tier (Sonnet $2/$10) and 2.5× `frontier` (Opus $4/$20) under the Claude runner. Evidence: run 6's two retries $2.19 (45% of the run) fixed (i) a test the coder itself wrote asserting `app.config["CLOCK"] is datetime.now` (identity on a bound method) and (ii) a template text mismatch; run 5's remediation $2.05. Those same tokens on Opus would have cost $0.56 and $0.59; on Sonnet $0.28 and $0.30. Whether the cheaper model would have fixed them is **unverified** (no counterfactual run exists).

**Options.**
- (a) Keep `special` (strongest model, different perspective — the policy's stated rationale).
- (b) Escalate to `frontier` instead: same "different perspective" under opencode, half the price under both runners. One line in `tiers.toml` + the frontmatter drift test.
- (c) Retry once on the same tier with the failure text (the first attempt's own test bug is usually a one-line fix), escalate only on a second retry — needs `MAX_CODER_ATTEMPTS = 3` and the escalation rule keyed on attempt ≥ 3 (`agent_config/tiers.tier_for_agent`).
- (d) Keep `special` only for remediation (reasoning-heavy, cross-cutting), `frontier` for per-task retries.

**Recommendation:** (d), then review after five stories with `factory metrics` (add "retry outcome by tier" there). **Effort: S.**

### 7. Prior-decision memory tripled every prompt and feeds rejected designs back in

**Problem.** `project_memory_block` (`pipeline/prompts/blocks.py:25-42`) injects up to `_MAX_PRIOR_ADRS = 5` whole ADRs — Decision, Constraints, Risks — into spec, architect, boundary, every coder task, and tester prompts. `load_project_memory` (`evidence/adr.py:87-115`) excludes only the current story; it includes ADRs of stories that **failed gate-2**: `ADR-US-0003` and `ADR-US-0004` carry `Status: proposed (pending architecture sign-off)` and describe designs the gate rejected as too large, yet they appear under "Prior Architecture Decisions (stay consistent; do NOT re-litigate)". `settled_threshold_terms` already refuses non-approved ADRs for the ambiguity gate; the memory block does not apply the same rule.

Evidence: run 6 spec-agent prompt is 67 250 characters, of which the four ADR renderings are ≈56 000 (83%); the project spec, brief and rules together are ≈8 100. Average input per call, run 1 → run 6: spec 7.9k → 28.1k tokens, architect 10.3k → 33.7k, boundary 13.2k → 36.1k, coder 13.8k → 41.5k, tester 21.9k → 52.1k. The fixed per-story overhead (spec+architect+boundary+tester+release) went from $0.65 to $1.49 and a first-attempt coder task from ≈$0.07 to ≈$0.24; a five-task story's coder from ≈$0.35 to ≈$1.19. It plateaus at five ADRs but at roughly three times run 1.

**Change.** (i) Include only ADRs whose `Status:` records an approval (reuse `evidence.artifacts.approval_status` wording, as `settled_threshold_terms` does) — the rejected-design ADRs then drop out. (ii) Inject only the `## Decision` and `## Data / API impact` sections of prior ADRs; constraints and risks were written for *that* story's coder. (iii) Decide (operator) whether a rejected design's ADR should stay in `docs/architecture/adr/` at all, or be removed when gate-2 rejects it. Goldens unaffected (they have no ADRs). Files: `evidence/adr.py`, `tests/evidence/`.

**Benefit.** Roughly −40% input tokens per story at today's sizes (≈$0.5–0.8 per story), and the architect stops being told to stay consistent with a design nobody approved. **Effort: S** for (i)+(ii). Operator decision only for (iii).

### 8. Test execution is a factory setting, not a per-command environment variable

**Problem.** `tests_enabled()` reads only `FACTORY_RUN_TESTS` (`verification/base.py:25-31`); `settings()` (`agent_config/settings.py`) has `[budget]`, `[timeouts]`, `[runner]` and nothing for verification. Evidence: runs 1 and 5 shipped with "Tests were never executed" as a Checkpoint 3 gap because the variable was not set for that command; run 6 had it. The release-agent then wrote a "CRITICAL EVIDENCE GAP" note on both.

**Change.** `[verification] run_tests = false` in `factory.toml`, env `FACTORY_RUN_TESTS` wins, `tests_enabled()` reads `settings().verification.run_tests`; `factory doctor` reports the effective value next to the product `.venv` check. Keep the default **off** — the contract (`EFFECTIVENESS.md §9`) ties default-on to a sandbox. Files: `agent_config/settings.py`, `verification/base.py`, `tests/agent_config/`.

**Benefit.** No more "forgot the variable" releases; one place to read what the factory will do. **Effort: S.** Decision: only the default (see Decisions).

### 9. The product interview: smaller turns, and the same "unsure" handling the story interview got

**Problem.** `MAX_QUESTIONS_PER_TURN = 4` (`runs/interview.py:64`); after the first turn every product-interview turn asked 4 questions (verified: 6 of 8 recorded turns parsed, each with 4 questions) that cannot see each other's answers. `run_interview` records an unsure answer as a decision: it calls `resolve_answer` only (lines 299-303); `is_undecided` and the follow-up logic exist only in `run_story_interview` (lines 408-431, added today in `30d8725`/`323fd53`). Evidence of the failure mode is in the story transcript of US-0002: "hmm not sure. whatever is easier I guess?", "I really don't know. what would you do?" were recorded verbatim as the operator's clarifications and the story was specified from them.

**Change.** Move the undecided/follow-up handling into a shared helper in `domain/interview.py` used by both loops; drop `_per_turn` to 2 (one broad, one dependent) or let the agent mark a question `depends_on` an earlier one in the same turn and defer it. Files: `runs/interview.py`, `domain/interview.py`, `agents/interview-agent.md` (prompt text — `make evals`).

**Benefit.** Fewer "assumed" answers in the brief, which every story inherits; the cost of extra turns is small (a product turn averaged $0.05, 14 s). **Effort: M.** Decision: turn size (see Decisions).

### 10. Shorter Checkpoint 3 question; move the release-agent's essay to the record

**Problem.** The park text at Checkpoint 3 is 2 160–3 348 characters (`gate_results.human_questions` ids 13, 37, 48): the evidence gaps, then every release-agent concern as a "◦ Release-agent note:" line. The same notes are already in `docs/work/<story>/RELEASE.md` and `factory review`. The operator reads it in a terminal to type `approve`.

**Change.** In `gate_after_release` (`domain/gates.py:363+`) keep the gaps and the *count* of release-agent concerns with a pointer to `RELEASE.md`; show the notes in full only in `factory review`. **Benefit.** The decision screen shows the decision. **Effort: S.** No operator decision.

---

## Defects found

Each is a bug (the code does something other than what its docstring or the contract says), with an offline reproduction. Defects 1 and 3 were **executed** for this review; 2, 4, 5 and 6 are **traced from the code path and the live rows** named, not executed — mark them confirmed only once their regression test fails.

1. **Build gate skips the test suite for non-Python attempts** (`verification/__init__.py:44-55`). Reproduce: `FACTORY_RUN_TESTS=1`, repo with a failing `tests/test_x.py`, `verify_changes([root/"app/templates/index.html"], root=root).summary == "verify:skip"` and `.passed is True`; also with `verify_changes([], root=root)`. Live instance: run 6 `gate_results` id 45. Contract line violated: `GATES.md` "a check that cannot run cannot earn a pass". → Recommendation 1.

2. **A provider failure is recorded as the agent going off-script and triggers a paid repair call** (`pipeline/agent_calls.py:74-97` ignores `AgentResult.success`). Reproduce: patch `factory.pipeline.agent_calls._run_or_replay` to return `AgentResult(success=False, output="ERROR: You've hit your session limit", returncode=1, …)` and drive the coder node (seed gate-1 and gate-2 rows for the boss): the run ends `blocked` with "Agent went off-script: ERROR: …" and `_run_or_replay` is called twice. Live instance: run 1 `agent_logs` ids 8–28. → Recommendation 3.

3. **Acceptance-criteria traceability reports a covered criterion as "unassessed"** (`domain/traceability.py:28-32`). Reproduce: `trace_criteria(["Clicking anywhere on a habit's row once marks it done for today without a full page reload; clicking the row again unticks it."], ["AC2 one click on the row ticks without a full reload, a second click unticks — server side tested (test_tick_then_untick) and the label-wraps-checkbox markup tested"], [])[0]["status"] == "unassessed"`. Live: run 6 gate-test id 47 ("4 AC unassessed") against a tester output listing all 12. → Recommendation 4a.

4. **"Existing tests were changed" fires on a file with only added lines** (`evidence/trust_package.py:210-219`). Reproduce: in a git repo, commit `tests/test_a.py`, append a new test function, commit, `assemble()` for a run whose `base_commit` is the first commit → blocker names `tests/test_a.py`. Live: run 5 (`git diff --stat 1f92354c..48a38909 -- tests/` = 582 insertions, 0 deletions; gate-release id 37 lists `tests/test_app.py`). → Recommendation 4b.

5. **A resume into the coder restarts at task 0** (`runs/service.py:390-394`). Reproduce: seed a run with passed `gate-build` rows for `[T-0001]`, `[T-0002]` and a `failed` status with an answered gate-2; `retry_run` → the first `NodeStarted` detail is "task 1/N T-0001 (attempt 1)". Live: run 1 ids 29–33, run 5 ids 55–60. → Recommendation 2.

6. **Rejected designs are injected as settled decisions** (`evidence/adr.py:102-113`). Reproduce: `load_project_memory(~/.factory/projects/habits, exclude_story="US-0007")` contains `ADR-US-0003` whose header reads `Status: proposed (pending architecture sign-off)` and whose story failed gate-2 (run 3). → Recommendation 7.

Not a defect, noted for completeness: `gate_results.responded_at` is overwritten by `factory retry` (run 5's Checkpoint 2 shows 10:12:40 although the coder started at 09:41:07), so the time the operator *first* answered is lost to `factory metrics` "review latency". One-line fix: `requeue_answered_gate` keeps the original timestamp, or `respond_to_gate` only sets it when NULL.

---

## Decisions waiting on the operator

1. **Checkpoint 2 when the boundary review passes every dimension** (rec. 5). Options: (a) always park; (b) a clean review clears the sensitivity park; (c) per-project setting, default "always". **Recommend (c)**, set to "on_findings" for habits.

2. **Which tier retries a failed coder attempt** (rec. 6). Options: (a) `special` as today; (b) `frontier`; (c) same tier once, then escalate (needs a third attempt); (d) `frontier` for task retries, `special` for remediation only. **Recommend (d)**; revisit with data after five more stories.

3. **Default for running the product's tests** (rec. 8). Options: (a) off unless `factory.toml`/env says on (the contract's position until a sandbox exists); (b) on when the product has its own `.venv`. **Recommend (a)**, with the setting in `factory.toml` so it is a one-time choice per machine, and the run header continuing to say when tests will not execute.

4. **What happens to the ADR of a design gate-2 rejected** (rec. 7 iii). Options: (a) keep the file as history, exclude it from agent memory; (b) delete it when gate-2 rejects. **Recommend (a)** — the record of what was tried is cheap and the exclusion rule removes the harm.

5. **Product-interview turn size** (rec. 9). Options: (a) 4 per turn as today (fewest turns, questions blind to each other); (b) 2 per turn; (c) 1 per turn (≈3× the turns, ≈$1 more per product, every question sees every answer). **Recommend (b)** plus the shared undecided handling.

6. **The runner.** Under `FACTORY_RUNNER=claude` every author and reviewer is an Anthropic model and the tester-independence rule `tests/agent_config/test_tiers.py` pins does not hold; the run-1 "session limit" shows the shared login is also a shared outage. This is the trade-off `CLAUDE.md` records; no code change proposed — only noting that the habits evidence was produced under it.

---

## Not worth doing now

- **Bringing interview/backlog spend under the $10 story cap.** They are already bounded by counts (`MAX_INTERVIEW_QUESTIONS` 40, `MAX_STORY_INTERVIEW_QUESTIONS` 8, `MAX_BACKLOG_REVISIONS` 5, `MAX_STACK_PROPOSALS` 3), total $1.12 against $13.96 of stories, and `factory status` already shows "intake" separately. A per-project cap would be a second budget to explain for no observed problem.
- **A sandbox so tests run by default.** Necessary before default-on (contract §9) and large; the setting in rec. 8 gives the operator the same outcome today on a machine they trust.
- **Branch per story + pull-request review.** Still "to build" in the contract, but nothing consumes a PR yet (no CI, no deploy); the trust package and `RELEASE.md` are the review artefact for now.
- **Reducing the architect's `sensitivity` tagging by prompt.** Would quietly remove a park by changing model behaviour; the gate rule (rec. 5) is the deterministic place.
- **Trimming the release-agent's notes themselves.** They are the most useful prose at Checkpoint 3 (run 6's named the port literal and the HTML 503 anomaly); the fix is where they are shown (rec. 10), not what they say.
- **Raising `MAX_CODER_ATTEMPTS`.** Both retries in run 6 succeeded on the first retry; more attempts would only raise the ceiling on a bad day.
- **Concurrent coder tasks, OpenTelemetry export, control bands.** Already rejected in `EFFECTIVENESS.md §9` for reasons that still hold.
- **Re-running the interview for habits.** The brief is approved and three stories are built on it; the "unsure" answers were in story clarifications (US-0002, failed and superseded), not in the brief.
