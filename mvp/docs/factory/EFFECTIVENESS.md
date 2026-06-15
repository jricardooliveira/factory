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

Cost is tracked per agent run (`agent_logs.cost_usd`), so the $ cap is enforceable today. Attempt counting and routing are the Phase-3 remediation loop.

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

Scope is enforced after the fact: the real git diff (§5.2) is checked against the pack's allowed scope, and out-of-scope changes are flagged. Template: [`context-pack.template.md`](./context-pack.template.md).

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
| Checkpoints 1 (spec) & 3 (release) sign-off | ⛔ to build |
| tester-agent + gate-test (QA/AC-coverage, security, performance sub-verdicts) | ✅ built |
| Run generated test bodies in the build gate (opt-in `FACTORY_RUN_TESTS`) | ✅ built (subprocess; hardened sandbox still ⛔) |
| Per-agent model tiers — frontier for thinking (spec/architect/tester), cheap for coder, escalate-on-retry (`factory tiers`, `model_tiers.py`) | ✅ built |
| Tester reviews the **real cumulative git diff** of every task (not the last task's self-report) | ✅ built (`verify.collect_repo_diff`) |
| Tester failure routes back to the coder for a bounded remediation pass (`MAX_TESTER_REMEDIATIONS`), findings carried, coder escalated to frontier | ✅ built |
| Malformed-JSON agent output gets one repair retry before `blocked` (live runs) | ✅ built |
| Build gate FAILS on a real import error (vs WARN for a merely-absent third-party dep) | ✅ built (`verify._classify_collect_failure`) |
| Brownfield awareness: architect + coder receive a real interface map of the existing repo | ✅ built (`repo_map.build_repo_inventory`) |
| Coder → architect feedback: an infeasible design routes back for a bounded re-design (`MAX_REARCHITECT_LOOPS`) | ✅ built |
| AC traceability: spec's real acceptance criteria cross-checked (deterministically) against the tester's claims; `unassessed` criteria surfaced in the trust package + gate-test reason (`traceability.py`) | ✅ built (evidence; hard-enforce at release checkpoint ⛔) |

The build order that turns this contract into reality is the phased workflow in `docs/superpowers/plans/2026-06-02-factory-improvements-workflow.md`.

---

## 9. Open questions (deliberately deferred)

- **Brownfield stacks:** `verify.py` covers Python / JS / TS. Java, Go, etc. need their own verification commands before the factory can be trusted on those repos.
- **Sandbox:** running agent-generated *test bodies* executes untrusted code; required before `gate-test` runs tests for real (today `gate-build` is compile/collect-only).
- **Notification mechanism:** the macOS desktop-ping implementation (e.g. `osascript`/`terminal-notifier`) is unspecified.

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
