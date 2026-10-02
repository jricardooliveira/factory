# Review Policy

The versioned policy the `tester-agent` reviews against — the AI-Native SDLC
playbook's Stage-5 `REVIEW.md`. It is **injected into the tester's prompt** by
`review_policy.load_review_policy()`, so editing this file changes review
behaviour. That makes it agent configuration: `make evals` is the regression net,
and a change here should be accompanied by an eval case.

Previously these passes lived only in prose inside `.opencode/agents/tester-agent.md`,
with the blocking severity threshold in a different file (`gates.py`) — two places
for one policy, free to drift. The agent definition now owns the *role and the JSON
contract*; this file owns *what to look for and what to ignore*.

---

## Review passes

Run all four. Each maps to a sub-verdict in the tester's output.

### 1. QA / acceptance coverage → `qa_verdict`

- Is **each acceptance criterion** verified? Every criterion must appear in either
  `ac_coverage` or `missing_coverage` — a criterion you do not mention at all is
  recorded as `unassessed` by `traceability.py` and reported as a blocker in the
  trust package, so silence is not neutral. *How* it is verified depends on its kind:
  - **Behavioural** — the system does something observable (an endpoint returns X,
    invalid input is rejected, a transition is refused). Must be exercised by a
    **test**. No test → `missing_coverage`.
  - **Structural or documentary** — something exists or is absent in the change
    (a dependency is/isn't declared, a README documents a command, a package is
    used, no secrets are committed). Verified by **reading the diff**. If the diff
    shows it, it is covered: put it in `ac_coverage`. Do NOT demand a unit test for
    "the README mentions the start command" — that produces absurd tests or an
    endless remediation loop, and proves nothing the diff doesn't already show.
- Are negative and edge cases covered: wrong input, missing resource, empty
  collection, and — where the project is access-scoped — **wrong role** and
  **wrong tenant**?
- For a bug fix, is there a **regression test that would have failed before**?

### 2. Security → `security_verdict` + `highest_severity`

- Secrets: not hard-coded, not logged, not in a fixture. (Credential-shaped file
  paths are already refused at materialization; this pass is about secrets in
  *content*.)
- Authorization is enforced **server-side**, not by a client or a hidden field.
- Tenant-scoped queries derive the tenant from the auth context, never from
  untrusted request data.
- No obvious injection: string-built SQL, shell interpolation, unescaped templates.
- Deserialization of untrusted input, path handling from user input.

### 3. Performance → `performance_verdict`

- N+1 queries; a query inside a loop.
- Unbounded reads: a list endpoint with no pagination, `SELECT *` over a growing
  table, loading a whole file into memory.
- A missing index on a column the new code filters or joins on.

### 4. Compliance vs. the committed artifacts → folded into `qa_verdict`

The chain is on disk; use it. In `docs/work/<story>/`:

- **SPEC.md** — does the implementation answer *this* story, including its
  **non-goals**? Work that satisfies no acceptance criterion is scope creep, even
  if it is good code.
- **PLAN.md** — does the diff stay within *Files that change*? A file outside the
  declared scope is reported by the trust package; call it out here too.
- **ADR** — does the implementation follow the recorded decision, or quietly work
  around it? A design worked around is worse than a design changed.

---

## Severity ladder

Mirrors the blocking thresholds in `gates.gate_after_tester` — **keep these in
step; if they disagree, one of them is wrong.**

| Severity | Means | Effect |
|---|---|---|
| `critical` | Exploitable now, or data loss / corruption | `security_verdict: fail` → run blocked |
| `high` | Exploitable with a plausible precondition; authz or tenant boundary crossed | `security_verdict: fail` → run blocked |
| `medium` | Real defect, bounded blast radius; a missing negative test | `warn` — reported, does not block |
| `low` | Hardening, defence-in-depth, unclear-but-suspect | `warn` |
| nit | Style, naming, formatting | **Do not report.** See the cap below. |

A missing test for a **behavioural** acceptance criterion is `qa_verdict: fail`
regardless of severity — coverage is the first trust artifact (EFFECTIVENESS §5.1).
A structural or documentary criterion that the diff visibly satisfies is covered;
one the diff visibly fails (the forbidden dependency IS there, the README does NOT
mention the command) is `missing_coverage` and fails QA the same way.

> Changed after a live run: the previous wording ("each criterion must be exercised
> by a test") made the tester fail a correct, compiling skeleton because "README
> documents the start command" and "uses log/slog" had no unit tests — while its
> own summary said the implementation was aligned with the story.

---

## What to skip

Reporting these dilutes the findings that matter and trains the operator to skim.

- **Style and formatting.** Line length, quote style, import order, trailing
  commas. `ruff` owns this, not you.
- **Naming preferences** where the existing name is clear and consistent with the
  surrounding module.
- **Missing docstrings** on private helpers.
- **Type annotations** on obvious locals.
- **Pre-existing issues outside the diff.** The factory's No Silent Fix rule cuts
  both ways: don't fix them, and don't bill them to this story. If one is serious,
  state it once as a follow-up, not as a finding against this change.
- **Speculative future scale.** "This won't work at a million rows" is only a
  finding if the project spec's NFRs say a million rows.
- **Generated and vendored paths**, lockfiles, migrations that were reviewed as
  part of the architecture.
- **Test-file style.** Tests are judged on what they prove, not how they read.

---

## Nit cap

**At most 3 non-blocking (`low`) findings per review.** Beyond that, drop the
weakest. A review with twenty low findings and one critical one hides the critical
one, and the whole point of this factory is *trust per interruption*: every finding
the operator reads and discards is interruption spent for nothing.

---

## Tuning

When a finding class proves consistently useless, delete it from the passes above
rather than learning to ignore it. When an escaped defect is found, add the class
that would have caught it — and add an eval case (`factory evals capture <run_id>`)
so the improvement is pinned rather than remembered.
