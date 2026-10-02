# AI Software Factory

You write a request in plain language. The factory turns it into a **story → design →
code → review**, stopping for your approval only where judgement is genuinely
required. The code lands in its own git repository with a dossier of evidence
beside it.

It is a **solo delivery engine**: one operator ships their own projects, mostly
unattended, on greenfield or existing codebases. When goals conflict it protects
**trustworthy output** over autonomy, throughput or speed — it is allowed to be
slower and more human-gated if that buys correctness and traceability.

Its effectiveness metric is **trust per interruption**: the factory is working when
it hands you a package you can trust *without reading all the code*, and interrupts
you only when you are the only one who can decide.

> **Governing contract:** [`docs/contract/EFFECTIVENESS.md`](docs/contract/EFFECTIVENESS.md).
> If the code and that document disagree, one of them is wrong.

---

## How it works

Four opencode agents, driven by a LangGraph pipeline, separated by **deterministic
gates written in Python** — never by an LLM deciding whether an LLM did well.

```
request
  → spec-agent      story, acceptance criteria, sliced tasks
  → [gate-1]        structural checks   ── ⏸ CHECKPOINT 1: "is this the right work?"
  → architect-agent design, ADR, risks
  → [gate-2]        scope + risk flags  ── ⏸ CHECKPOINT 2: "is this the right design?"
  → coder-agent     ONE task per call, dependency-ordered
  → [gate-build]    it must actually compile
  → tester-agent    QA / security / performance sub-verdicts
  → [gate-test]     blocking quality gate
  → trust package   the evidence you sign off against
```

**Agents cannot write files.** `write`, `edit`, `bash` and `patch` are disabled on
all four. Code reaches disk only through the coder's declared `code_blocks`, and any
file that appears in the repo undeclared *blocks* the build gate. That single
chokepoint also refuses credential-shaped paths (`.env`, `.git/`, key material).

---

## Setup

```bash
# 1. opencode — the agent runtime the factory shells out to
brew install opencode              # or see https://opencode.ai
opencode auth login                # you pay for the model calls

# 2. the factory itself (Python 3.12+), from the repo root
uv sync --dev

# 3. confirm it works — costs nothing, calls no model
make check

# 4. preflight before your first run: opencode, one probe per tier model, toolchains,
#    $FACTORY_HOME and its DB
.venv/bin/factory doctor           # `--offline` skips the (tiny, paid) model probes
```

Every command below is `.venv/bin/factory …` (or plain `factory …` with the venv
activated: `source .venv/bin/activate`). `factory --help` lists every verb.

`make check` should print `5xx passed` (every test green), `9/9 scenarios behaving as
expected` and `44/44 checks green`. That is the whole verification loop in one command.

---

## Your first project

```bash
# Register a project. Creates ~/.factory/projects/bookmarks/ — a git repository —
# with PROJECT_RULES.md and a project-spec.json from the stack template, committed
# as its first "factory:" commit.
.venv/bin/factory project create bookmarks --stack fastapi

# Ask for one thing.
.venv/bin/factory run --project bookmarks \
  "let me search my bookmarks by title, paginated 20 per page"
```

The run streams its progress. It will either finish, or **park** and tell you.
(A request spends tokens, so the CLI refuses anything that looks like a mistyped
command — `factory lsit`, `factory 'project list'` — with a suggestion, before any
model is called.)

```bash
.venv/bin/factory queue          # what is waiting for you, and why
.venv/bin/factory board          # interactive board — approve/reject in place
.venv/bin/factory review 17      # the full package for one run
```

```bash
.venv/bin/factory approve 17
.venv/bin/factory reject 17 "use polling, not websockets; drop the admin screen"
```

**Rejection is not a dead end.** Your feedback re-enters the pipeline as a new
attempt: reject at Checkpoint 1 and the story is re-specified with your answers;
reject at Checkpoint 2 and the design is redone with your objections.

---

## Where products live

Not in this repository. The factory keeps its state in **`$FACTORY_HOME`** (default
`~/.factory`), never in the directory you run it from:

```
$FACTORY_HOME/                 default ~/.factory
├── factory.db                 every run, gate and verbatim agent input/output (SQLite)
└── projects/<slug>/           one product = one git repository (code + evidence)
```

```bash
.venv/bin/factory workspace                 # the resolved home, its DB, every product repo
FACTORY_HOME=~/scratch-factory .venv/bin/factory project create demo   # a separate home
```

The checkout holds only the factory itself: the agent configuration in
[`agents/`](agents/) (definitions, review policy, model tiers), the code in
`src/factory/`, the eval corpus and the docs. Deleting a product is deleting its
directory; deleting `$FACTORY_HOME` resets the factory's history.

`factory doctor` checks the home is usable, its DB opens, and each product's
`.opencode` link resolves to this checkout's `.opencode/` (and so its `agents/`) — a product created from another
checkout would otherwise run with that checkout's agent definitions. A live project
run refuses outright when the link points at another checkout, and prints the
`ln -sfn` that re-points it.

The home is self-contained: `factory.db` stores each product's location relative to
it, so a copied or moved home (a backup, a test copy) governs its own products and
never writes into the original's. `factory replay` never touches a product either: it
re-drives the run in `replays/run-<id>/`, a scratch clone at the replayed run's
baseline — and approving a parked replay keeps replaying there, zero tokens.

---

## What the factory hands you

```
~/.factory/projects/bookmarks/        ← ONE git repository: your software + its audit trail
├── <your code>                       ← written by the coder, one "factory:" commit per task
├── PROJECT_RULES.md                  ← your standing rules (agents read them)
├── project-spec.json                 ← stack, conventions, constraints
└── docs/
    ├── work/US-0001/
    │   ├── INTENT.md                 ← your raw request, with author and date
    │   ├── SPEC.md                   ← problem, acceptance criteria, non-goals, tasks
    │   └── PLAN.md                   ← files that change, order of work, risks, proof
    ├── architecture/adr/             ← the design decision and why
    └── releases/                     ← the trust package (JSON)
```

Every piece of evidence is committed by the factory the moment it is written, so
`git log` in the product reads as its audit trail and a clone carries it. The factory
owns those paths: the coder may not write them, and they are left out of every
*code* measurement (the coder's scope check, the trust package's change set, the
diff the tester reviews).

**Coming from the old layout?** State used to sit beside the factory checkout
(`mvp/factory.db` + `mvp/projects/PROJ-*/{repo,docs}`), with each product's evidence
outside its repository. One command moves it into `$FACTORY_HOME`, merging each
product's evidence into its repo and committing it:

```bash
.venv/bin/factory workspace import-legacy mvp --dry-run   # see the plan; nothing moves
.venv/bin/factory workspace import-legacy mvp
```

`factory doctor` warns while an un-imported `factory.db` is still in the checkout, and
flags any product whose `.opencode` link points at another checkout.

The **trust package** is the point. Four artifacts are non-negotiable for release
sign-off (EFFECTIVENESS §5): passing tests with acceptance-criteria coverage, the
real git diff, the ADR, and a security verdict. It will **not** overstate itself — if
no test body actually ran, or the diff could not be measured from git, it says so and
names it as a blocker rather than offering you a green light.

`PLAN.md` → *Files that change* is the scope the real git diff is checked against, so
"the merged diff matches the committed plan" is an actual check, not a slogan.

---

## Try it without spending anything

Everything here is offline, deterministic and free — no model calls:

| Command | What it does |
|---|---|
| `factory simulate` | drives 9 representative stories through the real pipeline |
| `factory evals` | 44 regression checks on the **agent configuration** |
| `factory replay <run_id>` | re-runs a past run's orchestration on its frozen outputs, in a scratch clone (`$FACTORY_HOME/replays/`) |
| `factory metrics` | how the factory has actually been performing |
| `factory tiers` | which model each agent runs at |
| `factory workspace` | where the state lives: `$FACTORY_HOME`, its `factory.db`, every product repo |
| `factory doctor --offline` | preflight: opencode + go/node/tsc on PATH, `$FACTORY_HOME` and its DB, each product's `.opencode` link, any un-imported legacy `factory.db` (drop `--offline` to also probe each tier model — one tiny paid call per model) |

**Which model runs where is data, not code:** `agents/tiers.toml` maps each agent to a
tier, each tier to an opencode `provider/model`, and names the agents that escalate a
tier on retry. `FACTORY_TIER_<TIER>=provider/model` overrides a tier for one run. Run
`factory doctor` after changing either, so an unsupported model fails in seconds instead
of after a spec and an architect call have been paid for.

**`factory evals` is worth understanding.** The factory *is* an agent configuration
— the agent markdown files, the gate policy, the model tiers, the prompt assembly —
so editing `coder-agent.md` changes what gets built as surely as editing the
orchestrator does. The eval suite pins that: tools stay disabled, tiers match the
registry, and each agent's JSON contract still matches the model the code validates
against. `make evals` gates at 100%, and an empty suite never passes.

When a run fails in an interesting way, **freeze it**:

```bash
.venv/bin/factory evals capture 23 governance-block-on-undeclared-file
```

It becomes a permanent, free regression case. That is the whole
"every incident becomes an eval" loop, and it costs nothing to re-run.

---

## Working practices that matter

**One story at a time, small.** `gate-1` rejects a story sliced into more than 6
tasks. "Add search to bookmarks" works; "build me a SaaS" does not.

**Invest in `project-spec.json`.** It is injected into every agent and it is what
stops them inventing technology. The `forbidden` list is the highest-leverage field
in the whole factory. Generate a starting point with
`factory spec init <slug> --stack fastapi`; `fastapi` is currently the only template,
so other stacks mean writing the JSON by hand — worth the hour.

**Read the trust package, not the diff.** If you find yourself reading every line,
the factory is failing at its actual job; tighten the project spec or
[`REVIEW.md`](agents/policies/REVIEW.md) instead.

**Tune review in one place.** [`agents/policies/REVIEW.md`](agents/policies/REVIEW.md)
holds the tester's passes, severity ladder, skip list and nit cap. Editing it changes
review behaviour without touching an agent definition — then run `make evals`.

---

## Known limits — read before trusting it unattended

1. **Test bodies are not executed by default.** `gate-build` compiles and collects.
   Opt in with `FACTORY_RUN_TESTS=1`, but understand what that means: AI-generated
   code runs on your machine in a subprocess, with no hardened sandbox. Without it,
   the trust package reports `tests.executed: false` and withholds sign-off.
2. **The $1-per-task budget does not bind.** `agent_logs.cost_usd` is NULL in every
   row ever written — opencode's usage events are not being harvested — so only the
   2-attempt cap is limiting the loop. `factory metrics` reports this under
   *NOT MEASURABLE* rather than printing a reassuring `$0.00`. Don't leave long runs
   unattended.
3. **It commits straight onto the generated repo's current branch.** No branch per
   story, no PR. Separation of duties is currently *you reading the trust package*.
4. **There is no Checkpoint 3 (release sign-off).** `gate-release` is not built. You
   are the final gate.
5. **Verification (`src/factory/verification/`) covers Python, Go, JS and TS only.** Java and others need their own
   verification commands before the build gate means anything on those repos.
6. **One workspace per `$FACTORY_HOME`.** Point it elsewhere to keep separate
   histories (e.g. a scratch home for experiments); the factory never merges two.

The full built-vs-todo table, and the playbook plays deliberately **rejected** as
ceremony for a one-person team, are in
[EFFECTIVENESS.md §8–§9](docs/contract/EFFECTIVENESS.md).

---

## Documentation map

| Document | What it is |
|---|---|
| [EFFECTIVENESS.md](docs/contract/EFFECTIVENESS.md) | **the governing contract** — purpose, checkpoints, trust package, red flags |
| [GATES.md](docs/contract/GATES.md) | every gate, what it checks, what it blocks on |
| [AGENTS.md](docs/contract/AGENTS.md) | the agent roster and output contracts |
| [REVIEW.md](agents/policies/REVIEW.md) | the versioned review policy the tester applies |
| [REVIEW_QUEUE.md](docs/contract/REVIEW_QUEUE.md) | how work parks, how you are notified, resume semantics |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | the code layout, the one-way layering rule, where a new toolchain / gate / agent / command goes |
| [CLAUDE.md](CLAUDE.md) | for Claude Code working **on** the factory (not using it) |

---

## Design principles

- **Policy is deterministic Python, never delegated to an LLM.** Agents are
  replaceable executors; the pipeline and the gates own the governance.
- **A gate passing is not a checkpoint crossed.** A gate can pass and still require
  a human (`needs_human`).
- **Evidence is version-controlled**, not only rows in a database — the artifact
  chain and the ADRs live in git.
- **Every agent's verbatim input and output is stored**, so any run replays offline
  for free. That is what makes testing the orchestration affordable.
- **A malformed agent response is `blocked`, never guessed at.**
