# Software Factory v1 — Design

**Date:** 2026-04-27
**Input spec:** `software-factory-agent-instructions.md` (root of this repo)
**Status:** Approved for implementation planning

This document is the design that supersedes the input spec where the two disagree.
It records architectural decisions made during brainstorming, the amendments to the
input spec, and the staged execution plan for v1.

---

## 1. Goals

Build a controlled SDLC system where AI agents are replaceable executors inside a
governed workflow. The system enforces:

- No work without a story (feature, bug, tech-debt, operational, release).
- No implementation before planning, architecture, and boundary validation.
- Small, idempotent, scoped tasks.
- Explicit gates before every stage starts.
- Persistent state for stories, tasks, pipelines, blockers, and releases.
- Minimal context per agent through context packs.
- Traceability from initial request to release.
- Quality gates for tests, security, tenant isolation, API contracts, and performance.

The original input spec defines roles, gates, schemas, and rules. This design
defines **how** to realize that spec as running software.

---

## 2. Stack

- **Agent runtime:** [opencode](https://opencode.ai) — model-agnostic, local, supports
  custom agents in markdown with YAML frontmatter, has a long-running `serve` mode
  with HTTP API and a non-interactive `run --agent <name> --attach <url>` CLI.
- **Workflow engine:** Python + [LangGraph](https://langchain-ai.github.io/langgraph/)
  — stateful graph execution, native `interrupt()` for human-in-the-loop, resumable.
- **State store:** DuckDB (file-backed, embedded, no server). One factory-level DB
  plus one per project.
- **Artifacts:** Markdown for human-readable docs, JSON (against schema) for
  machine-readable handoffs. Files are the source of truth; DB rows reference paths.

### 2.1 Why opencode

- Custom agents are first-class: `.opencode/agents/<name>.md` with frontmatter for
  `mode`, `model`, `temperature`, and per-agent `permission` (`edit`, `bash`).
- Headless invocation works today: `opencode run --agent <name> --attach <url>`.
- One long-running `opencode serve` process avoids cold-boot per agent call.
- Model-agnostic — cheap models for governance agents, smarter models for
  code-touching agents, declared per-agent in frontmatter.

### 2.2 Why a Python orchestrator (not the TS opencode SDK)

- LangGraph is Python-first and more mature than its TS counterpart.
- The orchestrator never needs to enter opencode's session model — it shells out
  per agent call. Language is irrelevant on the wire.
- DuckDB has a clean Python binding.

---

## 3. Repository layout

Everything lives directly at the repo root (no `software-factory/` wrapper dir).

```
/Users/joaooliveira/dev/personal/factory/
├── software-factory-agent-instructions.md     # input spec (do not edit)
├── README.md
├── FACTORY_RULES.md
├── COMMON_RULES.md
├── HANDOFF_RULES.md
├── AGENTS.md                                  # human-readable agent index
│
├── .opencode/
│   └── agents/
│       ├── god.md
│       ├── spec-agent.md
│       ├── boss.md
│       ├── architect-agent.md
│       ├── boundary-agent.md
│       ├── coder-agent.md
│       ├── tester-agent.md
│       └── release-agent.md
│
├── orchestrator/                              # Python LangGraph code
│   ├── pyproject.toml
│   ├── src/factory/
│   │   ├── __init__.py
│   │   ├── graph.py                           # LangGraph definition
│   │   ├── nodes/                             # one node per agent
│   │   ├── opencode_client.py                 # `opencode run --attach` wrapper
│   │   ├── handoff.py                         # JSON schema validation
│   │   ├── state.py                           # DuckDB access
│   │   ├── budget.py                          # token + loop caps
│   │   ├── stubs.py                           # fixture responses for v1
│   │   └── cli.py                             # `factory request "..."`
│   └── tests/
│
├── state/
│   ├── factory.schema.sql
│   └── factory.duckdb                         # gitignored
│
├── stories/
│   ├── .template.md
│   ├── index.md
│   └── US-XXXX/                               # one dir per story, holds versions
│       ├── US-XXXX.v1.md
│       ├── US-XXXX.v2.md
│       └── current -> US-XXXX.v2.md           # symlink
│
├── tasks/
│   ├── .template.md
│   └── index.md
│
├── context-packs/
│   └── .template.md
│
├── handoffs/
│   ├── handoff.schema.json
│   └── examples/
│       └── example-handoff.json
│
├── workflows/
│   ├── project-workflow.md
│   ├── langgraph-design.md
│   └── gates.md
│
└── projects/
    └── PROJ-001-example/
        ├── PROJECT_RULES.md                   # port_range, database, git_remote, deviations
        ├── .secrets/                          # gitignored .env-style local secrets
        │   └── .env.example
        ├── state/
        │   ├── project.schema.sql
        │   └── project.duckdb
        ├── docs/
        │   ├── work/
        │   │   ├── stories/
        │   │   └── tasks/
        │   ├── context/
        │   ├── pipeline/<task-id>/            # SOURCE OF TRUTH for handoffs
        │   │   ├── architect-agent.handoff.json
        │   │   ├── architect-agent.summary.md
        │   │   ├── boundary-agent.handoff.json
        │   │   └── ...
        │   ├── architecture/adr/
        │   └── releases/
        └── repo/                              # actual source code (FastAPI sample)
            └── dev.sh                         # local service start/restart
```

**Key deviations from the input spec's §4:**

- No `projects/PROJ-XXX/agents/` directory. Agents live once at the factory level;
  per-project differences flow through `PROJECT_RULES.md` injected into context packs.
- Stories live in `stories/US-XXXX/` directories holding append-only versions, not
  flat files.
- `handoff.json` and `summary.md` live in `projects/<proj>/docs/pipeline/<task-id>/`
  and are the source of truth. DuckDB rows reference these paths.

---

## 4. Runtime topology

```
[ User CLI: `factory request "..."` ]
        │
        ▼
[ LangGraph orchestrator (Python) ] ──► DuckDB (factory + project)
        │
        │  per node:
        │  opencode run --agent <name>
        │    --attach http://localhost:4096
        │    --format json
        │    --prompt-file <generated>
        ▼
[ opencode serve (long-running, started by `factory up`) ]
        │
        ▼
[ .opencode/agents/<name>.md ]   ← one set, factory-wide
```

`opencode serve` is started once via `factory up` and runs until `factory down`.
The orchestrator never restarts it between calls.

---

## 5. The eight agents

| Agent | Layer | Default model | `permission.edit` | `permission.bash` |
|---|---|---|---|---|
| `god` | Factory | Haiku | `ask` | `ask` |
| `spec-agent` | Factory | Sonnet | `ask` | `ask` |
| `boss` | Project | Haiku | `ask` | `ask` |
| `architect-agent` | Project | Sonnet | `allow` | `ask` |
| `boundary-agent` | Project | Sonnet | `deny` (review-only) | `deny` |
| `coder-agent` | Project | Opus first try, Sonnet on retry | `allow` | `allow` |
| `tester-agent` | Project | Sonnet | `allow` | `allow` (run tests) |

opencode's `permission` flag controls whether the agent can use `edit`/`bash`
tools at all — it does **not** restrict which paths the agent may touch. Path
scoping is enforced by the orchestrator: each agent's context pack lists
`Allowed Scope` and `Forbidden Scope`, and the orchestrator validates the
files-changed list in the agent's handoff against those lists before accepting
the run. A scope violation flips the verdict to `fail` regardless of what the
agent itself reported.
| `release-agent` | Project | Haiku | `ask` | `ask` |

`ask` permissions trigger a CLI prompt. In `--yolo` mode `ask` is auto-approved
inside the orchestrator wrapper (not in opencode itself).

Roles, responsibilities, must/must-not rules, and required summary formats are
defined in the input spec §5 and reproduced (without modification, except for
amendments below) in each agent's `.md` file under `.opencode/agents/`.

---

## 6. Workflow gates

Gates 0–7 are defined in input spec §6 and reproduced verbatim in
`workflows/gates.md`. The amendments in §10 below extend but do not weaken the
gates.

LangGraph maps gates to edges, not nodes. An edge from node A to node B encodes
gate N — if gate N fails, the edge isn't taken; the orchestrator either loops
back, fails the task, or escalates to a human.

---

## 7. Human approval (interrupts)

LangGraph `interrupt()` at three transitions:

1. After `spec-agent` produces a story + tasks — before `boss` begins.
2. After `boundary-agent` returns `pass` — before `coder-agent` writes any code.
3. After `tester-agent` returns `pass` — before `release-agent` finalizes.

CLI prompt format:

```
[T-0001 / boundary-agent → coder-agent]
Verdict: pass
Summary: <auto-extracted>
Diff preview: (none yet, this is pre-implementation)

[a]pprove  [r]eject with notes  [v]iew handoff  [q]uit
>
```

`--yolo` bypasses all three. State is checkpointed before each interrupt; an
abandoned run resumes via `factory resume <task-id>`.

---

## 8. State and artifacts

### 8.1 DuckDB schema

Reproduce input spec §9 verbatim, plus these additions on `tasks`:

```sql
ALTER TABLE tasks ADD COLUMN remediation_loops_used INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN token_budget_used      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN locked_at              TIMESTAMP;          -- one-task-per-project lock
ALTER TABLE tasks ADD COLUMN locked_by_run_id       TEXT;               -- FK -> agent_runs.id
```

**Which DB holds which table:**

| Table | Factory DB | Project DB |
|---|---|---|
| `projects` | yes | no |
| `stories` | yes (canonical) | no |
| `tasks` | no | yes (canonical) |
| `agent_runs` | no | yes |
| `blockers` | yes (factory-level only, e.g. cross-project) | yes (task-level) |
| `releases` | no | yes |

Stories live at the factory level because `god` and `spec-agent` need to query
across projects (e.g., "find all open bug stories"). Tasks and below are
project-scoped — `boss` and the project-layer agents only ever query their own
project's DB. The orchestrator opens the right DB by project_id.

### 8.2 Source-of-truth rule

For every entry in `agent_runs`:

- `summary_path` points to a file at `projects/<proj>/docs/pipeline/<task-id>/<stage>.summary.md`.
- `handoff_path` points to a file at `projects/<proj>/docs/pipeline/<task-id>/<stage>.handoff.json`.

If a row exists without both files on disk, the row is invalid and the task is
considered corrupted. The orchestrator validates this on resume.

### 8.3 Versioning

Stories and ADRs are append-only. Each revision creates a new file
(`US-0001.v1.md` → `US-0001.v2.md`). A `current` symlink in the story directory
points at the active version. Tasks are not versioned at v1 — if a task changes
materially, it gets a new ID and the old one is closed with status `superseded`.

---

## 9. Handoff schema

Per input spec §8, with these required additions on every handoff:

```json
{
  "work_id": "T-0001",
  "parent_story": "US-0001",
  "project_id": "PROJ-001",
  "stage": "coder-agent",
  "input_artifacts": ["..."],
  "output_artifacts": ["..."],
  "verdict": "complete",
  "blockers": [],
  "risks": [],
  "assumptions": [],
  "scope_deviations": [],
  "next_authorization": "tester-agent",
  "remediation_loop": 0,
  "tokens_used": 12345,
  "defects_discovered": []
}
```

`defects_discovered` is required on every stage but is non-empty only for
`coder-agent` and `tester-agent` in practice. Items in this array surface upward
through `boss` → `release-agent` for human triage at the next approval gate.

---

## 10. Spec amendments

These amendments override the input spec. They were agreed during brainstorming
on 2026-04-27.

### 10.1 `coder-agent` "no silent fix" path is defined

The input spec §5.6 says `coder-agent` should "ask for or create a defect task
through the workflow" but provides no channel. Amendment:

- `coder-agent` MUST record any unrelated issue it discovers in
  `handoff.defects_discovered` as an array of `{description, severity, location}`.
- `coder-agent` MUST NOT fix unrelated issues unless they block the current task.
- `boss` MUST propagate `defects_discovered` from each project-layer agent's
  handoff into its own summary.
- `release-agent` MUST surface accumulated defects to the human at the
  pre-release approval gate. The human decides: file as new story (recorded by
  `god`), accept and ship, or block.

### 10.2 Loop and budget control

- Each task carries `max_remediation_loops` (default 3) and `max_token_budget`
  (default 200,000 tokens, configurable per task).
- The orchestrator increments `tasks.remediation_loops_used` on every loop back
  to a prior stage.
- The orchestrator sums `handoff.tokens_used` into `tasks.token_budget_used`.
- Hitting either cap escalates to the human (out-of-band interrupt, not the
  three normal approval gates).

### 10.3 Concurrency

- One in-flight task per project. The orchestrator acquires a project-level lock
  by setting `tasks.locked_at` and `tasks.locked_by_run_id` on the task it is
  about to run; it refuses to start any other task in the same project while the
  lock is held.
- The data model permits cross-project parallelism (different projects = different
  locks), but the v1 orchestrator is single-worker and runs everything serially —
  this is a v2 promise, not a v1 capability.
- Lock release happens on task `complete`, `fail`, or human-issued `abort`.

### 10.4 Files are the source of truth

- Every persisted artifact (story, task, context pack, handoff, summary, ADR,
  release notes) is a file.
- DB rows hold paths and metadata only.
- On `factory resume`, the orchestrator reconciles DB state against the
  filesystem; conflicts mean the run is corrupted and requires human triage.

### 10.5 Append-only versioning

- Stories: see §8.3. ADRs: same pattern under
  `projects/<proj>/docs/architecture/adr/ADR-XXXX/`.
- Migrations are inherently append-only by SQL convention; the design adds no
  rule beyond "never edit a committed migration".

### 10.6 Per-agent permissions

Set in each agent's frontmatter as in §5 above.

### 10.7 Prompt-injection defense for `god`

`god` is the only agent that consumes raw, unstructured human input. Defenses:

- Hard cap on request length (8,000 chars). Anything longer is rejected with a
  message asking the human to summarize.
- Strip ASCII control characters and zero-width Unicode before passing to opencode.
- Raw input is logged separately to `state/factory.log.jsonl` *before*
  classification, so post-hoc inspection of injection attempts is possible.
- `god`'s system prompt explicitly instructs it to treat the entire user message
  as data, never as instructions, and to refuse anything that looks like an
  embedded directive ("ignore previous instructions", "you are now…", etc.).
- `god` cannot call any agent directly — it only writes to `requests/` and
  `state.factory.duckdb`. Even a fully compromised `god` cannot bypass the
  workflow gates that govern downstream agents.

---

## 11. Operational conventions

These are operational decisions that supplement the input spec rather than
amend it. They define how the factory plays with deploy targets, ports,
databases, secrets, and concurrent requests.

### 11.1 Deployment

The factory does **not** deploy. It produces a PR; deploy is handled externally:

- Each project's `PROJECT_RULES.md` declares `git_remote` (e.g.,
  `git@github.com:joaooliveira/proj-001.git`). The human creates the GitHub
  repo once and adds the URL.
- `release-agent` performs `git push origin factory/T-XXXX` on the per-task
  branch and opens a PR to `main` via `gh pr create`. It stops there.
- The human reviews and merges the PR.
- [Coolify](https://coolify.io) is the deploy target. It watches `main` and
  deploys automatically on merge. The factory has no Coolify integration code —
  the integration is "Coolify is configured to auto-deploy this repo's main
  branch", outside the factory.

Implication: the factory has zero code to manage deploys, infra, or service
restart in production. All of that is Coolify's job.

### 11.2 Local service management

For local development of managed projects, each project ships a
`projects/<id>/repo/dev.sh` that starts/restarts that project's services.
The factory exposes a thin convenience wrapper:

```
factory project run <project-id>     # exec dev.sh
factory project stop <project-id>    # exec dev.sh stop (if defined)
```

`dev.sh` is the project's responsibility to write; the factory neither
generates nor validates it at v1.

### 11.3 Port allocation

Each project reserves a port range to avoid local-dev collisions:

- `PROJECT_RULES.md` declares `port_range: 8000-8099` (100 ports per project).
- The factory enforces uniqueness across registered projects: the
  `projects` table in the factory DB has a `port_range_low` and
  `port_range_high` column with a uniqueness constraint preventing overlap.
- PROJ-001-example reserves `8000-8099`. Future projects pick the next free
  100-port window (`8100-8199`, etc.).

### 11.4 Database conventions

- Local development assumes a single Postgres instance on `localhost:5432`.
- Each project gets its **own database** (not its own instance) on that
  shared server.
- `PROJECT_RULES.md` declares `database_name: proj001` and
  `database_engine: postgres` (default; `sqlite` allowed as deviation).
- Connection string format: `postgresql://user:pass@localhost:5432/<database_name>`.
  Credentials come from secrets (§11.5), not from the factory.
- Factory state DBs (`state/factory.duckdb` and per-project
  `projects/<id>/state/project.duckdb`) are DuckDB and unrelated to project
  data DBs.

PROJ-001-example deviates: it uses **SQLite** for v1 to keep bootstrap
friction low. Its `PROJECT_RULES.md` documents the deviation explicitly.
v1.5+ projects created by `factory project create` will default to Postgres.

### 11.5 Secrets

- **Local development:** secrets live in `projects/<id>/.secrets/` (gitignored)
  as `.env`-style key=value files. The factory loads them as env vars when
  invoking project commands. Agents see secret **names** in their context pack,
  never **values**.
- **Production:** secrets are managed by Coolify (UI-driven, encrypted at rest,
  injected into containers as env vars). The factory does not deploy and does
  not see production secrets.
- **Future option:** if a project needs more sophisticated secret management
  than Coolify provides, [Infisical](https://infisical.com) is the documented
  fallback. Not implemented in v1.

### 11.6 Request queue

`god` accepts requests via `factory request "..."`. Multiple requests can be
submitted while a task is in flight; they are persisted and processed serially:

- Factory DB gains a `requests` table: `id`, `raw_text`, `submitted_at`,
  `status` (`queued`, `classifying`, `routed`, `rejected`).
- A single worker (the running orchestrator process) drains the queue in FIFO
  order. While one request is being routed, others wait.
- `factory request --wait` blocks until that request reaches a routed or
  rejected state. Without `--wait`, the CLI returns immediately with the
  `request-id` and the human can use `factory status request <id>` later.

Concurrency rule from §10.3 still applies after routing: at most one in-flight
task per project, regardless of request order.

### 11.7 Sequential agents within a task

Agents within a single task run strictly sequentially per input spec §5.3.
The data model and graph definition do **not** explicitly serialize beyond
what the gates require — i.e., a future v2 could fan out subagents (e.g.,
`coder-agent` spawning per-file workers) without a schema change. v1 simply
never takes such a path.

### 11.8 CLI surface

Consolidated reference for v1:

```
factory up                              start opencode serve + initialize state
factory down                            stop opencode serve

factory request "..."                   submit a request (queues, returns request-id)
factory request "..." --wait            submit and block until routed/rejected
factory request "..." --project <id>    skip god's project routing

factory status                          show all in-flight tasks and queued requests
factory status <task-id>                detailed pipeline state for one task
factory status request <request-id>     state of a specific request

factory resume <task-id>                resume an interrupted task
factory abort <task-id>                 cancel a task and release its lock

factory logs <task-id>                  show all stage transcripts for a task
factory logs <task-id> --stage <agent>  filter to one agent's transcript

factory project list                    list registered projects
factory project run <project-id>        exec the project's dev.sh
factory project stop <project-id>       exec dev.sh stop, if defined

factory --yolo <subcommand>             skip all human approval gates
```

`factory project create` is **not** in v1 — projects are pre-scaffolded by the
human (per A4-α decision: option (b)). Added in v1.5+.

---

## 12. Staged execution plan

The whole point of v1 is to validate the state machine, gates, and handoffs
without burning tokens. Real LLM calls turn on agent-by-agent in v1.5+.

| Stage | Scope | Definition of done |
|---|---|---|
| 0 | LangGraph + DuckDB + opencode serve up; all 8 agents return canned `pass` handoffs from `stubs.py` fixtures | A `factory request` runs end-to-end, all gates pass, all artifacts written, all DB rows correct |
| 1 | Add `fail`/`warn`/`blocked` fixtures + remediation loops + human interrupts | Every branch in `graph.py` has a fixture that exercises it; `factory resume` works after an interrupt |
| 2 | Real `god` + `spec-agent` (smallest blast radius, cheapest models) | A real human request produces a real story + tasks; rest still stubbed |
| 3 | Real `boss` + `architect-agent` + `boundary-agent` | Pre-implementation governance produces real ADRs and boundary verdicts |
| 4 | Real `coder-agent` + `tester-agent` against PROJ-001 | A real code change lands in `projects/PROJ-001/repo/` with passing tests |
| 5 | Real `release-agent` | Full loop end-to-end on a real task |

**v1 = stages 0 + 1.** v1.5 = stages 2–5, on by one.

---

## 13. The sample project (PROJ-001)

A tiny real FastAPI app, just enough surface area to exercise every gate:

```
projects/PROJ-001-example/
├── PROJECT_RULES.md       # declares port_range, database, git_remote, deviations
├── .secrets/              # gitignored, local secrets
│   └── .env.example
└── repo/
    ├── pyproject.toml
    ├── dev.sh             # start/stop services for `factory project run`
    ├── src/proj001/
    │   ├── __init__.py
    │   ├── main.py        # FastAPI app, single /health endpoint
    │   ├── db.py          # SQLModel + SQLite engine
    │   └── models.py      # one tenant-scoped SQLModel to exercise tenant boundary
    ├── tests/
    │   ├── test_health.py
    │   └── conftest.py
    ├── alembic.ini
    └── alembic/           # Alembic migrations against SQLModel metadata (empty until architect-agent adds one)
        ├── env.py
        └── versions/
```

Why FastAPI + SQLModel + Alembic: this is the smallest stack where the
`boundary-agent` checks (tenant isolation, API contract, OpenAPI, pagination,
error model) and `architect-agent` checks (migrations, indexes, schema notes)
all have real things to look at.

`PROJECT_RULES.md` for PROJ-001 declares (illustrative):

```yaml
project_id: PROJ-001
port_range: 8000-8099
database_engine: sqlite        # deviates from factory default (postgres) for v1 simplicity
database_name: proj001
git_remote: git@github.com:joaooliveira/proj-001-example.git
deviation_notes:
  - "SQLite chosen over Postgres to minimize bootstrap friction in v1; switch to Postgres in v1.5"
```

---

## 14. Out of scope for v1

These are deliberately deferred:

- **CI/CD via GitHub Actions or similar.** Coolify does production deploys
  (§11.1). The factory itself does not invoke any CI service.
- **Multi-user / auth on the orchestrator.** Single-user local tool.
- **Windows support.** macOS and Linux only (we use POSIX symlinks for `current`
  pointers in story versioning).
- **Multi-task concurrency within a project.** One project lock at a time
  (§10.3).
- **Cross-project work coordination** beyond `god`'s factory-level visibility.
- **Web UI / dashboard.** CLI only.
- **Release-agent merging or publishing.** It pushes a branch and opens a PR;
  human merges. No `gh release create`, no tag automation.
- **Story/task editing through the orchestrator.** Edit files directly; the
  orchestrator validates on resume.
- **Infrastructure-as-code managed by the factory.** Use Coolify's built-in
  automation; the factory has no Terraform/Pulumi/CDK awareness.
- **Resumption on a different machine.** DBs are local file-backed; a run
  belongs to the machine that started it.
- **Operating offline in stages 2+.** Real LLM calls require network.
- **Self-modifying agents.** `.opencode/agents/*.md` are human-edited; agents
  cannot rewrite their own role files.

What is **in scope** (clarifying earlier ambiguity):

- PR creation via `gh pr create` from `release-agent` (§11.1).
- Per-task git push to a `factory/T-XXXX` branch with a co-author trailer
  identifying the agent.
- Prompt-injection defenses on `god` (§10.7).
- Request queue (§11.6).
- Local secrets via `.env`-style files in `projects/<id>/.secrets/` (§11.5).
- Local service management via per-project `dev.sh` (§11.2).
- Port range registration per project (§11.3).

---

## 15. Open questions

None blocking implementation. Items to revisit during stage 2+:

- Whether `god` should auto-route across multiple projects (current assumption:
  the human picks the project on `factory request`).
- Whether `spec-agent` should be allowed to refuse a request and bounce it back
  to `god` for re-classification (current assumption: yes, via `verdict: fail`).
- Token-cost telemetry beyond per-task — do we want per-stage histograms?

---

## 16. Inputs to the implementation plan

Anything the implementation plan needs to know that isn't covered above:

- The orchestrator is a brand-new Python project. No existing code to integrate
  with.
- Python version: latest stable (3.13+).
- Package manager: `uv` (fast, lockfile-driven; matches the user's stated
  preference for modern tooling).
- Testing: `pytest`, with a separate test target for stages 0/1 (stub-driven,
  fast) vs. stages 2+ (real LLM calls, slow, opt-in via marker).
- Linting/formatting: `ruff` only (formatter + linter in one).
- LSP usage: the orchestrator code itself should be navigable by Pyright /
  pylsp; no special structure required.
