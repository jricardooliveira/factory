# Gate Catalog

Gates are **deterministic, code-based** checks between stages (`src/factory/domain/gates.py`, `src/factory/verification/`). They are policy enforced by Python, not judgment delegated to an LLM. A gate produces a verdict (`pass` / `warn` / `fail`) and may additionally flag that a **human checkpoint** is required.

Two distinct concepts:
- **Gate** = automatic pre/post-condition check. Blocks the line on `fail`.
- **Checkpoint** = mandatory human sign-off (async, see [REVIEW_QUEUE.md](./REVIEW_QUEUE.md)). Only three exist: after spec, after architecture, at release.

A gate passing is necessary but not sufficient to cross a checkpoint — the operator still signs off.

---

## gate-1 — Spec gate `✅ built`

**Runs:** after `spec-agent`, before architecture. **Code:** `domain/gates.gate_after_spec()`, wired by `pipeline/nodes/gates.node_gate_1`.

Passes only if: verdict is `pass`; story has a title; ≥2 acceptance criteria; ≥1 task; ≤ `MAX_TASKS_PER_STORY` (6) tasks; and the task graph is executable — no duplicate ids, no dependency on an unknown task, no cycle (`domain/task_order.dependency_problems`; `order_tasks` stays forgiving so a run never crashes, which is exactly why a cycle used to be built in declared order, a task before its own dependency). These are STRUCTURAL checks — a story failing any of them is rejected, because there is nothing coherent for the operator to sign off on.

**Human-needs detection — two independent triggers**, combined into ONE park so the operator answers everything in a single interruption:

1. **The agent asked.** Open questions (`spec.questions`) do not fail the gate. A structurally sound story whose author needs a decision is the Stage-1 clarifying-question round-trip working. Note `verdict: "fail"` **with** questions is the agent honouring its documented ambiguity contract (`spec-agent.md`) and parks; `verdict != "pass"` with *nothing to ask* is a genuine rejection. Checking the verdict first is how a live run threw away four correct product questions and reported "Spec verdict is 'fail'".

2. **A deterministic threshold check** (`domain/ambiguity.unbound_criteria`). An acceptance criterion containing a term that only means something once a number is attached — `overdue`, `stale`, `recent`, `slow`, `active`, `frequently`, … — and carrying no number, parks the story. This exists because trigger 1 proved to be a **coin flip**: the same vague request produced four precise questions on one live run and `verdict: pass` on the next, after which the *architect* silently chose "at least 24 hours" — an undocumented business rule nobody approved. Policy is deterministic Python, so this belongs in the gate, not in the model's mood.

   The vocabulary is deliberately **narrow**. Quality adjectives (`appropriate`, `reasonable`, `proper`) are excluded: they are review-policy concerns for [REVIEW.md](../../agents/policies/REVIEW.md) and the tester, and the SupportFlow challenge uses "appropriate" in eight of fifteen stories — flagging those would park nearly every story and train the operator to click through.

   A term already **settled by a human** is not re-asked — the factory does not re-litigate its own decisions (EFFECTIVENESS §7). `pipeline/nodes/gates.settled_threshold_terms` gathers only operator-authored sources — the project spec, `PROJECT_RULES.md`, the operator's own answers at this run's checkpoints, and a prior ADR whose `Status:` records an approval — and hands them to `domain/ambiguity.defined_threshold_terms()`. It deliberately does NOT read the whole memory block the agents receive: ADRs are architect-authored and stamped `proposed`, and reading them let one run's invented "24 hours" pass the next run's gate, which then chose 48. This story's own ADR never settles its own question.

**Feeds → Checkpoint 1 (spec sign-off) `✅ built`.** Resume: approve → architect (the story is accepted as written); reject → re-specify with the operator's answers carried in as `prior_findings` (`pipeline/graph.py`: `resume_entry_for`, `compile_spec_resume_pipeline`).

---

## gate-2 — Architecture gate `✅ built`

**Runs:** after `architect-agent`, before coding. **Code:** `domain/gates.gate_after_architect()`, wired by `pipeline/nodes/gates.node_gate_2`.

Passes only if: verdict is not `fail`; architecture notes present; ≥1 affected module; ≤ `MAX_MODULES_PER_STORY` (16) modules.

**Human-needs detection (already implemented):** even on pass, flags `needs_human` when the architect reports `breaking_changes`, `external_dependencies`, or `sensitivity` tags (legal/compliance/security/financial/pii).

**Feeds → Checkpoint 2 (architecture sign-off).**

---

## Boundary review (Gate 4, decided inside gate-2) `✅ built`

**Runs:** between `architect-agent` and gate-2, **only** when the design declares a boundary impact — an API change, a database change or migration, breaking changes, or a sensitive area (`domain/gates.boundary_review_reasons`). A design with none never pays for it. **Code:** `pipeline/nodes/boundary.py`, routed by `graph.route_after_architect` / `route_after_boundary`; the verdict logic is in `domain/gates.py`.

The boundary-agent returns four sub-verdicts — tenant, authorization, API contract, security — and the review's verdict is **computed** (`boundary_overall`): any `fail` fails it, any `warn` warns, regardless of the agent's own `overall`.

- **fail** → the design goes back to the architect with the findings and required changes (`MAX_BOUNDARY_REDESIGNS` = 1); failing again, **gate-2 rejects** the design. No code is written.
- **warn** → gate-2 parks at **Checkpoint 2** with the findings.
- **breaking changes** found by the review join the architect's in the Checkpoint 2 question.
- **unavailable** (the agent failed or went off-script on a live run) → Checkpoint 2 asks whether to implement without a review. A replay of a run recorded before the agent existed skips it (logged `skipped`), as it originally ran.
- When the architect's `sensitivity` flag parks the run, the question shows the boundary review's sub-verdicts beside it. The park itself stays: whether a passed review may answer it is an autonomy policy for the operator.

---

## gate-build — Build verification `✅ built`

**Runs:** after `coder-agent`, post-materialization. **Code:** `src/factory/verification/` → `verify_changes()`, called from `pipeline/nodes/coder.py`.

Infers toolchain from materialized file extensions and runs deterministic checks:
- **Python:** `py_compile` and a **static import check** (`python.static_import_check`: a local module or name the coder imports must exist — found by parsing, not importing). `pytest --collect-only` and the suite run only when opted in (`FACTORY_RUN_TESTS=1`): collection imports `conftest.py` and test modules, i.e. it EXECUTES generated code, so by default nothing the coder wrote is run.
- **Go:** `go build ./...`, then `go vet ./...`, then `go test ./...` (opted in), run from
  each module root found by walking up from the changed `.go` files — a monorepo's module is
  `backend/go.mod`, not the repo root. An unresolvable import UNDER the module's own path
  fails; an unreachable third-party module warns (same rule as Python's missing-dep policy).
- **JS:** `node --check`. **TS:** `tsc --noEmit -p <nearest tsconfig.json>`, preferring the
  project's own pinned `node_modules/.bin/tsc`. No tsconfig anywhere ⇒ **warn**, not fail.

**Verdict policy:** **fail** on real syntax/compile/import errors — and when the **toolchain itself is not installed** (`go`, `gofmt`, `node`, `tsc`): a check that cannot run cannot earn a pass, and it used to `skip` its files through. **warn** when a third-party dependency is missing (not proof the code is broken). Running test **bodies** is opt-in until a sandbox exists.

Tests are run after **any** Python change once `FACTORY_RUN_TESTS=1` — not only when the task happened to write a test file, which used to leave a source-only change never exercising the project's existing suite.

A `complete` coder verdict does **not** win if `gate-build` fails. Also computes a **scope-mismatch** note by comparing the real git diff against the agent's claimed files, and **blocks** on any file that landed in the repo but was not declared in `code_blocks` (an out-of-band write). The factory's own evidence inside a project repo (`workspace.layout.EVIDENCE_PATHS`: `docs/work/`, `docs/architecture/adr/`, `docs/releases/`, `PROJECT_RULES.md`, `project-spec.json`) is committed by the factory as it is produced and excluded from this check — and a code block that targets one of those paths is refused outright, since gate-1 reads `PROJECT_RULES.md` as operator-authored.

Declared-scope violations (a changed file outside the tasks' `scope`) are measured by `verification.scope.paths_outside_scope` but reported in the trust package rather than blocking here — see EFFECTIVENESS.md §6 for why that is deliberate. Test files and toolchain manifests (`go.mod`, `package.json`, `tsconfig.json`, `pyproject.toml`, …) are exempt: the coder is *required* to add tests, and a build manifest must sit where the toolchain looks for it, so flagging either trains the operator to ignore the signal.

---

## gate-test — Quality / security gate `✅ built`

**Runs:** after `tester-agent`. **Code:** `domain/gates.gate_after_tester()`, wired by `pipeline/nodes/gates.node_gate_test`.

Fails if: the tester's overall verdict is `fail`; `qa_verdict` is `fail` (an acceptance criterion has no test); `highest_severity` is `high` or `critical`, or `security_verdict` is `fail`; `performance_verdict` is `fail`.

The tester reviews the **real git diff of this run** — from the run's own `base_commit`, so a later story's review is not filled with earlier stories' code (`workspace.git.collect_repo_diff(base=…)`) — not the last task's self-report, and applies the versioned policy in [REVIEW.md](../../agents/policies/REVIEW.md) — passes, severity ladder, skip list and nit cap live there, so review behaviour is tunable without editing an agent definition. The severity ladder in that doc must stay in step with this gate's thresholds; `factory evals` checks the policy exists and covers every sub-verdict.

Acceptance criteria the tester never mentioned are surfaced deterministically (`domain/traceability.unassessed_criteria`) and annotated on the gate reason — a silent drop is not neutral.

On failure the run routes back to the coder for ONE bounded remediation pass carrying the findings (`MAX_TESTER_REMEDIATIONS`), then parks.

---

## gate-release — Release readiness gate `✅ built`

**Runs:** after `gate-test` passes and the `release-agent` has written the release notes. **Code:** `domain/gates.gate_after_release()`, wired by `pipeline/nodes/gates.node_gate_release`.

**It always parks for Checkpoint 3 — it ASKS, it never allows.** Its verdict is whether the evidence bar is met (`passed` = READY), never whether to release. The gaps it names:

- every blocker of the **trust package** (`evidence/trust_package.assemble`): tests never executed / failed — judged on the FINAL candidate, newest result per toolchain, so a failure a retry fixed no longer sinks it — a change set not measured from git, files changed outside the declared scope, acceptance criteria the tester never assessed, no ADR for the story;
- a package that does not match its schema (`trust_package.schema_errors`) or **could not be saved** to `docs/releases/` — unsaved evidence is not evidence;
- **no usable release notes** (the release-agent failed, went off-script, or the run predates it);
- **migration notes missing** when the design changes the database (`db_impact` / `migration_needed`), **rollback notes missing** when it changes the schema or breaks an API;
- a **blocking concern** raised by the release-agent (`verdict: fail`). Its `warn` concerns are shown but do not count as gaps.

The trust package is written to `docs/releases/` *at* the checkpoint, so the operator reads it before deciding. The gate first **pins the candidate** (`pipeline_runs.candidate_commit`): the package names it and measures its change set up to it, so re-assembling it after later work cannot change its meaning, and the boss will release exactly that code — if any code changed after the checkpoint (another story, a manual edit; the factory's own evidence commits do not count), the approval is refused. After release the package says `next_authorization: none`. The release-agent writes words only; it cannot pass this gate, and nothing else can either: `release` runs behind the boss, which requires the operator's APPROVAL on the newest gate-release row (`domain/authorization.authorize_release`). Approving a NOT READY release is permitted — the gaps were named — and is recorded as accepted risk.

**Feeds → Checkpoint 3 (release sign-off) `✅ built`.** Resume: approve → `release` (story completed, final trust package); reject → a remediation coder pass carrying the operator's words, then the tester and this gate again.

---

## Authorization — the boss `✅ built`

**Runs:** before every agent stage except the spec-agent (whose input is the operator's request). **Code:** the rules in `domain/authorization.py` (pure), applied by `pipeline/boss.authorized`, which wraps each agent node in `pipeline/graph.py`.

A gate judges a stage's **output** after it ran; authorization judges its **inputs** before a token is spent. It decides from the *recorded* verdicts — newest row per gate, plus the operator's answer if it parked — because a resumed run rebuilds its state from agent logs and carries no gate dicts.

| Stage | Authorized only if |
|---|---|
| `architect-agent` | a usable story with acceptance criteria and tasks; gate-1 passed, and if it parked, the operator approved (not rejected) |
| `boundary-agent` | an architecture that declares a boundary impact (otherwise there is nothing to review) |
| `coder-agent` (per task) | an architecture with notes and affected modules; gate-2 passed / approved at Checkpoint 2; the task has a purpose; every task it depends on is already built |
| `coder-agent` (remediation) | the newest gate-test FAILED, or the operator REJECTED the release at Checkpoint 3 — and there are findings to resolve |
| `tester-agent` | every task of the story is built and the newest gate-build passed |
| `release-agent` | the newest gate-test passed |
| `release` (not an agent) | the newest gate-release carries the operator's APPROVAL — no agent can release |

A task with no allowed scope or no completion evidence is authorized with a **warning** (recorded, not blocking). A refusal **blocks** the run — `finish_run(..., "blocked")`, never left `running` — and the error names every missing prerequisite. Every decision, allowed or refused, is stored in the `authorizations` table and shown on the run timeline (`factory review`) and in the story's committed `docs/work/<story>/PIPELINE.md`.

---

## Cross-gate policy

- **Auto-remediation:** on `fail` between checkpoints, route back (implementation → coder, design → architect) carrying prior findings, up to **2 attempts per task**, then stop and queue; and no model call once the story has spent **$10** (`MAX_STORY_COST_USD`). `✅ built`
- **Budget source:** per-call usage harvested from opencode's `step_finish` events (rows before 2026-10-02 are NULL), priced by `domain/budget.py`: the provider's cost when it reports one, else tokens × the list price in `agents/tiers.toml` [prices] (a subscription login reports $0). Summed over every live run of the story; replays spend nothing. `factory doctor` warns about a tier model with no price.
- **Thresholds** (`MAX_TASKS_PER_STORY`, `MAX_MODULES_PER_STORY`, attempt/cost caps) live as named constants in `src/factory/domain/gates.py`, not scattered in node logic.
- **Every gate result is persisted** to `gate_results` (`state.db.log_gate`) (with `needs_human`, `human_questions`, `human_response`) and surfaced in `factory review`; every boss authorization to `authorizations` (`state.db.log_authorization`).
