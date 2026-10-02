# Factory Effectiveness Playbook

**Date:** 2026-06-02 · **Status:** governing contract for the MVP

This is the north-star document. Every design decision below was made deliberately, not defaulted. When the code and this doc disagree, one of them is wrong — fix the mismatch, don't ignore it.

---

## 1. What this factory is for

A **solo delivery engine**: one operator ships their own projects through an AI-agent SDLC, mostly unattended, on both greenfield and existing ("brownfield") codebases.

When goals conflict, the factory protects **trustworthy output** over autonomy, throughput, or learning. It is allowed to be slower and more human-gated if that buys correctness, safety, and traceability.

**Effectiveness metric:** *trust per interruption.* The factory is effective when it assembles a package the operator can trust **without reading all the code**, and only interrupts them when judgment is genuinely required.

---

## 2. The operating model: a checkpointed assembly line

Each stage runs autonomously, but the line **parks at three human checkpoints**. Build and test run unattended between the architecture and release checkpoints.

```
request
  → spec-agent
  → [gate-1]
  → ⏸ CHECKPOINT 1: spec sign-off          ── "is this the right work?"
  → architect-agent
  → [gate-2]
  → ⏸ CHECKPOINT 2: architecture sign-off   ── "is this the right design?"
  → coder-agent
  → [gate-build]
  → tester-agent / boundary checks          ── (autonomous)
  → [gate-test, gate-release]
  → ⏸ CHECKPOINT 3: release sign-off         ── "ship it?"  (full trust package)
  → done
```

The checkpoints map to the original spec's stage boundaries; the *autonomy between them* is what makes it a solo engine rather than a babysitting exercise.

---

## 3. Interaction model: async + notify

The factory **never blocks waiting for the operator in real time.** When a checkpoint is reached or a failure escalates:

1. The task is **parked in a review queue** with its current package (see §5).
2. A **desktop notification** fires so the operator doesn't have to poll.
3. The operator reviews on their own schedule (batching is fine) and **approves** or **rejects** via the CLI.

Rejection is not a dead end: it re-enters the pipeline as a new attempt carrying the operator's feedback (see §6).

See [REVIEW_QUEUE.md](./REVIEW_QUEUE.md) for the queue + notification contract.

---

## 4. Failure handling: fail-fast auto-remediation

Between checkpoints, when a gate fails, the factory **auto-remediates within a tight budget**:

- **Budget: 2 attempts OR ~$1 (USD) per task**, whichever comes first.
- Remediation routes back to the stage that can fix it (implementation failure → coder; design failure → architect), carrying the prior findings.
- On budget exhaustion, the factory **stops and queues the failure** for the operator — it does not loop indefinitely or silently burn money.

Attempt counting and routing are built (the Phase-3 remediation loop).

> **⚠️ The $ cap is NOT enforced today.** `agent_logs.cost_usd` is NULL in every row
> ever written — opencode's usage events are not being harvested by
> `opencode_client._extract_usage_from_json_stream` — so `get_run_cost()` always
> returns `0.0` and the `cost_so_far < MAX_TASK_COST_USD` comparison is always true.
> **Only `MAX_CODER_ATTEMPTS` is actually bounding the loop.** `factory metrics`
> reports this under *NOT MEASURABLE* rather than printing a reassuring `$0.00`.
> Fixing the harvest requires inspecting a live opencode event stream.

---

## 5. The trust package (release sign-off bar)

At the release checkpoint, the operator must be able to trust the work **without reading the diff line by line.** All four artifacts are **non-negotiable**:

1. **Passing tests + acceptance-criteria coverage** — tests actually run and pass, and *each* acceptance criterion maps to a test that exercises it.
2. **Real git diff** — the actual change set measured from `git` in the project repo, NOT the agent's self-reported file list.
3. **ADR / design rationale** — a short record of the design decisions and why, written to `docs/architecture/adr/`.
4. **Security / boundary verdict** — explicit findings on tenant isolation, authorization, secrets-not-logged, and any breaking API changes.

A release sign-off is offered only when all four are present and green. Anything missing keeps the task in the queue with the gap named. The machine-readable shape is [`trust-package.schema.json`](./trust-package.schema.json).

---

## 6. Brownfield context: task-scoped context packs

Because the factory works on existing repos and protects trust, agents must **not** receive the whole repo (expensive, scope-leaky). The orchestrator assembles a **minimal, task-scoped context pack** per agent containing only: the task, relevant acceptance criteria, the files in scope, applicable prior ADRs + `PROJECT_RULES.md`, and explicit allowed/forbidden scope.

Scope is checked after the fact: the real git diff (§5.2) is compared against the
union of the tasks' declared `scope` (`verify.paths_outside_scope`), and violations
are reported as `diff.scope_violations` in the trust package **and** named as a
blocker, so a release sign-off is not offered while any exists. Test files never
count as a violation — the coder is required to add tests.

**This is evidence, not yet a block:** a scope breach does not fail `gate-build` or
trigger a retry. Hard-blocking is a policy decision for the operator (a coder
legitimately touching an adjacent file would dead-end the run), so it is
deliberately left as a checkpoint-3 concern. The declared scope is committed in
`PLAN.md` → *Files that change* (`artifacts.planned_scope`), so the plan and the
check read the same list. Template: [`context-pack.template.md`](./context-pack.template.md).

---

## 7. Memory: agents consult prior decisions

ADRs are required evidence anyway (§5.3) — so they are also **fed back in**. On every new task, architect and coder **read prior ADRs and `PROJECT_RULES.md`** for the project, so the factory stays internally consistent and stops re-litigating settled decisions. (Learning anti-patterns from failed runs is a later enhancement, explicitly out of scope for now.)

---

## 8. What's built vs. what this contract still requires

| Capability | Status |
|---|---|
| spec → gate-1 → architect → gate-2 → coder pipeline | ✅ built |
| `gate-build` (compile/parse verification) | ✅ built |
| Real git diff baseline per project repo | ✅ built |
| Cost/token/prompt provenance per run | ✅ built |
| Offline replay + fixtures (test orchestration cheaply) | ✅ built |
| Checkpoint 2 human-needs detection (breaking/sensitivity) | ✅ built (gate-2) |
| Checkpoint 2 resume: approve→coder, reject→re-architect w/ feedback | ✅ built |
| Remediation loop (2-attempt / $1 budget, re-entry) | ✅ built |
| ADR persistence + consult-on-next-task | ✅ built |
| Async review queue (`factory queue`) + desktop notify | ✅ built |
| Per-task coder execution (one task per call, dependency-ordered) | ✅ built |
| Context-pack assembly (per-task scoped packs) | ✅ built |
| Task dependencies (`depends_on` + topological order) | ✅ built |
| Governance: agents can't write files (tools disabled) + out-of-band write detection blocks the gate | ✅ built |
| Trust-package assembly + schema validation + surfacing (`factory review`, saved to docs/releases) | ✅ built |
| Checkpoint 1 (spec sign-off): open questions PARK the run for the operator instead of failing it; approve → architect, reject → re-specify with the answers (`gate_after_spec` needs_human, `resume_entry_for`, `compile_spec_resume_pipeline`) | ✅ built |
| Checkpoint 3 (release sign-off) + `gate-release` | ⛔ to build |
| **Deterministic ambiguity detection**: an acceptance criterion needing a threshold it was never given parks at Checkpoint 1 (`gates.unbound_criteria`), with terms already settled in committed project memory exempted. Built because agent-reported ambiguity proved non-deterministic across two identical live runs | ✅ built |
| **Go verification** in `gate-build`: `go build` → `go vet` → `go test` per module, with `gofmt -e` as a module-less parse fallback so Go can never pass unverified | ✅ built |
| **Continuous evals of the agent configuration** (`factory evals`): tools-disabled governance invariant, `model_tier` ↔ registry, each agent's JSON contract diffed against its Pydantic model, review-policy coverage, plus a behavioural corpus driven through the real pipeline. Gated at 100% by `make evals`; an empty suite never passes. `factory evals capture <run_id>` freezes any real run (or incident) as a permanent case | ✅ built |
| **Committed artifact chain** INTENT → SPEC → PLAN (→ ADR → diff), written to `docs/work/<story>/` (`artifacts.py`) — the requirements are no longer trapped in a gitignored DB | ✅ built |
| **Trust package tells the truth**: `tests.passed` requires a test body to have actually run (`pytest_run:pass`), not merely a green gate; `diff` is measured from git against the run's own `base_commit` and is `"unavailable"` (and fails `validate()`) when it cannot be; every unmet evidence bar is named in `blockers` and release sign-off is withheld | ✅ built |
| **Versioned review policy** (`agents/policies/REVIEW.md`) injected into the tester prompt — passes, severity ladder, skip list and nit cap in one committed file instead of split between agent prose and `gates.py` | ✅ built |
| **`factory metrics`**: the playbook's indicators over the factory's own history, with an explicit NOT MEASURABLE section | ✅ built |
| Single verification command (`make check` = tests + scenarios + evals) | ✅ built |
| Repo `CLAUDE.md` + deterministic Claude Code hooks (protected paths, credential paths, post-edit compile check, agent-config change reminder) | ✅ built |
| Materialization is all-or-nothing and refuses credential-shaped paths (`.env*`, `.git/`, key material) at the single write chokepoint | ✅ built |
| A failed gate-1 / architect PERSISTS the failure (`finish_run`) instead of leaving the run `running` until `reconcile` mislabels it a dead process | ✅ built |
| Resume rebuilds state from the LATEST agent output, so the operator cannot approve one design while the coder builds an earlier one (`build_resume_context`) | ✅ built |
| The project's existing test suite runs after ANY Python change, not only when the task happened to write a test file | ✅ built |
| Cost/token harvest from opencode (`agent_logs.cost_usd` is NULL in 100% of rows — see §4) | ⛔ to build |
| Canonical DB path (four `factory.db` files exist; the path is CWD-relative) | ⛔ to build |
| Branch per story + PR-shaped review (the factory commits straight onto the current branch) | ⛔ to build |
| tester-agent + gate-test (QA/AC-coverage, security, performance sub-verdicts) | ✅ built |
| Run generated test bodies in the build gate (opt-in `FACTORY_RUN_TESTS`) | ✅ built (subprocess; hardened sandbox still ⛔) |
| Per-agent model tiers — frontier for thinking (spec/architect/tester), cheap for coder, escalate-on-retry (`factory tiers`, policy in `agents/tiers.toml`; `factory doctor` probes every tier model before a run) | ✅ built |
| Tester reviews the **real cumulative git diff** of every task (not the last task's self-report) | ✅ built (`verify.collect_repo_diff`) |
| Tester failure routes back to the coder for a bounded remediation pass (`MAX_TESTER_REMEDIATIONS`), findings carried, coder escalated to frontier | ✅ built |
| Malformed-JSON agent output gets one repair retry before `blocked` (live runs) | ✅ built |
| Build gate FAILS on a real import error (vs WARN for a merely-absent third-party dep) | ✅ built (`verify._classify_collect_failure`) |
| Brownfield awareness: architect + coder receive a real interface map of the existing repo | ✅ built (`repo_map.build_repo_inventory`) |
| Coder → architect feedback: an infeasible design routes back for a bounded re-design (`MAX_REARCHITECT_LOOPS`) | ✅ built |
| AC traceability: spec's real acceptance criteria cross-checked (deterministically) against the tester's claims; `unassessed` criteria surfaced in the trust package + gate-test reason (`traceability.py`) | ✅ built (evidence; hard-enforce at release checkpoint ⛔) |

The build order that turns this contract into reality is the phased workflow in `docs/design/plans/2026-06-02-factory-improvements-workflow.md`.

---

## 9. Open questions (deliberately deferred)

- **Brownfield stacks:** `verify.py` covers Python, **Go** (build + vet + test) and JS/TS. Java, Rust, C# etc. still need their own verification commands before the factory can be trusted on those repos — until then `gate-build` reports "no verifiable files" and passes, which is a silent false PASS.
- **Sandbox:** running agent-generated *test bodies* executes untrusted code; required before `gate-test` runs tests for real (today `gate-build` is compile/collect-only).
- **Notification mechanism:** the macOS desktop-ping implementation (`osascript`) is
  implemented but opt-in via `FACTORY_NOTIFY=1`.

### Deliberately rejected (decisions, not omissions)

The AI-Native SDLC playbook's Stage-6 apparatus is designed for regulated
enterprises with platform teams. For a solo factory it is ceremony, and adopting it
would add places for policy to drift without adding trust:

- **Control bands / `bands.yaml` / Western-Electric sigma tiers.** A sigma computed
  over a handful of completed runs is noise dressed as a control chart. The honest
  substitute is fixed thresholds the operator wrote down — see the roadmap.
- **Claude on call in an incident channel.** For a one-person team this pages the
  agent to tell its own author about the author's laptop.
- **OpenTelemetry export.** Not "too small for a tracing backend" — the per-call
  record is *silently empty* (§4). An exporter would faithfully export nulls. Fix
  the harvest first; `factory metrics` is the right scope until "I need to see this
  without shelling into the box" is actually true.
- **MCP deploy/status/rollback tools, environment tiers, managed settings.** Nothing
  deploys: no push, no branch, no PR, no deploy path. An environment ladder would be
  three names for one laptop, and a "managed" settings file the operator can freely
  edit is theater by construction. The transferable kernel is a per-project autonomy
  level — on the roadmap.
- **Concurrent coder tasks.** The serial design is load-bearing for two
  non-negotiable controls: scope is measured from whole-repo `git status`, so two
  concurrent coders each see the other's files as undeclared and each blocks the
  run; and `git_commit_all` is `git add -A`, so the first task to finish commits the
  other's partial output under its own message, poisoning the diff the tester
  reviews. It would also make `factory replay` non-deterministic, costing the
  factory its cheapest regression harness.

**The half of Stage 6 that DOES transfer — "every incident becomes a permanent
eval" — is built**: `factory evals capture <run_id>` + `evals/cases/` + a 100%
gate in `make evals`.

---

## 10. Red flags — the factory is degrading if…

- A task reaches release sign-off missing any of the four trust artifacts.
- A checkpoint is skipped or auto-approved without the operator.
- Remediation loops past the 2-attempt / $1 budget.
- An agent receives the whole repo instead of a context pack.
- The real git diff diverges from the agent's claimed file list and it isn't flagged.
- A "complete" coder verdict ships code that doesn't pass `gate-build`.
- Architecture decisions are made without consulting existing ADRs.
- Code is written before a failing test exists (see the `tdd` skill).
- A run reaches a terminal state without `finish_run` being called — it sits as
  `running`, never appears in `factory queue`, and `reconcile` later mislabels a
  policy decision as a dead process.
- The trust package claims evidence it does not have (a `tests.passed` not backed by
  an executed suite; a `diff` that is a self-report labelled as measured).
- An agent definition's JSON contract drifts from its Pydantic model, so the agent
  is instructed to return a field the orchestrator silently drops.
- `make evals` is green because the suite is empty or because a case's expectation
  was edited to match new behaviour instead of the behaviour being questioned.
- A resume hands a downstream agent an artifact the operator did not approve.
- A doc in `docs/contract/` claims a capability the code does not have. Both this
  file's §8 table and `GATES.md` are contracts; if code and doc disagree, one of
  them is wrong.
- A *behavioural* guarantee rests on an agent choosing to behave. If a rule matters,
  a deterministic check must enforce it — an agent-reported signal is evidence, not
  a guarantee. (Observed: the same vague request produced four clarifying questions
  on one run and silent invention of a 24-hour rule on the next.)
- A verification check reports a verdict it did not earn — in EITHER direction. A
  false PASS ships unverified code; a false FAIL spends the remediation budget
  re-fixing code that was never broken and teaches the operator to ignore the gate.
- A hand-authored eval case encodes an assumption about an agent rather than the
  agent's real contract. Prefer `factory evals capture <run_id>` on a real run.
