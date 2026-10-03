# CLAUDE.md — AI Software Factory

A LangGraph pipeline that drives opencode agents (spec → architect → coder → tester) through
deterministic gates to build software projects. **The factory is an agent configuration**, so
changes to `agents/*.md`, `agents/policies/REVIEW.md`, `domain/gates.py` + `domain/ambiguity.py`,
`agents/tiers.toml`, or prompt assembly in `pipeline/` are behaviour changes and must be
regression-tested like code.

Governing contract: `docs/contract/EFFECTIVENESS.md`. When code and that doc disagree, one of
them is wrong — fix the mismatch, don't ignore it. Code layout, the layering rule and where a new
toolchain / gate / agent / command goes: `docs/ARCHITECTURE.md`.

## Commands

All commands run from the repo root (there is no `mvp/` wrapper any more). There is no global
`pytest` — always use the venv (`uv sync --dev` creates it).

| Command | Expected output |
|---|---|
| `make check` | `967 passed, 1 skipped` + `12/12 scenarios behaving as expected` + `68/68 checks green`. **Run before claiming done.** |
| `.venv/bin/python -m pytest -q` | `967 passed, 1 skipped` (~60s; the skip is the tsc-dependent TS test when `tsc` is absent; offline, zero tokens) |
| `.venv/bin/python -m pytest tests/verification/test_verify.py -q` | single file, for the TDD loop |
| `.venv/bin/factory simulate` | 12/12 scenario matrix, offline, zero tokens |
| `.venv/bin/factory evals` | 68/68 agent-configuration checks; exits non-zero below 100% |
| `.venv/bin/factory evals capture <run_id> <name>` | freeze a real run (or incident) as a permanent eval case |
| `.venv/bin/factory metrics` | SDLC indicators over the factory's own history, plus what is NOT measurable |
| `.venv/bin/factory replay <run_id>` | re-drives a past run's orchestration on frozen agent outputs, zero tokens |
| `.venv/bin/factory workspace` | resolved `$FACTORY_HOME` (default `~/.factory`), its `factory.db` and every project repo |
| `.venv/bin/factory status [project]` | where each project stands (Define → Plan → Build → Release, backlog per story, intake + story spend) and the exact next command; board key `s` |
| `.venv/bin/factory interview <project>` | interviews the operator about the product (live model calls), then writes + commits `docs/work/BRIEF.md` and `INTERVIEW.md` on approval; re-running resumes. `factory run --project` requires that brief (or `--no-interview`) |
| `.venv/bin/factory interview <project> --amend "what changed"` | reopens an approved brief for that change only, rewrites + commits it, then offers `factory backlog` so unstarted stories are re-proposed |
| `.venv/bin/factory interview <project> --import answers.json` | records answers from the `/factory-intake` skill (`[{topic, question, options?, answer, assumed?}]`); refuses, recording nothing, unless every required topic is answered |
| `.venv/bin/factory backlog <project>` | proposes the ordered story list from the brief; approve, or type feedback to regenerate |
| `.venv/bin/factory next <project> [--no-interview]` | starts the next approved backlog story (story-level interview first on a TTY) |
| `.venv/bin/factory doctor [--offline]` | preflight: opencode, a probe per distinct tier model (paid, tiny; skipped offline), go/node/tsc, `$FACTORY_HOME` + its DB, each product's `.opencode` link, a Python product without its own `.venv` (warning), leftover legacy `factory.db`; non-zero if a model is unreachable or the home is unusable |
| `.venv/bin/factory board` | Textual board; `m` (or ctrl+p) opens the menu of every major verb (new project, interview, backlog, next/new story, retry/replay, doctor, evals, simulate, metrics, tiers) |
| `FACTORY_RUNNER=claude .venv/bin/factory …` | runs every agent through the local Claude Code CLI (`claude -p`) on `agents/tiers.toml` `[claude_tiers]` instead of opencode; permanent via `factory.toml` `[runner] agents = "claude"` |
| `.venv/bin/factory --help` | full CLI verb list |

`ruff` is configured in `pyproject.toml` (line-length 100, `E,F,I,W`) but **is not installed** in
`.venv`; `make lint` skips with a notice rather than failing.

## Architecture

```
agents/                 The agent configuration: the 8 agent .md definitions (write/edit/bash/patch
                        all disabled), policies/REVIEW.md (injected into the tester prompt) and
                        tiers.toml (agent -> tier -> model, escalate-on-retry: the ONLY place
                        model ids are chosen).
.opencode/agents        -> ../agents (relative symlink; opencode resolves agents through it).
src/factory/
  domain/               PURE, no I/O, imports nothing else from factory.
    contracts.py        Pydantic models for every agent output.
    gates.py            Deterministic gate policy + every budget constant (MAX_*). No LLM calls.
    ambiguity.py        Threshold-term detection behind Checkpoint 1 (THRESHOLD_TERMS, unbound_criteria).
    task_order.py       Dependency-ordered task list (Kahn sort), shared by pipeline + PLAN.md;
                        dependency_problems = the strict reading gate-1 applies (cycles, unknown deps).
    authorization.py    The boss's rules: may a stage START, given the recorded verdicts? (pure)
    traceability.py     Deterministic AC ↔ tester-claim cross-check (catches silently dropped criteria).
    agent_output.py     parse_agent_json & friends.   project_spec.py  ProjectSpec model.
    backlog.py          BacklogStory/BacklogOutput (the backlog-agent's contract).
    lifecycle.py        A project's phases + the one next command (`factory status`, board `s`).
    interview.py        Intake interview: REQUIRED_TOPICS, InterviewTurn, uncovered_topics (coverage
                        is decided from recorded answers, never the model's claim), resolve_answer.
  agent_config/         tiers.py (loads + validates agents/tiers.toml; FACTORY_TIER_* env wins),
                        review_policy.py, settings.py (factory.toml at the checkout root or $FACTORY_SETTINGS:
                        budget cap + probe timeout + runner (opencode|claude); env > file > default; invalid = refused; `settings()` is read per call, never
                        cached).
  pipeline/             The orchestrator (LangGraph). Owns routing/remediation. __init__ is the
                        PUBLIC API — other packages import only from `factory.pipeline`.
    state.py            PipelineState.
    graph.py            Conditional edges, resume_entry_for, ONE build_pipeline(entry=…); the
                        build_*/compile_* resume names are that graph entered at another node.
    boss.py             Wraps every acting stage (graph._AUTHORIZED_STAGES): authorizes it from the DB's
                        gate verdicts before it runs; a refusal BLOCKS the run, agent never called.
    agent_calls.py      The single agent-call boundary (_run_or_replay, JSON repair). Tests
                        patch `factory.pipeline.agent_calls._run_or_replay` / `.run_agent`.
    nodes/              spec.py, architect.py, boundary.py (only when the design touches an
                        API/data/sensitive area), coder.py (+ remediation, scope diff), tester.py,
                        release.py (release-agent notes + `release` = the operator's approval),
                        gates.py (gate-1/2/test, settled_threshold_terms), evidence.py.
    prompts/            Every agent prompt: spec/architect/boundary/coder/tester/release.py, blocks.py,
                        context_pack.py. Byte-pinned by tests/pipeline/prompts/test_prompt_golden.py.
  verification/         Non-LLM build verification: python.py, go.py, typescript.py, scope.py
                        (declared-scope check); verify_changes in __init__.
  evidence/             artifacts.py (INTENT → SPEC → PLAN chain), adr.py (decision memory),
                        trust_package.py (+ schemas/), metrics.py, progress.py (per-run stage
                        flow + timeline from the DB; shared by CLI, TUI and simulate),
                        pipeline_record.py (docs/work/<story>/PIPELINE.md — the boss's committed
                        record of a run, written from runs.service._finish at every stop),
                        brief.py (docs/work/BRIEF.md + INTERVIEW.md, rendered from recorded answers),
                        backlog.py (docs/work/BACKLOG.md from the backlog rows).
  workspace/            layout.py ($FACTORY_HOME resolver: home/db_path/projects_dir, re-exported
                        from `factory.workspace`; EVIDENCE_PATHS = what the factory owns in a
                        product repo), projects.py, templates.py, git.py (checkpoint + evidence
                        commits, baseline, real diff), materialize.py, repo_map.py, legacy.py
                        (`factory workspace import-legacy`).
  adapters/             opencode.py + claude_cli.py + claude_sdk.py (the only places a model is
                        called; a `claude/<model>` id makes opencode.run_agent hand the call to
                        `claude -p`; the SDK one only for the interview, optional extra `.[claude]`),
                        result.py (AgentResult), notify.py.
  state/db.py           SQLite schema + every accessor. Additive migrations via _ensure_column.
  state/interviews.py   interview_answers / interview_turns accessors (verbatim agent I/O).
  state/backlog.py      backlog_stories accessors: a new proposal replaces only unstarted rows.
  selftest/             evals/ (agent-configuration regression), simulate.py (scenario matrix).
  preflight/doctor.py   `factory doctor` preflight; probes go through run_agent.
  runs/                 Application service: run / replay / resume / retry (service.py), resume
                        context + decision recovery (context.py), the intake interview that runs
                        BEFORE the pipeline (interview.py), the
                        story backlog + `factory next` (backlog.py), `factory status` (status.py). NEVER prints: reports progress through
                        an `on_event` callback (events.py) — the interview through `ask`/`approve`
                        callbacks — and refuses with `RunError`.
  interfaces/           render.py (every rich print helper; takes data, never reads the DB),
                        cli/ (main.py = argv dispatch + usage; run.py, review.py, project.py,
                        interview.py, backlog.py, selftest.py, board.py, workspace.py = one module per command
                        group),
                        board/ (tui.py, data.py, html_report.py). Nothing imports interfaces.
skills/                 Anti-slop skills (ponytail, ponytail-review, karpathy-guidelines, superpowers
                        TDD / systematic-debugging / verification-before-completion; MIT, LICENSES.md)
                        copied into every product's .claude/skills/ (`create_project`, `factory
                        project refresh`) for HUMANS / Claude Code sessions working on the
                        product. Factory agents run with tools disabled and never load them:
                        they only receive the "Code discipline" section of PROJECT_RULES.md,
                        injected into every agent prompt (golden-pinned).
evals/cases/*.json      Behavioural eval corpus (frozen agent outputs + expected outcome).
examples/specs/         Sample project specs.
docs/ARCHITECTURE.md    One page: layer diagram, package ownership, the one-way rule, where to add things.
docs/contract/          EFFECTIVENESS.md (contract), GATES.md, AGENTS.md, REVIEW_QUEUE.md,
                        context-pack.template.md.
docs/design/            Historical plans/specs + the original brief.
docs/challenges/        Challenge briefs (supportflow.md).
$FACTORY_HOME/          NOT in the repo (default ~/.factory): factory.db + projects/<slug>/,
                        each product its own git repo. `factory workspace` prints it.
tests/                  Mirrors src/factory/: tests/<area>/ tests src/factory/<area>/ (state/,
                        domain/, pipeline/prompts/, interfaces/{cli,board}/ ...). Cross-cutting
                        suites live in tests/integration/ (replay, project-workspace runs, the
                        subprocess-stdin invariant, test_layout.py = paths + layering rule).
                        conftest.py + fixtures/ stay at tests/ root. Test basenames are unique.
```

**Layering** (enforced by `tests/integration/test_layout.py`; arrows point one way only):

```
interfaces -> runs -> {pipeline, verification, evidence, workspace, selftest, agent_config} -> domain
```

`domain` imports nothing from factory. `adapters` and `state` serve the middle layers and depend
only on `domain`. Middle layers never import `runs`. NOTHING imports `interfaces` except the
console script (`factory.interfaces.cli.main:main`); `runs` and `selftest` report through return
values and callbacks. `interfaces/render.py` may not reach state/pipeline/adapters/workspace. The test's
`KNOWN_VIOLATIONS` allowlist is empty and may only shrink.

**Design principles.** Policy is deterministic Python, never delegated to an LLM. Agents are
replaceable executors; the pipeline and gates own the governance. Every agent's verbatim
input/output is stored, so any run replays offline for free. Evidence is version-controlled
(the artifact chain + ADRs), not only DB rows.

## Conventions

- Python 3.12+, `from __future__ import annotations`, full type hints, `pathlib` over `os.path`.
- Pydantic models for every agent output — a malformed response is `blocked`, never guessed at.
- Tests in `tests/<package>/test_<area>.py`, mirroring `src/factory/`. **TDD is mandatory** (`.claude/skills/tdd`): write the
  failing test, watch it fail, then implement.
- New deterministic checks go in `verification/` / `domain/gates.py` with a unit test — not into an agent prompt.
- Budget/threshold constants live as named constants in `domain/gates.py`, never inline in node logic.
- Comments explain *why* a non-obvious decision was made, not what the line does.

## Things Claude gets wrong here

- **Never make a live `opencode` call from a test.** Orchestration is tested offline via the
  replay-fixture pattern in `tests/integration/test_pipeline_replay.py` + `tests/fixtures/agent_outputs/`.
  Reaching for mocks to test the graph is the wrong instinct — seed `agent_logs` and set
  `replay_run_id`.
- **State lives in `$FACTORY_HOME` (default `~/.factory`), never the working directory.**
  `factory.workspace.db_path()` is THE database; `state.db.get_db/init_db` deliberately have no
  default path. Products are `$FACTORY_HOME/projects/<slug>/` — factory *output*, not source:
  never hand-edit them. `tests/conftest.py` points `FACTORY_HOME` at a tmp dir for EVERY test
  (autouse), so no test can touch the operator's real products.
- **`factory replay <id>` runs in a scratch clone** (`$FACTORY_HOME/replays/run-<id>/`, at the
  replayed run's baseline) and never writes into the product; a replay that parks at a
  checkpoint parks as a NEW `waiting_human` run, and `factory approve` on it keeps replaying
  (`runs.service._resume_state` carries `replay_run_id`) — zero tokens. It still writes rows
  into the real `factory.db`; to experiment without that, copy `$FACTORY_HOME`, rewrite
  `projects.repo_path` in the copy, and point `FACTORY_HOME` at it.
- **A project directory IS its git repository** (`projects.repo_path` == project dir ==
  `opencode_cwd` == `state["project_dir"]`). Its evidence (`workspace.layout.EVIDENCE_PATHS`:
  `docs/work/`, `docs/architecture/adr/`, `docs/releases/`, `PROJECT_RULES.md`,
  `project-spec.json`) is committed by the factory as it is produced via `git.git_commit_paths`
  (never `git add -A`, which would launder an out-of-band write), is refused as coder output
  (`materialize_code_blocks(reserved=...)`), and is excluded from every CODE measurement — the
  coder's scope check, the trust package's change set, the tester's diff. A new code
  measurement must pass `exclude=` too, or the factory's own paperwork shows up as agent work.
- **Never commit `*.db`.** All SQLite state is gitignored and regenerable.
- **Don't overwrite `state["story_id"]` with the agent's `spec.story_id`** — the agent invents its
  own numbering; the DB row id is canonical, and clobbering it orphans later story updates.
- **Agents cannot write files.** Code reaches disk only via `code_blocks` → `materialize_code_blocks`.
  Any file that appears in the repo undeclared is an out-of-band write and *blocks* gate-build.
  Don't "fix" that by enabling agent write tools.
- **A run that stops mid-coding discards its own uncommitted writes** (`coder._discard_attempt`
  → `workspace.git.git_discard_paths`): only the paths the factory materialized on this task's
  attempts (or the remediation pass) — restored from HEAD if tracked, else deleted. Never
  `git clean`: operator files, the out-of-band file itself and evidence stay. Otherwise the
  NEXT story is blocked as out-of-band on its first task. The code survives in `agent_logs`.
  `git_commit_all` (`git add -A`) never commits `__pycache__/`, `*.pyc`, `.pytest_cache/` or a
  product `/.venv/` (`_INFRA_EXCLUDES` in `.git/info/exclude`).
- **The runner is a choice, the tiers are not.** `settings().runner.agents` (`factory.toml`
  `[runner] agents`, env `FACTORY_RUNNER`) = `opencode` (default) or `claude`; under `claude`,
  `tiers.model_for_tier` reads `[claude_tiers]` (every tier, all `claude/<model>`, each priced)
  and the adapter runs `claude -p --tools "" --system-prompt <agent .md body>`, prompt on stdin.
  `FACTORY_TIER_*` still wins. Reviewer/author family independence does NOT hold under the
  claude runner — the operator's trade-off. `tests/conftest.py` unsets `FACTORY_RUNNER`.
- **Model choice lives in `agents/tiers.toml`, nowhere else.** Each agent's `.md` frontmatter
  repeats its `model_tier:` and that tier's `model:`; `tests/agent_config/test_tiers.py` and
  `factory evals` fail on drift. Change the toml and the frontmatter together. Default models
  must be ones this machine's opencode accepts: `openai/*` through the ChatGPT/Codex login
  (GPT-6 Astra/Sol/Luna), Anthropic ONLY as `requesty/claude-*` (`anthropic/*` is not a
  configured provider; Requesty must approve each model for the key). A reviewer tier
  never shares a model family with the authors (`tests/agent_config/test_tiers.py` pins
  it). Every tier model needs a `[prices]` entry for the $10 cap.
- **`factory doctor` tests patch `factory.selftest.doctor.run_agent`** — a probe is a real,
  paid model call.
- **The `factory:` commit-message prefix is load-bearing.** `workspace.git._factory_baseline()` finds the
  pre-factory baseline by locating the oldest commit whose subject starts with it.
- Agents may prefix paths with `repo/` (old PROJECT_RULES said "Source root: repo/");
  `materialize.normalize_block_path` strips it when the root is named `repo` or is a project
  repo (`repo_root=True`). Don't double-prefix.
- A gate may **pass and still need a human** (`GateResult.needs_human`). Passing ≠ crossing a checkpoint.
  Open questions from the spec-agent PARK the run (Checkpoint 1); they are not a failure.
- **Run orchestration lives in `factory.runs`, never in an interface.** The CLI renders its
  events (`cli.run.RunPrinter`); the TUI calls it with no callback. Don't print from `runs/`
  (a test forbids it) and don't import `interfaces.cli` from the board — that console-swap
  hack is what `runs/` replaced. Tests that drive a command patch the name where the command
  looks it up, e.g. `patch("factory.runs.run_project_pipeline")`.
- **The boss authorizes from the DB, not graph state.** A test that drives a mid-line graph
  (architect / coder / tester entry) must seed the gate rows a real run would have — e.g. a
  passed `gate-1-spec` before the architect, a passed or operator-APPROVED `gate-2-architect`
  before the coder — or the boss (correctly) refuses with "never ran". Don't bypass it; seed the
  record. A new agent node goes in `graph._AUTHORIZED_STAGES` with a rule in `domain/authorization.py`.
- **A reviewed run ends PARKED, never `completed`.** gate-test pass → release-agent → gate-release,
  which always parks (Checkpoint 3). Only `factory approve` at that checkpoint completes a story
  (the boss refuses `release` without the operator's APPROVAL on the newest gate-release row).
  A test that needs a completed run approves it: `runs.resume_run(run_id, "approve", ...)`.
  `evals capture` of a released run therefore expects `waiting_human` + gate-test passed.
- **An agent added after runs were recorded must survive their replay.** `_run_or_replay` raises
  `agent_calls.ReplayGap` when a frozen output is missing; the release-agent catches it and logs
  the stage `skipped`. Any new agent must do the same, or every captured eval case breaks.
- **Every terminal state must call `finish_run`.** Returning `{"status": "failed"}` in graph
  state only is how a run ends up stuck `running`, invisible to `factory queue`, and later
  mislabelled by `reconcile` as a dead process.
- **Resume must read the LATEST agent log** (`runs.build_resume_context` / `get_agent_log`, not
  `get_run_logs` + `next(...)` which is ascending). Otherwise the operator approves one
  design and the coder builds an earlier one.
- **The budget is $10 per user story** (default `MAX_STORY_COST_USD`; the live value is
  `settings().budget.max_story_cost_usd`, from `factory.toml` or `FACTORY_MAX_STORY_COST_USD`; tests pin
  `FACTORY_SETTINGS` to a missing file so defaults apply), checked before EVERY live
  model call (`agent_calls._check_budget` → `BudgetExhausted`) and by the boss before each
  stage. Usage comes from opencode's `step_finish` events (rows before 2026-10-02 are NULL);
  the ChatGPT/Codex login reports **$0**, so spend is tokens × list price from
  `agents/tiers.toml` [prices] (`domain/budget.py`). A new model needs a price there.
- **Default verification executes nothing the coder wrote.** `pytest --collect-only` is never
  run (collection imports — runs — test modules); the static import check covers what it
  caught. A missing toolchain FAILS its files, never skips them. The run header says when
  tests will not execute. Opted-in tests (`FACTORY_RUN_TESTS=1`) run with the PRODUCT's
  interpreter (`verification.python.product_python`: `<repo>/.venv/bin/python`, else
  `$FACTORY_PRODUCT_PYTHON`, else `sys.executable`), named in the check's detail; a
  third-party module (or pytest) missing from it is `pytest_run:warn` — an environment
  problem, never a pass and never "tests executed" in the trust package.
- **A failed task attempt's files stay uncommitted until the task passes**, so a retry's
  scope check subtracts `state["attempt_written"]` (what the factory materialized on the
  earlier attempts) from the unclaimed set; it is cleared when the task passes.
- **The coder's task pack carries the current text of its in-scope files**
  (`prompts.blocks.scope_files_block`, capped by `MAX_SCOPE_FILE_CHARS`, evidence paths
  skipped, a missing path marked "(new file)"); the block is omitted when the task has no
  scope, which is why the goldens (scope-less tasks) did not change.
- **Live progress is an event, not a print.** `runs.service._stream` streams LangGraph with
  `stream_mode=["tasks", "updates"]`: `NodeStarted` before a node runs, `NodeCompleted`
  after it with the duration/cost of the agent_logs rows it wrote (None, never 0, when
  unknown — a replay logs 0.0s).
- **`docs/work/BRIEF.md` existing IS the approved brief** (`runs.has_brief`); there is no flag
  in the DB. The intake interview (`factory interview`, `runs/interview.py`) runs BEFORE the
  pipeline — it is not a graph node, so the boss and `replay` know nothing about it — and
  `factory run --project` refuses a project without a brief unless `--no-interview` (on a TTY
  it interviews first). Once the brief is approved the same agent proposes `project-spec.json`
  (`confirm_stack`; corrections are `stack` answers, `MAX_STACK_PROPOSALS`). An approved brief
  is only reopened by `--amend`. On a TTY with a brief, `factory run --project` and `factory
  next` first run the story-level interview (`run_story_interview`, `# Mode: story`, at most
  `MAX_STORY_INTERVIEW_QUESTIONS`): its answers are appended to the story REQUEST, never stored
  as project answers (they would leak into the brief); `--no-interview` skips it, Checkpoint 1
  stays the safety net. The brief is injected into prompts by `project_memory_block`; with no
  BRIEF.md the prompts are byte-identical. Interview tests patch
  `factory.runs.interview.run_agent`; `tests/conftest.py` pins `FACTORY_INTERVIEW_ENGINE=opencode`
  for every test (with the `.[claude]` extra and `claude` on PATH, "auto" is a live SDK call); CLI tests patch `factory.runs.run_interview` / `.has_brief` /
  `.run_story_interview` / `.import_answers`.
  The `/factory-intake` skill (`.claude/skills/factory-intake/SKILL.md`) is a second door to the
  same interview: Claude asks in Claude Code, then calls `factory interview --import` (product) or
  `factory run --project --no-interview` with an Operator clarifications block (story).
- **`factory dismiss` of a failed/blocked run returns its backlog story to `approved`**
  (`state.backlog.return_to_backlog`), so `factory next` starts it again. `factory retry` only
  re-drives a run with an answered checkpoint; `factory status` offers it only then
  (`RunFact.retryable`), else dismiss → next.
- **One live run per project** (`runs.service._refuse_if_project_busy`): the coder's
  checkpoint stages the whole working tree. Replays are exempt (own scratch clone).
- **Every review diff starts at the run's `base_commit`** (state key), and Checkpoint 3 pins
  `candidate_commit`: `release` refuses if code changed since (evidence commits excluded).
- Editing anything in `agents/`, `domain/gates.py`, `domain/ambiguity.py`, `agent_config/`, or
  `pipeline/` (incl. `prompts/`) is an **agent-configuration change** — run `make evals`.
  `agents/*.md` and `agents/policies/REVIEW.md` are prompt TEXT: keep edits deliberate.
- **Assembled prompts are golden-pinned.** `tests/pipeline/prompts/test_prompt_golden.py`
  compares every agent's prompt byte-for-byte with `tests/fixtures/prompts/*.txt` (the tester's
  embeds `agents/policies/REVIEW.md`). A refactor must leave them identical; a DELIBERATE prompt
  change regenerates them with `FACTORY_UPDATE_GOLDEN=1 .venv/bin/python -m pytest
  tests/pipeline/prompts/test_prompt_golden.py`, and the fixture diff is what gets reviewed.
- **Patch where the name is looked up.** The agent boundary lives in `pipeline/agent_calls.py`,
  so tests patch `factory.pipeline.agent_calls._run_or_replay` (or `.run_agent`), not
  `factory.pipeline.*`. Outside `pipeline/`, import only names in `factory.pipeline.__all__`
  (`tests/pipeline/test_public_api.py` enforces it, plus graph → nodes → prompts/agent_calls → state).
- The trust package must never overstate evidence. `tests.passed` requires an executed
  suite; `diff.source` must be git-measured or `"unavailable"`.
- **A verification check must earn its verdict.** Two bugs of this shape were shipped:
  `tsc --noEmit` with no `-p` printed its HELP TEXT and exited 1 (a false FAIL on every
  monorepo), and Go was unknown to verification entirely (a false PASS on any Go repo).
  A new toolchain needs three things together: a `verification/<toolchain>.py` check wired
  into `verify_changes`, its test-run marker (`pytest_run`, `go_test`, …) in
  `evidence.trust_package._TEST_RUN_MARKERS` + `_TEST_RUN_COMMANDS`, and any structurally
  required manifest in `verification.scope._MANIFEST_NAMES` — or the scope check cries wolf.
  See `docs/ARCHITECTURE.md` → "A new toolchain".
