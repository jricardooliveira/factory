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

Passes only if: verdict is not `fail`; architecture notes present; ≥1 affected module; ≤ `MAX_MODULES_PER_STORY` (12) modules.

**Human-needs detection (already implemented):** even on pass, flags `needs_human` when the architect reports `breaking_changes`, `external_dependencies`, or `sensitivity` tags (legal/compliance/security/financial/pii).

**Feeds → Checkpoint 2 (architecture sign-off).**

---

## gate-build — Build verification `✅ built`

**Runs:** after `coder-agent`, post-materialization. **Code:** `src/factory/verification/` → `verify_changes()`, called from `pipeline/nodes/coder.py`.

Infers toolchain from materialized file extensions and runs deterministic checks:
- **Python:** `py_compile`; `pytest --collect-only` (or the full suite when opted in).
- **Go:** `go build ./...`, then `go vet ./...`, then `go test ./...` (opted in), run from
  each module root found by walking up from the changed `.go` files — a monorepo's module is
  `backend/go.mod`, not the repo root. An unresolvable import UNDER the module's own path
  fails; an unreachable third-party module warns (same rule as Python's missing-dep policy).
- **JS:** `node --check`. **TS:** `tsc --noEmit -p <nearest tsconfig.json>`, preferring the
  project's own pinned `node_modules/.bin/tsc`. No tsconfig anywhere ⇒ **warn**, not fail.

**Verdict policy:** hard **fail** only on real syntax/compile errors. **warn** (never block) when a toolchain/dependency is missing or pytest collection fails (collection imports project deps that may be absent — not proof the code is broken). Running test **bodies** is deferred until a sandbox exists.

Tests are run after **any** Python change once `FACTORY_RUN_TESTS=1` — not only when the task happened to write a test file, which used to leave a source-only change never exercising the project's existing suite.

A `complete` coder verdict does **not** win if `gate-build` fails. Also computes a **scope-mismatch** note by comparing the real git diff against the agent's claimed files, and **blocks** on any file that landed in the repo but was not declared in `code_blocks` (an out-of-band write). The factory's own evidence inside a project repo (`workspace.layout.EVIDENCE_PATHS`: `docs/work/`, `docs/architecture/adr/`, `docs/releases/`, `PROJECT_RULES.md`, `project-spec.json`) is committed by the factory as it is produced and excluded from this check — and a code block that targets one of those paths is refused outright, since gate-1 reads `PROJECT_RULES.md` as operator-authored.

Declared-scope violations (a changed file outside the tasks' `scope`) are measured by `verification.scope.paths_outside_scope` but reported in the trust package rather than blocking here — see EFFECTIVENESS.md §6 for why that is deliberate. Test files and toolchain manifests (`go.mod`, `package.json`, `tsconfig.json`, `pyproject.toml`, …) are exempt: the coder is *required* to add tests, and a build manifest must sit where the toolchain looks for it, so flagging either trains the operator to ignore the signal.

---

## gate-test — Quality / security gate `✅ built`

**Runs:** after `tester-agent`. **Code:** `domain/gates.gate_after_tester()`, wired by `pipeline/nodes/gates.node_gate_test`.

Fails if: the tester's overall verdict is `fail`; `qa_verdict` is `fail` (an acceptance criterion has no test); `highest_severity` is `high` or `critical`, or `security_verdict` is `fail`; `performance_verdict` is `fail`.

The tester reviews the **real cumulative git diff** of every task (`workspace.git.collect_repo_diff`), not the last task's self-report, and applies the versioned policy in [REVIEW.md](../../agents/policies/REVIEW.md) — passes, severity ladder, skip list and nit cap live there, so review behaviour is tunable without editing an agent definition. The severity ladder in that doc must stay in step with this gate's thresholds; `factory evals` checks the policy exists and covers every sub-verdict.

Acceptance criteria the tester never mentioned are surfaced deterministically (`domain/traceability.unassessed_criteria`) and annotated on the gate reason — a silent drop is not neutral.

On failure the run routes back to the coder for ONE bounded remediation pass carrying the findings (`MAX_TESTER_REMEDIATIONS`), then parks.

---

## gate-release — Release readiness gate `⛔ to build`

**Runs:** before Checkpoint 3. **Requires:** release-agent.

Passes only if all four **trust-package** artifacts are present and green (tests+AC, real diff, ADR, security/boundary verdict — see EFFECTIVENESS §5).

The package itself is built (`evidence/trust_package.assemble`) and now refuses to overstate itself: `tests.passed` requires an executed suite, `diff` is git-measured against the run's `base_commit` or reported as `"unavailable"`, and every unmet bar is named in `blockers` with `next_authorization: "operator-review"`. `evidence/trust_package.validate()` enforces the schema's constraints (not just required keys) so a non-git-measured package cannot pass.

**What remains:** a `gate_after_release()` that turns those blockers into a gate verdict with `needs_human=True`, and the checkpoint-3 resume dispatch. The gate should ASK, never allow — an agent must not pass the release gate.

**Feeds → Checkpoint 3 (release sign-off).**

---

## Authorization — the boss `✅ built`

**Runs:** before every agent stage except the spec-agent (whose input is the operator's request). **Code:** the rules in `domain/authorization.py` (pure), applied by `pipeline/boss.authorized`, which wraps each agent node in `pipeline/graph.py`.

A gate judges a stage's **output** after it ran; authorization judges its **inputs** before a token is spent. It decides from the *recorded* verdicts — newest row per gate, plus the operator's answer if it parked — because a resumed run rebuilds its state from agent logs and carries no gate dicts.

| Stage | Authorized only if |
|---|---|
| `architect-agent` | a usable story with acceptance criteria and tasks; gate-1 passed, and if it parked, the operator approved (not rejected) |
| `coder-agent` (per task) | an architecture with notes and affected modules; gate-2 passed / approved at Checkpoint 2; the task has a purpose; every task it depends on is already built |
| `coder-agent` (remediation) | the newest gate-test FAILED and there are findings to resolve |
| `tester-agent` | every task of the story is built and the newest gate-build passed |

A task with no allowed scope or no completion evidence is authorized with a **warning** (recorded, not blocking). A refusal **blocks** the run — `finish_run(..., "blocked")`, never left `running` — and the error names every missing prerequisite. Every decision, allowed or refused, is stored in the `authorizations` table and shown on the run timeline (`factory review`) and in the story's committed `docs/work/<story>/PIPELINE.md`.

---

## Cross-gate policy

- **Auto-remediation:** on `fail` between checkpoints, route back (implementation → coder, design → architect) carrying prior findings, up to **2 attempts or ~$1/task**, then stop and queue. `✅ built`
- **Budget source:** `agent_logs.cost_usd` is summed per task to enforce the $ cap — **but that column is NULL in every row ever written**, so only the attempt cap actually binds. See EFFECTIVENESS.md §4; `factory metrics` reports it as NOT MEASURABLE.
- **Thresholds** (`MAX_TASKS_PER_STORY`, `MAX_MODULES_PER_STORY`, attempt/cost caps) live as named constants in `src/factory/domain/gates.py`, not scattered in node logic.
- **Every gate result is persisted** to `gate_results` (`state.db.log_gate`) (with `needs_human`, `human_questions`, `human_response`) and surfaced in `factory review`; every boss authorization to `authorizations` (`state.db.log_authorization`).
