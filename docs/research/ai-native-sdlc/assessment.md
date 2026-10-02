# Factory assessment against the AI-native SDLC playbook

**Reviewed revision:** `de5fe873ec4baece49035f1b6c11139d695aa7aa`  
**Date:** 2026-10-02  
**Scope:** evaluation and research documentation only; recommendations below are not implemented.

**Version boundary:** release-agent/checkpoint work began in another session after this snapshot. It is unreviewed here and may address some findings. See [concurrent-work details](evidence.md#reviewed-state) before applying this assessment to the current working tree.

## Verdict

The factory has a substantial, tested foundation for governed code generation. Claude's adaptation goes beyond adding agent prompts: it includes deterministic gates, recorded authorizations, bounded remediation, persisted decisions, measured diffs, replay, configuration checks, and an operator workflow.

It is **not yet a complete, trustworthy delivery loop**. Its strongest guarantees concern the sequence of work and the shape of agent answers. Its weakest guarantees concern whether a particular product revision actually works, whether the evidence describes that revision accurately, and whether an approved change is the only change that can proceed.

In simple terms: the factory can reliably check that the paperwork passed through the right stations. It still needs stronger proof that the finished product works and is ready for release.

This assessment is informed by the [source notes](README.md). All repository findings below are my analysis of code or local reproductions. The [evidence report](evidence.md) records confidence and limitations.

## What I verified

The stable reviewed copy passed **619 tests**, **10/10 simulations**, and **44/44 eval checks**. Those are meaningful results for the factory's existing contracts. Additional experiments confirmed gaps that those checks do not currently reject:

- A run can complete with no executed product tests.
- A completed run's saved trust package can still report `running`.
- Python test collection executes import-time code even when test execution is disabled.
- Unsupported file extensions produce a passing verification verdict containing only a skipped check.
- Missing cost data is returned as `0.0` by the budget accessor.

I did not call live models, deploy anything, inspect private product data, or verify GitHub branch protections. Passing these offline checks does not establish current model quality or production readiness.

## How the factory actually works

| Step | Actual responsibility | Main code |
|---|---|---|
| Intake | CLI/TUI selects a project, records a story/run, resolves workspace and baseline. | [runs/service.py](../../../src/factory/runs/service.py), [workspace/](../../../src/factory/workspace/) |
| Specification | Spec agent translates the request into acceptance criteria and tasks. Gate 1 checks structural quality, unresolved ambiguity, and task dependencies. It can park for an operator. | [nodes/spec.py](../../../src/factory/pipeline/nodes/spec.py), [domain/gates.py](../../../src/factory/domain/gates.py) |
| Authorization | Before a downstream agent runs, the boss checks recorded gate decisions and required inputs. Refusal is persisted as a blocked run. | [pipeline/boss.py](../../../src/factory/pipeline/boss.py), [domain/authorization.py](../../../src/factory/domain/authorization.py) |
| Design | Architect receives project context, prior decisions, and an interface inventory. It returns structured design notes. Gate 2 checks them and can ask about reported risks. | [nodes/architect.py](../../../src/factory/pipeline/nodes/architect.py), [prompts/](../../../src/factory/pipeline/prompts/) |
| Build | Coder returns complete file contents as JSON. The factory validates paths, writes files, measures changed paths, runs supported checks, and checkpoints tasks. Failures feed bounded retry or redesign. | [nodes/coder.py](../../../src/factory/pipeline/nodes/coder.py), [materialize.py](../../../src/factory/workspace/materialize.py), [verification/](../../../src/factory/verification/) |
| Review | A separate tester call receives the spec, architecture, review policy, build result, and a real cumulative diff where available. Gate-test applies verdict and severity rules; limited remediation is possible. | [prompts/tester.py](../../../src/factory/pipeline/prompts/tester.py), [nodes/gates.py](../../../src/factory/pipeline/nodes/gates.py) |
| Evidence | SQLite stores calls, gates, approvals, and authorizations. Product repos receive intent/spec/plan, ADRs, a pipeline record, and a trust package on completion. | [state/db.py](../../../src/factory/state/db.py), [evidence/](../../../src/factory/evidence/), [evidence_writers.py](../../../src/factory/pipeline/evidence_writers.py) |
| Replay and diagnostics | Frozen answers drive the real graph without an LLM. Project replays use scratch clones. Simulations and config checks exercise orchestration and configuration invariants. | [selftest/](../../../src/factory/selftest/), [sandbox.py](../../../src/factory/workspace/sandbox.py) |

The runtime boundary is [adapters/opencode.py](../../../src/factory/adapters/opencode.py): the graph calls OpenCode, which returns structured agent output. LangGraph controls routing; the model is not the final authority for every transition.

### Two different control systems must stay distinct

| Developing the factory itself | Running the factory on a product |
|---|---|
| `CLAUDE.md`, `.claude/skills/`, `.claude/hooks/` | `agents/*.md`, prompts, boss, gates, materializer, verification, OpenCode adapter |
| `make check` and `.github/workflows/check.yml` | Per-run build/review results and the product's release evidence |
| Protects the factory development workflow where those tools honor the configuration | Must protect generated product work regardless of which assistant developed the factory |

A Claude Code hook does not automatically constrain an OpenCode subprocess. Likewise, hundreds of passing factory tests do not mean the generated product's tests have run.

## Practices already represented well

- **Deterministic orchestration:** typed outputs, clear graph transitions, explicit failure/park states, and a boss that refuses calls without the required recorded decisions.
- **Useful new boss implementation:** the reviewed commit rejects task cycles, missing dependency references, and duplicate task IDs; logs authorization separately from output gates; and commits `PIPELINE.md` at stops. These are implemented, not recommendations to build again.
- **Human ambiguity handling:** operator answers and approved decisions can settle thresholds; a merely proposed ADR cannot establish an authoritative business rule.
- **Bounded feedback:** build errors and reviewer findings return to the coder with attempt limits. Design feedback has a distinct route back to architecture.
- **Persistent evidence and memory:** original intent, specs, plans, ADRs, run logs, and project rules have concrete storage paths. Replay can help diagnose a historical orchestration decision.
- **Independent review context:** the tester has a separate call and a versioned [review policy](../../../agents/policies/REVIEW.md), including severity handling.
- **Cheap regression protection:** a single local verification target, CI, simulations, config invariants, and incident capture make factory development reviewable.
- **Appropriate restraint for a solo factory:** postponing statistical control bands, deployment automation, and concurrent coders is sensible. Reliability is the current constraint.

## Coverage across the lifecycle

These are qualitative assessments, not invented maturity scores.

| Practice | Current coverage | Main remaining gap |
|---|---|---|
| Intent and requirements | Substantial | Explicit policy for when human acceptance is required; bind acceptance to an artifact version. |
| Design and plan | Substantial | Adequate existing-code context, enforced task scope, and approval of changed plans. |
| Repository instructions and skills | Present for factory development | Keep runtime controls distinct; test behavioral effects of prompt/skill changes. |
| Parallel agents | Deliberately limited | Isolated branches/worktrees and review capacity before concurrent writes. |
| Feedback loop | Partial | Isolated execution of real product checks, supported stack commands, preserved test oracle. |
| Continuous evals | Strong offline regression layer | Current-model behavioral measurement is absent from the eval path inspected. |
| AI review | Useful in-process implementation | Candidate-scoped review evidence and PR/human merge authorization. |
| Approval enforcement | Stronger stage-entry checks | Scope and exact revision binding; release authorization; runtime isolation. |
| CI/CD | Factory CI exists | Product CI/release handoff, branch policy verification, eventual deployment/rollback. |
| Maintenance loop | Metrics and incident capture foundations | Trigger → triaged proposal → approved fix → lasting regression case. |

## Findings and improvements

### F1 — Completion and release evidence disagree

**Priority: first. Confidence: reproduced and code-confirmed.**

In `node_gate_test`, a passing tester result marks the story and run completed. Product test execution, an enforceable release checkpoint, and successful persistence of release evidence are not prerequisites. The package can report `verdict: pass` while also carrying blockers. This distinction is acknowledged in [EFFECTIVENESS.md](../../contract/EFFECTIVENESS.md), where gate-release is still planned.

There is also a concrete ordering defect: `node_gate_test` calls `write_trust_package` **before committing** its database transaction. The writer assembles evidence through another database connection. In the reproduction, the run ended completed but the committed JSON still said `running`; assembling after completion returned `pass`.

Evidence quality needs additional work:

- `tests.ac_coverage[].covered_by` is always empty. The current cross-check detects omitted criterion names, not which executed test proved each criterion.
- `_test_execution` reads string markers across historical gate records. A failed attempt followed by a successful retry remains failed; a pass followed by a skipped check can remain passed. Neither is a reliable verdict for the final candidate revision.
- `_blockers` does not enforce every claimed evidence requirement: for example, it does not check the ADR path. `validate()` is not called by the trust-package writer.
- Diff assembly uses the repository's current state and a start baseline; no immutable end revision is captured by that package. Reassembling old evidence after later work can change its meaning.
- Evidence-writing failures are best effort. That can be reasonable during intermediate work, but final readiness must explicitly reject missing required evidence.

**Recommended outcome:** distinguish “implementation finished,” “ready for review,” “release approved,” and eventually “released.” Produce one validated package for a pinned candidate commit after final state is visible. Require exact test commands, results, test counts, criterion-to-test references, measured diff, decision records, and unresolved findings. Preserve earlier attempts as history without using them as the final candidate's verdict. A persistence or validation failure should prevent readiness and explain why.

**Acceptance proof:** a passing tester cannot make an untested candidate release-ready; the saved package agrees with final DB state; a later story cannot change old evidence; required missing evidence blocks readiness; successful remediation is judged against the final revision.

Evidence: [nodes/gates.py](../../../src/factory/pipeline/nodes/gates.py), [evidence_writers.py](../../../src/factory/pipeline/evidence_writers.py), [trust_package.py](../../../src/factory/evidence/trust_package.py), [git.py](../../../src/factory/workspace/git.py).

### F2 — Verification needs both real tests and real isolation

**Priority: first, alongside F1. Confidence: reproduced and code-confirmed.**

Python normally compiles changed files and, where tests exist, collects them. Actual test bodies are opt-in. Go has build/vet and opt-in tests; JS/TS verification does not provide equivalent test execution. Changed-file extensions select checks, so a configuration-only change or an unsupported source type can get only `verify:skip`, with overall verdict `pass`.

The default Python path is also less isolated than its comment claims. `pytest --collect-only` imports `conftest.py` and test modules. A harmless reproduction wrote a marker file during collection with `FACTORY_RUN_TESTS=0`. Disabling test bodies does not prevent product code execution.

Verification subprocesses inherit their environment and are not wrapped in an OS-level sandbox. The OpenCode command includes `--dangerously-skip-permissions`. Agent definitions disable four write/execute tools, which is useful, but those configuration checks are not evidence of a complete runtime sandbox or an exhaustive effective-tool allowlist. A replay clone isolates product history; it is not a security boundary around the host.

**Recommended outcome:** define an operator-approved verification profile per product: dependency installation, build, lint/type checks, tests, and browser checks where relevant. Execute commands in a disposable environment with minimal credentials, bounded filesystem/network access, timeouts, and controlled dependencies. Run the project's environment rather than relying on the factory's Python environment. Treat a missing required tool or unsupported profile as “verification incomplete,” with an explicit exception route.

**Acceptance proof:** import-time code cannot write outside the disposable workspace or see a planted host secret; a source regression fails real tests; a dependency/config-only change runs the relevant checks; missing tools and zero-test runs cannot silently satisfy a required test stage.

Evidence: [verification/__init__.py](../../../src/factory/verification/__init__.py), [base.py](../../../src/factory/verification/base.py), [python.py](../../../src/factory/verification/python.py), [go.py](../../../src/factory/verification/go.py), [typescript.py](../../../src/factory/verification/typescript.py), [opencode.py](../../../src/factory/adapters/opencode.py).

### F3 — Existing-code context is insufficient for safe full-file edits

**Priority: high before serious existing-project work. Confidence: code-confirmed risk; no destructive live run attempted.**

The inventory provides Python signatures and class interfaces, filenames for JS/TS, and no Go files. It is bounded to 60 files and 6,000 characters. The coder instruction prohibits reading source files directly and asks it to return complete file contents. The materializer writes the returned content for both `create` and `modify`.

Consequently, a normal coder task can be asked to replace an existing implementation without seeing its body. An interface such as `calculate_total(order)` does not reveal discounts, rounding, or existing edge cases. A syntactically valid replacement can erase them. Remediation's diff provides some additional context but does not solve the normal edit path.

The tester also receives a bounded cumulative diff; `collect_repo_diff` starts at the earliest factory commit, not necessarily this story's base. Over time, older changes can consume the 16,000-character review budget.

**Recommended outcome:** retain the compact inventory for discovery, then retrieve full relevant files and tests under an explicit read policy. Support the languages actually offered. Use patches or full-file replacements guarded by an expected prior-content hash. If relevant context cannot fit, stop or split the task. Review the current story's pinned diff and make omitted context explicit.

**Acceptance proof:** a task changes one behavior in a pre-existing module while preserving unrelated behavior; omitted context causes a clear stop; stale file hashes reject writes; Go modules appear in discovery; the reviewer sees all changes for the current candidate.

Evidence: [repo_map.py](../../../src/factory/workspace/repo_map.py), [context_pack.py](../../../src/factory/pipeline/prompts/context_pack.py), [coder-agent.md](../../../agents/coder-agent.md), [materialize.py](../../../src/factory/workspace/materialize.py), [prompts/tester.py](../../../src/factory/pipeline/prompts/tester.py).

### F4 — Offline evals cannot measure changes in model answer quality

**Priority: high before tuning prompts or changing models. Confidence: code-confirmed.**

`factory evals` runs configuration invariants and frozen-answer replays. This is valuable: it catches incompatible contracts, enabled forbidden tools, and broken routing. But replay supplies yesterday's answer even if today's prompt would produce a worse one. The scheduled workflow's comment about detecting model drift overstates what that job measures.

The adapter and preflight can establish reachability, but that is also different from task quality. The [course's eval lesson](https://academy.claude.com/courses/ai-native-sdlc-playbook/continuous-evals-in-ci) describes exercising current agent behavior, which is the missing layer here.

**Recommended outcome:** keep the zero-cost suite and name its scope accurately. Add a separate, explicitly budgeted suite that runs current agent configurations against disposable product fixtures. Start with a small, varied corpus, then expand as failures teach you what matters. Include existing-code edits, ambiguity, security boundaries, malformed outputs, unsupported environments, and attempts to weaken tests. Compare candidate and baseline configurations; repeat cases enough to expose variability. Record model, complete configuration fingerprint, outcome, cost coverage, and artifacts.

**Acceptance proof:** deliberately worsening a prompt produces a measurable regression on a representative task even though frozen replays still pass. Configuration changes cannot silently bypass the behavioral review policy.

Evidence: [selftest/evals/](../../../src/factory/selftest/evals/), [agent_calls.py](../../../src/factory/pipeline/agent_calls.py), [check.yml](../../../.github/workflows/check.yml).

### F5 — Stage authorization needs scope, version, and test-integrity guarantees

**Priority: high. Confidence: code-confirmed.**

The new boss is a good foundation, but it deliberately permits tasks with missing scope or completion evidence and logs warnings. Human checkpoints are conditional: an unambiguous spec and a design without the reported risk flags can progress automatically. That may be a sensible autonomy choice, but it should not be described as unconditional human sign-off.

Authorization uses recorded gate names/results and human responses; it is not bound to hashes of the approved spec, plan, rules, and candidate. The task-scope check records violations in release evidence rather than always blocking the write. Test paths and some manifest files are exempt from that scope policy. There is no distinction between adding a new test and weakening a previously approved regression test.

**Recommended outcome:** define a small per-project autonomy policy. Require usable scope and completion evidence before coding, then check proposed writes against approved scope before materialization. Record the approved artifact revision; material changes to it invalidate approval. Allow controlled additions to test coverage while protecting designated regression tests and requiring explicit review for expectation changes. Manifest changes should receive dependency/risk review rather than an unrestricted exception.

**Acceptance proof:** a disallowed path never gets written; changing an approved plan requires a new decision; a protected failing regression test cannot be removed or relaxed to manufacture success; low-risk automatic progress remains explicitly supported where the operator has chosen it.

Evidence: [authorization.py](../../../src/factory/domain/authorization.py), [boss.py](../../../src/factory/pipeline/boss.py), [domain/gates.py](../../../src/factory/domain/gates.py), [scope.py](../../../src/factory/verification/scope.py), [materialize.py](../../../src/factory/workspace/materialize.py).

### F6 — Unknown cost is treated as zero by the budget path

**Priority: high before increasing live workload. Confidence: reproduced for missing usage; actual provider coverage not measured.**

`get_run_cost()` uses `COALESCE(SUM(cost_usd), 0.0)`. A stored call with missing cost therefore contributes no measured spend. The adapter only recognizes a particular event shape and can return missing usage. Retry decisions compare aggregate run spend to a constant named as a task budget; this is neither a strict per-task budget nor a pre-call spending ceiling.

Attempt limits still provide a real bound on retries. The metrics code also exposes cost coverage, which is useful. However, neither makes a `$1` spending guarantee true when usage is absent. Comments claiming every historical cost is null were not independently verified against the operator's database.

**Recommended outcome:** represent complete, partial, and unknown usage separately. Choose an explicit policy for unavailable usage, such as an operator-set call/token allowance with a conservative reserve. Account per task and per run. Record intended and observed model, provider events needed for accounting, and an aggregate fingerprint of agent definition, prompts, rules, policy, and tier settings. If the provider cannot enforce a hard currency ceiling, label the budget as a soft limit.

**Acceptance proof:** missing usage never displays as confirmed zero spend; a call cannot start when its policy allowance is exhausted; retries count against the correct task; the report states how much spend is measured and what remains unknown.

Evidence: [state/db.py](../../../src/factory/state/db.py), [state/reports.py](../../../src/factory/state/reports.py), [opencode.py](../../../src/factory/adapters/opencode.py), [metrics.py](../../../src/factory/evidence/metrics.py), [nodes/coder.py](../../../src/factory/pipeline/nodes/coder.py).

### F7 — Product review and delivery need a controlled handoff

**Priority: next, after evidence and verification are dependable. Confidence: repository inspection; remote policy unverified.**

Product work is committed in the active product repository. There is no completed per-story branch → PR → human merge → release workflow in the inspected pipeline. `git_commit_all` stages all changes, so concurrent runs in the same repository would interfere. Separate replay clones do not isolate normal live story execution.

The factory has its own CI workflow. A workflow file alone does not establish required branch checks, reviewer approval, or product CI. The remote repository's actual protections were outside this review. There is no deployed-product health or rollback path to evaluate yet.

**Recommended outcome:** isolate a live story in its own branch/worktree and initially lock concurrent writes per project. Publish a PR containing the pinned plan, candidate diff, test results, and trust package. Have product CI independently reproduce required verification. Keep authoring credentials unable to approve their own release. Add deployment and rehearsed rollback only once release authorization has a concrete target.

**Acceptance proof:** two stories cannot stage each other's changes; a rejected candidate leaves the accepted product branch unchanged; required checks and approval block merging; release approval identifies exactly the revision and environment authorized.

Evidence: [runs/service.py](../../../src/factory/runs/service.py), [workspace/git.py](../../../src/factory/workspace/git.py), [workspace/sandbox.py](../../../src/factory/workspace/sandbox.py), [check.yml](../../../.github/workflows/check.yml), [EFFECTIVENESS.md](../../contract/EFFECTIVENESS.md).

### F8 — Make maintenance and documentation reflect measured guarantees

**Priority: incremental; begin after the first reliability fixes. Confidence: code and documentation inspection.**

Metrics currently describe factory runs, retries, gates, checkpoints, and cost coverage. They do not constitute an autonomous maintenance loop or measure released-product outcomes. Replays and simulations must be distinguishable from live delivery when interpreting totals. “No coder retry” measures retry incidence, not necessarily first-pass correctness or accepted delivery.

Some statements also outpace enforcement: the workflow still says no remote exists; scheduled frozen replay is described as detecting model drift; lint is optional in `make check` and absent from the CI steps inspected; schema validation exists but does not govern trust-package writing. The reviewed commit already fixed the old replay warning in `CLAUDE.md`, so that is not an outstanding finding.

**Recommended outcome:** use one status vocabulary and one verified command set across README, contracts, CLI, and generated evidence. Label each control as implemented, advisory, or planned. Define a few outcome metrics with explicit denominators: accepted changes per live run, operator review time, release-evidence completeness, measured usage coverage, and escaped defects once there are releases. Start maintenance with a deterministic trigger that opens a diagnosis/proposal record for human triage. Accepted incidents should create both product regression tests and appropriate factory/model eval cases.

**Acceptance proof:** a known failure produces one deduplicated proposal with evidence and ownership; resolving it links to a lasting regression case; dashboards distinguish live work from replay; documentation's claimed required checks match execution.

Evidence: [metrics.py](../../../src/factory/evidence/metrics.py), [state/reports.py](../../../src/factory/state/reports.py), [Makefile](../../../Makefile), [check.yml](../../../.github/workflows/check.yml), [EFFECTIVENESS.md](../../contract/EFFECTIVENESS.md).

## Proposed sequence, without committing to implementation

| Order | Reviewable increment | What it buys |
|---|---|---|
| 1 | Correct final trust-package state and candidate provenance; introduce an enforceable readiness decision. | Operators can trust what “done” means. |
| 2 | One complete isolated product verification profile, starting with the stack actually used most. | A green result proves behavior in a known environment. |
| 3 | Scoped existing-code retrieval, guarded edits, explicit autonomy policy, and protected regression tests. | Existing products can evolve without relying on blind full-file replacement. |
| 4 | Bounded current-model evals and usage coverage/budget policy. | Prompt/model changes can be compared on quality and cost. |
| 5 | Story isolation, PR evidence, required product CI, and release approval. | Working changes can enter the accepted branch through a reviewable boundary. |
| 6 | Operational signals, triaged proposals, incident regression cases; later deploy/rollback automation. | Failures improve the system instead of becoming repeated manual work. |

Before implementing, the operator should decide the first supported product stack, the desired automatic-versus-human approval policy, the live-eval allowance, and the release destination. These choices are not needed to accept the findings; they determine the smallest useful implementation.

I would preserve the existing architecture and the new deterministic boss. The next step is to strengthen the evidence and execution boundaries around them, not add more agent roles or rewrite the runtime.
