---
name: tdd
description: Use when implementing any feature or bugfix in the software factory (src/factory/) — write the failing test first, watch it fail, then implement. Triggers on "implement X", "add Y", "fix Z" in this repo.
---

# Test-Driven Development (factory)

Write the test first. Watch it fail. Write the minimal code to pass. Refactor.

**Core principle:** if you didn't watch the test fail, you don't know it tests the right thing.

This factory is a process-encoding machine; its own development must follow a strict process too. Good TDD is the most reliable way to keep agent-written and human-written code in this repo honest.

## When to use

Always, for factory work (run from the repo root):
- New pipeline nodes, gates, models, CLI commands
- Bug fixes (write the test that reproduces the bug first)

Skip only for: pure docs, the plan/workflow markdown, throwaway exploration.

## The loop

1. **Confirm the interface.** State the function/class signature and where it lives *before* writing anything. If boundaries are unclear, stop and design them — untestable code is a design smell, not a testing problem.
2. **Red.** Write one focused test asserting the desired behavior. Run it and watch it fail for the *expected* reason:
   ```bash
   .venv/bin/python -m pytest tests/<package>/test_<area>.py -q
   ```
3. **Green.** Write the minimum code to pass. No extra features, no speculative abstraction.
4. **Refactor.** Clean up with the test as a safety net. Re-run.
5. Repeat one behavior at a time.

## This repo's conventions

- Tests live in `tests/<package>/` mirroring `src/factory/<package>/`, named `test_*.py`, plain `unittest.TestCase` or pytest functions.
- Run a single file: `.venv/bin/python -m pytest tests/verification/test_verify.py -q`. Full suite: `.venv/bin/python -m pytest -q`.
- **Orchestration logic (pipeline gates/edges) must be tested offline** using the replay-fixture pattern in `tests/integration/test_pipeline_replay.py` + `tests/fixtures/agent_outputs/`. Seed an original run's `agent_logs`, then drive `compile_pipeline()` with `replay_run_id` set. **Never** write a test that makes a live `opencode` call.
- **Deterministic checks belong in code, not in an LLM.** New verification logic goes in `src/factory/verification/` / `src/factory/domain/gates.py` with its own unit test, mirroring `gate-build`.
- After implementing, run the full suite and confirm green before reporting done.

## Red flags (stop and fix)

- Writing implementation before a failing test exists.
- A test that passes the first time you run it (you didn't watch it fail — suspect it tests nothing).
- Reaching for mocks to test orchestration — use replay fixtures instead.
- "I'll add tests after" — that's not TDD, and in this repo it's how the silent-failure hole reopens.
