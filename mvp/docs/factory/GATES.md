# Gate Catalog

Gates are **deterministic, code-based** checks between stages (`src/factory/gates.py`, `src/factory/verify.py`). They are policy enforced by Python, not judgment delegated to an LLM. A gate produces a verdict (`pass` / `warn` / `fail`) and may additionally flag that a **human checkpoint** is required.

Two distinct concepts:
- **Gate** = automatic pre/post-condition check. Blocks the line on `fail`.
- **Checkpoint** = mandatory human sign-off (async, see [REVIEW_QUEUE.md](./REVIEW_QUEUE.md)). Only three exist: after spec, after architecture, at release.

A gate passing is necessary but not sufficient to cross a checkpoint — the operator still signs off.

---

## gate-1 — Spec gate `✅ built`

**Runs:** after `spec-agent`, before architecture. **Code:** `gate_after_spec()`.

Passes only if: verdict is `pass`; story has a title; ≥2 acceptance criteria; ≥1 task; no open questions; ≤ `MAX_TASKS_PER_STORY` (6) tasks.

**Feeds → Checkpoint 1 (spec sign-off).**

---

## gate-2 — Architecture gate `✅ built`

**Runs:** after `architect-agent`, before coding. **Code:** `gate_after_architect()`.

Passes only if: verdict is not `fail`; architecture notes present; ≥1 affected module; ≤ `MAX_MODULES_PER_STORY` (12) modules.

**Human-needs detection (already implemented):** even on pass, flags `needs_human` when the architect reports `breaking_changes`, `external_dependencies`, or `sensitivity` tags (legal/compliance/security/financial/pii).

**Feeds → Checkpoint 2 (architecture sign-off).**

---

## gate-build — Build verification `✅ built`

**Runs:** after `coder-agent`, post-materialization. **Code:** `verify.py` → `verify_changes()`.

Infers toolchain from materialized file extensions and runs deterministic checks:
- Python: `py_compile`; `pytest --collect-only` when test files were written.
- JS: `node --check`. TS: `tsc --noEmit`.

**Verdict policy:** hard **fail** only on real syntax/compile errors. **warn** (never block) when a toolchain/dependency is missing or pytest collection fails (collection imports project deps that may be absent — not proof the code is broken). Running test **bodies** is deferred until a sandbox exists.

A `complete` coder verdict does **not** win if `gate-build` fails. Also computes a **scope-mismatch** note by comparing the real git diff against the agent's claimed files.

---

## gate-test — Quality / security gate `⛔ to build`

**Runs:** after `tester-agent`. **Requires:** tester-agent (Phase 5).

Will enforce: each acceptance criterion is covered by a passing test; mandatory negative cases (wrong-role / wrong-tenant where applicable); no high/critical security finding; no severe performance regression. Sub-verdicts (QA / security / performance) per the original spec. **Hard requirement: a sandbox** before executing generated test bodies.

---

## gate-release — Release readiness gate `⛔ to build`

**Runs:** before Checkpoint 3. **Requires:** release-agent.

Passes only if all four **trust-package** artifacts are present and green (tests+AC, real diff, ADR, security/boundary verdict — see EFFECTIVENESS §5). Assembles the package the operator signs off against.

**Feeds → Checkpoint 3 (release sign-off).**

---

## Cross-gate policy

- **Auto-remediation:** on `fail` between checkpoints, route back (implementation → coder, design → architect) carrying prior findings, up to **2 attempts or ~$1/task**, then stop and queue. *(Phase 3.)*
- **Budget source:** `agent_logs.cost_usd` is summed per task to enforce the $ cap.
- **Thresholds** (`MAX_TASKS_PER_STORY`, `MAX_MODULES_PER_STORY`, attempt/cost caps) live as named constants in `gates.py`, not scattered in node logic.
- **Every gate result is persisted** to `gate_results` (with `needs_human`, `human_questions`, `human_response`) and surfaced in `factory review`.
