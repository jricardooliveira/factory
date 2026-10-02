# Factory Improvements — Implementation Workflow

**Date:** 2026-06-02
**Scope:** MVP at `mvp/` — turn the spec→architect→coder happy-path demo into a loop you can test, measure, and trust.
**Sequencing principle:** Build the things that make everything else testable and observable *first*. Don't build agents 4–8 until the core loop is closeable, measurable, and verified. Ceremony last, loop first.

---

## Phase ordering at a glance

```
Phase 0  Observability + replay  ──┐  (unblocks cheap testing of everything below)
Phase 1  Close silent-failure hole │  (verify written code actually works)
Phase 2  Decision memory           │  (factory stops re-litigating itself)
Phase 3  Re-entrant loop           │  (the real architecture fix)
Phase 4  One vertical slice E2E    │  (prove the loop is worth the ceremony)
Phase 5  tester-agent → boundary   ┘  (only now, built on Phase 1 + 3)
Cross-cutting: DB consolidation, per-agent model tier, sandbox — pulled in where they first bite.
```

Each phase ends in a state you could stop at and still have shipped something useful.

---

## Phase 0 — Observability & deterministic replay

**Why first:** Agents are stochastic. Until you can (a) attribute cost/drift to a cause and (b) re-run the orchestration without burning tokens, every change below is debugged by guesswork and real money.

### Task 0.1 — Cost & provenance columns on `agent_logs`
- **Files:** `src/factory/state/db.py`, `src/factory/opencode_client.py`, `src/factory/pipeline.py`
- **Change:**
  - Add columns to `agent_logs` (use the existing `_ensure_column` migration pattern — no destructive migration needed):
    `tokens_in INTEGER`, `tokens_out INTEGER`, `cost_usd REAL`, `model_name TEXT`, `model_version TEXT`, `prompt_hash TEXT`.
  - `opencode_client.run_agent`: capture token counts and model name from the opencode JSON event stream (the `_extract_text_from_json_stream` loop already parses events — extend it to pull `usage`/`model` events instead of discarding them). Return them on `AgentResult`.
  - `pipeline.log_agent(...)` calls: compute `prompt_hash = sha256(prompt)[:16]` and persist the new fields.
- **Acceptance:** After a run, `SELECT agent, tokens_in, tokens_out, cost_usd, model_name, prompt_hash FROM agent_logs WHERE run_id=?` returns populated rows. `factory review <run_id>` shows per-stage cost and a run total.
- **Effort:** ~1–2h.

### Task 0.2 — Prompt versioning
- **Files:** `src/factory/opencode_client.py` (or a small `agent_registry.py`)
- **Change:** When invoking agent `X`, hash the contents of `.opencode/agents/X.md` and persist as `agent_prompt_hash` on the log row. This is distinct from `prompt_hash` (the rendered input) — it pins *the agent definition* used.
- **Acceptance:** Editing `coder-agent.md` and re-running produces a different `agent_prompt_hash`; old runs retain their original hash. You can answer "did behaviour change because I edited the agent or because the model drifted?"
- **Effort:** ~30m.

### Task 0.3 — Record/replay mode
- **Files:** `src/factory/opencode_client.py`, `src/factory/pipeline.py`, `src/factory/cli.py`
- **Key fact:** `agent_logs.input_text` and `output_text` are **already stored verbatim**. Replay is mostly plumbing.
- **Change:**
  - Add a `replay_run_id: int | None` to `PipelineState`.
  - In each node, if replaying, fetch the matching stored `output_text` for `(replay_run_id, agent)` and skip `run_agent` entirely — feed the stored text into `_extract_json`.
  - CLI: `factory replay <run_id>` re-runs the orchestration (gates, parsing, edges, materialization) against frozen agent outputs.
- **Acceptance:** `factory replay <run_id>` completes with zero opencode calls (zero added cost) and reproduces the same gate verdicts. Becomes the substrate for orchestration unit tests.
- **Effort:** ~2–3h.

### Task 0.4 — Orchestration tests on replay fixtures
- **Files:** `tests/fixtures/agent_outputs/*.json` (new), `tests/test_pipeline_replay.py` (new)
- **Change:** Capture a handful of real agent outputs (one clean pass, one vague-spec fail, one breaking-change → needs-human, one off-script non-JSON). Drive the pipeline from those fixtures, no LLM.
- **Acceptance:** `pytest tests/test_pipeline_replay.py` runs offline and asserts gate verdicts + final status for each fixture. This is the regression net for every later change.
- **Effort:** ~2h.

**Phase 0 done when:** you can re-run any historical pipeline for free and have offline tests over gate/edge logic.

---

## Phase 1 — Close the silent-failure hole

**Why now:** Today `verdict: complete` + files written = "success". Nothing confirms the code parses, imports, or runs. This is the single biggest correctness gap and it's pure Python — no LLM, no new agent.

### Task 1.1 — Git baseline per project repo
- **Files:** `src/factory/projects.py` (scaffolding), `src/factory/materialize.py`
- **Change:** `git init` the project's `repo/` at scaffold time. After `materialize_code_blocks`, the *real* diff is `git status --porcelain` / `git diff`, not the agent's self-reported `files_created`. Capture the actual changed-file set.
- **Acceptance:** A run records the materialization diff measured from git, and flags any mismatch between agent-claimed and actually-written files (an early scope-violation signal).
- **Effort:** ~1–2h.

### Task 1.2 — Deterministic post-materialization verification gate ("gate-build")
- **Files:** new `src/factory/verify.py`, wire into `pipeline.py` after `node_coder_agent`
- **Change:** A non-LLM verification step keyed off the project's stack (from the project spec):
  - Python: `python -m py_compile <changed files>`, then `pytest --collect-only` (and run tests if present).
  - JS/TS: `tsc --noEmit` / `node --check`.
  - Capture pass/fail + output into a new `gate_results` row (`gate-build`).
- **Acceptance:** A coder output that writes syntactically broken code yields `gate-build: failed` and the run does NOT finish `completed`. Add a replay fixture (Phase 0.4) with broken code to lock this in.
- **Effort:** ~3–4h.
- **⚠️ Pulls in the sandbox decision (cross-cutting C3):** running generated tests = executing untrusted code. Do C3 before enabling *test execution* (collect-only is safe to ship first).

**Phase 1 done when:** the pipeline can no longer report success for code that doesn't compile.

---

## Phase 2 — Decision memory

**Why now:** `architect-agent` emits `architecture_notes` as a JSON string that is **never written to disk**. The second run on a project has zero memory of the first. Cheap to fix, compounding payoff.

### Task 2.1 — Persist architecture notes as ADRs
- **Files:** `src/factory/pipeline.py` (after `node_architect_agent`), reuse the `docs/architecture/adr/` tree `projects.py` already scaffolds
- **Change:** Write each architect output to `projects/<proj>/docs/architecture/adr/ADR-<seq>-<slug>.md` (notes, modules, risks, breaking changes, sensitivity). Record the path on the run.
- **Acceptance:** After a run, an ADR file exists on disk; `factory review` links to it.
- **Effort:** ~1h.

### Task 2.2 — Feed prior ADRs + PROJECT_RULES.md into architect & coder context
- **Files:** `src/factory/pipeline.py` (prompt assembly in architect/coder nodes), `src/factory/project_spec.py`
- **Change:** Prepend existing ADRs (or a digest) and `PROJECT_RULES.md` to the architect/coder prompts. Today only `project_spec` is injected.
- **Acceptance:** A second story on the same project shows the prior ADR content in the architect's input (`agent_logs.input_text`).
- **Effort:** ~1–2h.
- **Note:** This is the seed of "context packs" without building the whole boss subsystem. Defer the formal per-task context-pack machinery — this gets 80% of the value.

**Phase 2 done when:** the factory accumulates and re-consults its own decisions across runs.

---

## Phase 3 — Re-entrant remediation loop (the real architecture fix)

**Why now:** The spec is forward-only waterfall; the pipeline edges are hardcoded forward (`pipeline.py` `add_edge`/`add_conditional_edges`). Real software is rework. Retrofitting this after 5 agents exist is the painful path — do it while there are 3.

### Task 3.1 — Add re-entry context to agent contracts
- **Files:** `src/factory/models.py`
- **Change:** Add to the relevant inputs/state: `attempt_number: int`, `triggered_by: str | None` (e.g. `"gate-build"`, `"tester-agent"`), `prior_findings: list[str]`. Carry these in `PipelineState`.
- **Acceptance:** A re-run after a failed gate passes the prior failure reason into the coder/architect prompt; `attempt_number` increments and is logged.
- **Effort:** ~2h.

### Task 3.2 — Conditional remediation edges
- **Files:** `src/factory/pipeline.py`
- **Change:** Replace the linear `coder-agent → END` with conditional routing: on `gate-build` (and later `tester-agent`) failure, route back to `coder-agent` (implementation issue) or `architect-agent` (design issue) with `prior_findings` populated. Cap attempts (e.g. `MAX_ATTEMPTS = 3`) to bound cost/loops — store the cap with the other gate thresholds in `gates.py`.
- **Acceptance:** A run that fails `gate-build` once, then would pass on retry, loops back to coder and completes — visible as multiple coder rows with rising `attempt_number`. A run that fails 3× stops with status `failed`, not an infinite loop.
- **Effort:** ~3–4h. **Hard-depends on Phase 0 replay tests** to verify loop logic without burning tokens.

### Task 3.3 — Resume-after-human flow
- **Files:** `src/factory/pipeline.py` (`build_coder_only_pipeline` already exists as a stub), `src/factory/cli.py`
- **Change:** Make `factory approve <run_id>` / `reject <run_id>` actually resume: approve → run the coder-only continuation from frozen architect state; reject → capture structured feedback as `prior_findings` and re-enter `spec-agent` or `architect-agent` as a new attempt (not just a `rejected` row).
- **Acceptance:** The `waiting_human` path (gate-2) can be resumed both ways end-to-end from the CLI.
- **Effort:** ~3h.

**Phase 3 done when:** failures route backward with context and bounded retries instead of dead-ending.

---

## Phase 4 — One vertical slice, end-to-end

**Why now:** The MVP is uniformly half-built across all agents. Pick ONE realistic task type and make it genuinely work end-to-end before broadening. This validates that the loop (Phases 0–3) is worth the ceremony.

### Task 4.1 — Choose & harden the slice
- **Suggested slice:** "Add a CRUD endpoint to an existing FastAPI project" (exercises schema reasoning, real test execution, real git commit) — or confirm a different first use case.
- **Files:** `specs/` (a realistic project spec), the agent `.md` definitions, `verify.py`
- **Change:** Run the slice through spec → architect → ADR → coder → gate-build (with real `pytest` execution) → git commit. Fix whatever breaks until it's reliable across ~5 runs.
- **Acceptance:** From a single `factory "..."` invocation, a real endpoint + passing tests land as a git commit in `repo/`, with cost recorded and an ADR written. Documented as the canonical demo.
- **Effort:** ~1 day (mostly iteration on agent prompts).

**Phase 4 done when:** there's one task type the factory does correctly, repeatably, with verification and cost visibility.

---

## Phase 5 — tester-agent, then boundary-agent

**Why last:** A tester-agent built *before* Phase 1 would be an LLM re-judging "did tests pass" — unreliable. Built *on top of* deterministic `gate-build`, it judges the things determinism can't: coverage adequacy, negative-path presence, security smells.

### Task 5.1 — tester-agent with sub-verdicts
- **Files:** new `.opencode/agents/tester-agent.md`, `models.py` (a `TesterOutput` with separate QA / security / performance verdicts per spec §5.7), `pipeline.py`, `gates.py` (`gate-6`)
- **Change:** tester-agent consumes the real diff (Phase 1.1) + `gate-build` result + acceptance criteria, emits structured sub-verdicts; gate-6 enforces "high/critical security ⇒ overall fail". Wire failures into the Phase 3 remediation loop.
- **Acceptance:** A run with missing negative tests yields `gate-6` QA fail and routes back to coder.
- **Effort:** ~1 day.

### Task 5.2 — boundary-agent (only if the slice needs auth/tenant/API contract)
- **Files:** new `.opencode/agents/boundary-agent.md`, `models.py`, `pipeline.py`, `gates.py` (`gate-4`)
- **Change:** Pre-implementation gate per spec §5.5 with tenant/auth/API/security sub-verdicts. Slot between architect and coder.
- **Acceptance:** An API-impacting story without a contract update fails `gate-4` before any code is written.
- **Effort:** ~1 day.

**Defer indefinitely:** `boss` and `god` as standalone agents. Their real responsibilities are gates (`gates.py`) + context assembly (Phase 2.2) + routing (Phase 3) — all implemented as code, not LLM agents. Build them only if multi-project/multi-team coordination becomes a real need.

---

## Cross-cutting tasks (pull in where noted)

### C1 — Consolidate state DB ownership
- **Problem:** Three `factory.db` files exist; only `mvp/factory.db` is used. The per-project ones are dead.
- **Decision needed:** one central DB (recommended for now — `god`-style cross-project queries stay trivial) vs. per-project DBs (better isolation). Pick one and delete/wire-up accordingly.
- **Do before Phase 4** so the canonical demo has unambiguous state.
- **Effort:** ~1h once decided.

### C2 — Per-agent model tier
- **Change:** Add `model_tier: critical | standard | fast` to each agent definition; map to concrete models in `opencode_client`. boundary/tester → critical; rote coder work → fast.
- **Pairs with Phase 0.1** (you'll already be capturing `model_name`).
- **Effort:** ~1–2h.

### C3 — Sandbox for executed code
- **Problem:** Running agent-generated tests (Phase 1.2, 5.1) executes untrusted code on the host.
- **Change:** Run `verify.py` test execution in a container / disposable sandbox, not the host shell. `materialize.py` path safety is already in place (`_safe_target`); this covers *execution*, which that doesn't.
- **Hard requirement before enabling test *execution*** (collect-only/compile checks can ship without it).
- **Effort:** ~half day.

---

## Recommended first sitting

1. **0.1 + 0.2** (cost/provenance + prompt versioning) — ~2h, unblocks measurement.
2. **0.3 + 0.4** (replay + fixtures) — ~half day, unblocks cheap testing.
3. **1.2** (build verification gate, collect-only first) — closes the correctness hole.

After that you have a measurable, offline-testable, no-longer-silently-failing core loop — the foundation everything else stands on.
