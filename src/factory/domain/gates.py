"""Gate logic — automatic checks between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field

from factory.domain.ambiguity import is_bound, unbound_criteria
from factory.domain.contracts import (
    ArchitectOutput,
    BoundaryOutput,
    CoderOutput,
    ReleaseOutput,
    SpecOutput,
    TesterOutput,
)
from factory.domain.task_order import dependency_problems


@dataclass
class GateResult:
    gate: str
    passed: bool
    reason: str
    needs_human: bool = False
    human_questions: list[str] = field(default_factory=list)


# ── Configurable thresholds ───────────────────────────────────────

MAX_TASKS_PER_STORY = 6
MAX_MODULES_PER_STORY = 12

# ── Remediation budget (EFFECTIVENESS.md §4): fail-fast, then queue ──
# A task may be re-attempted up to MAX_CODER_ATTEMPTS times, and no model call is
# made once the user story has spent MAX_STORY_COST_USD (estimated at API list
# prices from recorded tokens — see domain/budget.py). Operator decision, 2026-10-02.
MAX_CODER_ATTEMPTS = 2
MAX_STORY_COST_USD = 10.0

# How many times a tester (QA/security) failure may route back to the coder for a
# remediation pass before the run is parked for the human. Bounded like the build
# loop so tester findings don't dead-end, but also don't loop forever.
MAX_TESTER_REMEDIATIONS = 1

# How many times the coder may bounce an infeasible design back to the architect
# (coder → architect feedback) before the run is parked for the human.
MAX_REARCHITECT_LOOPS = 1

# How many times a FAILED boundary review sends the design back to the architect
# (with the reviewer's required changes) before gate-2 rejects it.
MAX_BOUNDARY_REDESIGNS = 1


# Ambiguity detection (THRESHOLD_TERMS, unbound_criteria, ...) lives in
# factory.domain.ambiguity; gate 1 below applies it.


# ── Gate 1: After spec-agent ──────────────────────────────────────

def gate_after_spec(
    spec: SpecOutput,
    defined_terms: frozenset[str] = frozenset(),
    *,
    request: str = "",
) -> GateResult:
    """Validates spec-agent output before architect-agent.

    `defined_terms` are threshold words already settled in the project's
    committed context, so a later story does not re-ask a question a prior ADR
    already answered.
    """
    failures: list[str] = []

    # `verdict != "pass"` is only a rejection when the agent has nothing to ASK.
    # `spec-agent.md` tells the agent: "If the request is ambiguous, set
    # verdict: 'fail' and put your questions in a questions array" — so a failing
    # verdict WITH questions is the agent honouring its ambiguity contract, and
    # must route to Checkpoint 1 rather than be discarded as a malformed story.
    # Checking the verdict first is how a live Story-10 run threw away four
    # correct product questions and reported "Spec verdict is 'fail'".
    if spec.verdict != "pass" and not spec.questions:
        failures.append(f"Spec verdict is '{spec.verdict}', not 'pass'")

    if not spec.title:
        failures.append("Story has no title")

    if len(spec.acceptance_criteria) < 2:
        failures.append(
            f"Need at least 2 acceptance criteria, got {len(spec.acceptance_criteria)}"
        )

    if not spec.tasks:
        failures.append("No tasks defined")

    # Budget check: too many tasks = scope explosion
    if len(spec.tasks) > MAX_TASKS_PER_STORY:
        failures.append(
            f"Story has {len(spec.tasks)} tasks (max {MAX_TASKS_PER_STORY}). "
            "Split into multiple stories."
        )

    # An unexecutable task graph: the coder would build a task before the one it
    # depends on (order_tasks forgives bad data so that a run never crashes).
    failures.extend(f"Task graph: {p}" for p in dependency_problems(spec.tasks))

    # A STRUCTURAL problem is a rejection: there is nothing coherent for the
    # operator to sign off on, so it must not be laundered into a checkpoint.
    if failures:
        return GateResult(
            gate="gate-1-spec",
            passed=False,
            reason="; ".join(failures),
        )

    reason = (
        f"Story '{spec.title}' has {len(spec.acceptance_criteria)} AC "
        f"and {len(spec.tasks)} tasks"
    )

    # ── Checkpoint 1 ──────────────────────────────────────────────────
    # Two independent triggers, combined into one park so the operator answers
    # everything in a single interruption:
    #   (a) the agent's own questions — its documented ambiguity contract;
    #   (b) a DETERMINISTIC check for a criterion that needs a number the story
    #       never supplied. (b) exists because (a) proved to be a coin flip.
    human_questions: list[str] = []

    if spec.questions:
        human_questions.append(
            "❓ THE SPEC-AGENT NEEDS A DECISION before this story can be designed:\n"
            + "\n".join(f"  • {q}" for q in spec.questions)
            + "\n  → Approve to proceed with the story as written, or reject with "
            "the answers and it will be re-specified."
        )

    unbound = unbound_criteria(
        spec.acceptance_criteria, request=request, defined_terms=defined_terms
    )
    if unbound:
        human_questions.append(
            "📐 UNAUTHORISED THRESHOLD — your request used a term that only means "
            "something once a number is attached, and never gave the number. These "
            "criteria either leave it undefined or fill it in with a value you "
            "never approved:\n"
            + "\n".join(
                f"  • '{term}' — {'the story INVENTED a value your request never supplied' if is_bound(crit) else 'still undefined'}"
                f" in: {crit}"
                for crit, term in unbound
            )
            + "\n  → Reject with the definition (e.g. \"overdue = HIGH, not CLOSED, "
            "created >24h ago\"), or approve to accept whatever the agents choose."
        )

    if human_questions:
        parts = []
        if spec.questions:
            parts.append(f"{len(spec.questions)} open question(s)")
        if unbound:
            parts.append(f"{len(unbound)} undefined threshold(s)")
        return GateResult(
            gate="gate-1-spec",
            passed=True,
            reason=f"{reason}. ⏸️ NEEDS HUMAN APPROVAL ({', '.join(parts)})",
            needs_human=True,
            human_questions=human_questions,
        )

    return GateResult(gate="gate-1-spec", passed=True, reason=reason)


# ── Gate 2: After architect-agent ─────────────────────────────────

def gate_after_architect(
    architect: ArchitectOutput,
    boundary: BoundaryOutput | None = None,
    *,
    boundary_unavailable: bool = False,
) -> GateResult:
    """Validates architect-agent output (and its boundary review) before coder-agent.

    This gate checks both automatic pass/fail conditions AND whether
    human approval is needed before proceeding. `boundary` is the pre-implementation
    boundary review when the design required one; `boundary_unavailable` means it
    was required but could not be completed live — the operator decides.
    """
    failures: list[str] = []
    human_questions: list[str] = []
    boundary_verdict = boundary_overall(boundary) if boundary is not None else None
    if boundary_verdict == "fail":
        failures.append(
            "Boundary review failed — " + "; ".join(boundary_findings(boundary, "fail"))
        )

    if architect.verdict == "fail":
        failures.append("Architect verdict is 'fail'")

    if not architect.architecture_notes:
        failures.append("No architecture notes provided")

    if not architect.modules_affected:
        failures.append("No affected modules listed")

    # Budget check: too many modules
    if len(architect.modules_affected) > MAX_MODULES_PER_STORY:
        failures.append(
            f"Architecture touches {len(architect.modules_affected)} modules "
            f"(max {MAX_MODULES_PER_STORY}). Story scope is too large — split it."
        )

    if failures:
        return GateResult(
            gate="gate-2-architect",
            passed=False,
            reason="; ".join(failures),
        )

    # ── Human approval checks (gate passes but needs sign-off) ────

    # Breaking changes need human approval — the architect's and the boundary review's.
    breaking = list(architect.breaking_changes)
    if boundary is not None:
        breaking += [b for b in boundary.api_contract.breaking_changes if b not in breaking]
    if breaking:
        human_questions.append(
            f"⚠️ BREAKING CHANGES detected — these will affect existing API consumers:\n"
            + "\n".join(f"  • {bc}" for bc in breaking)
            + "\n  → Approve these breaking changes?"
        )

    if boundary_verdict == "warn":
        human_questions.append(
            "🧱 BOUNDARY REVIEW WARNINGS — tenant / authorization / API contract / security "
            "concerns found before any code is written:\n"
            + "\n".join(f"  • {f}" for f in boundary_findings(boundary, "warn"))
            + "\n  → Approve the design with these boundary risks, or reject with the fix?"
        )
    if boundary_unavailable:
        reasons = boundary_review_reasons(architect)
        human_questions.append(
            "🧱 BOUNDARY REVIEW MISSING — this design needs one ("
            + "; ".join(reasons)
            + ") but the review could not be completed.\n"
            "  → Approve to implement without a boundary review, or reject to redesign?"
        )

    # External dependencies need human confirmation
    if architect.external_dependencies:
        human_questions.append(
            f"🔗 EXTERNAL DEPENDENCIES required before implementation:\n"
            + "\n".join(f"  • {dep}" for dep in architect.external_dependencies)
            + "\n  → Confirm these are available and configured?"
        )

    # Sensitive work needs human review
    if architect.sensitivity:
        tags = ", ".join(architect.sensitivity)
        reviewed = ""
        if boundary is not None:
            # The park is the operator's policy; the boundary review's answer sits beside it.
            reviewed = "  ◦ The boundary review judged: " + ", ".join(
                f"{d}: {getattr(boundary, d).verdict}" for d in _BOUNDARY_DIMENSIONS
            ) + "\n"
        human_questions.append(
            f"🛡️ SENSITIVE WORK detected [{tags}] — this may require domain expertise "
            f"(legal review, security audit, compliance check) that agents cannot provide.\n"
            f"{reviewed}"
            f"  → Confirm you accept the risk of agent-generated {tags} logic?"
        )

    warn = " (with warnings)" if architect.verdict == "warn" else ""
    needs_human = len(human_questions) > 0

    reason = f"Architecture approved{warn}. {len(architect.modules_affected)} modules, {len(architect.risks)} risks flagged"
    if needs_human:
        reason += f". ⏸️ NEEDS HUMAN APPROVAL ({len(human_questions)} items)"

    return GateResult(
        gate="gate-2-architect",
        passed=True,
        reason=reason,
        needs_human=needs_human,
        human_questions=human_questions,
    )


# ── Gate 6: After tester-agent ────────────────────────────────────

def gate_after_tester(tester: TesterOutput) -> GateResult:
    """Blocking quality gate: QA coverage, security severity, performance."""
    failures: list[str] = []

    if tester.overall == "fail":
        failures.append("Tester overall verdict is 'fail'")
    if tester.qa_verdict == "fail":
        failures.append(f"QA failed; missing coverage: {tester.missing_coverage or '[unspecified]'}")
    if tester.highest_severity in ("high", "critical") or tester.security_verdict == "fail":
        failures.append(f"Security: {tester.highest_severity} findings: {tester.security_findings}")
    if tester.performance_verdict == "fail":
        failures.append(f"Performance regression: {tester.performance_findings}")

    if failures:
        return GateResult(gate="gate-test", passed=False, reason="; ".join(failures))

    warn = " (with warnings)" if "warn" in (
        tester.overall, tester.qa_verdict, tester.security_verdict, tester.performance_verdict
    ) else ""
    return GateResult(
        gate="gate-test",
        passed=True,
        reason=f"QA/security/performance passed{warn}. AC covered: {len(tester.ac_coverage)}",
    )


# ── Gate 7: release readiness → Checkpoint 3 ──────────────────────

_NO = ("", "no", "none", "n/a", "not_applicable")


def _says_yes(value: str) -> bool:
    """Free-text fields ("yes — adds a table", "None.") — anything but a "no…" is yes."""
    text = (value or "").strip().lower().rstrip(".!")
    return text not in _NO and not text.startswith("no ") and not text.startswith("no,")


def changes_schema(architect: ArchitectOutput | None) -> bool:
    return bool(architect) and (
        _says_yes(architect.db_impact) or _says_yes(architect.migration_needed)
    )


def gate_after_release(
    evidence_blockers: list[str],
    notes: ReleaseOutput | None,
    architect: ArchitectOutput | None,
) -> GateResult:
    """Is the release ready for sign-off? ALWAYS parks for the operator (Checkpoint 3).

    `passed` means the evidence bar is met; it never means "released". No agent
    may pass the release gate (GATES.md), so even a fully green release waits
    for a human, and an unready one is offered with every gap named — approving
    it is the operator accepting those gaps, recorded as such by the boss.
    """
    gaps = list(evidence_blockers)
    notices: list[str] = []
    if notes is None:
        gaps.append("Release notes were not written (the release-agent produced no usable notes)")
    else:
        if not notes.summary.strip():
            gaps.append("The release notes have no summary of what changed")
        if changes_schema(architect) and not _says_yes(notes.migration_notes):
            gaps.append("Migration notes are missing, but the design changes the database")
        if (changes_schema(architect) or (architect and architect.breaking_changes)) and (
            not notes.rollback_notes.strip()
        ):
            gaps.append(
                "Rollback notes are missing, but the change alters the schema or breaks an API"
            )
        if notes.verdict == "fail":
            gaps += [f"Release-agent concern: {c}" for c in notes.concerns] or [
                "The release-agent raised a blocking concern without detail"
            ]
        elif notes.concerns:
            notices += [f"Release-agent note: {c}" for c in notes.concerns]

    ready = not gaps
    lines = ["🚀 RELEASE SIGN-OFF (Checkpoint 3) — " + (
        "READY: every evidence bar is met." if ready
        else f"NOT READY: {len(gaps)} gap(s) in the evidence:"
    )]
    lines += [f"  • {g}" for g in gaps]
    lines += [f"  ◦ {n}" for n in notices]
    lines.append(
        "  → Approve to release" + (" accepting the gaps above as a known risk" if gaps else "")
        + ", or reject with what must change — it goes back to the coder, then the tester."
    )
    reason = (
        "READY for release sign-off" if ready else f"NOT READY for release ({len(gaps)} gap(s))"
    ) + ". ⏸️ NEEDS HUMAN APPROVAL (release sign-off)"
    return GateResult(
        gate="gate-release",
        passed=ready,
        reason=reason,
        needs_human=True,
        human_questions=["\n".join(lines)],
    )


# ── Gate 4: the boundary review (runs inside gate-2's decision) ───

_BOUNDARY_DIMENSIONS = ("tenant", "authorization", "api_contract", "security")


def boundary_review_reasons(architect: ArchitectOutput) -> list[str]:
    """Why this design needs a pre-implementation boundary review ([] = it does not).

    Decided from the architect's own declarations, so a trivial story never pays
    for a review and a boundary-touching one never skips it.
    """
    reasons: list[str] = []
    if _says_yes(architect.api_impact):
        reasons.append("it changes an API")
    if _says_yes(architect.db_impact) or _says_yes(architect.migration_needed):
        reasons.append("it changes the database")
    if architect.breaking_changes:
        reasons.append("it declares breaking changes")
    if architect.sensitivity:
        reasons.append(f"it touches sensitive areas ({', '.join(architect.sensitivity)})")
    return reasons


def boundary_overall(review: BoundaryOutput) -> str:
    """The review's real verdict: any failed dimension fails it, any warning warns.

    The brief: "if any required sub-verdict is fail, the overall verdict must be
    fail" — enforced here, never taken from the agent's own `overall`.
    """
    verdicts = [getattr(review, d).verdict for d in _BOUNDARY_DIMENSIONS] + [review.overall]
    if "fail" in verdicts:
        return "fail"
    if "warn" in verdicts:
        return "warn"
    return "pass"


def boundary_findings(review: BoundaryOutput, level: str) -> list[str]:
    """`dimension: finding` for every dimension at `level` (plus required changes on fail)."""
    out: list[str] = []
    for dim in _BOUNDARY_DIMENSIONS:
        sub = getattr(review, dim)
        if sub.verdict == level:
            out += [f"{dim}: {f}" for f in sub.findings] or [f"{dim}: {level} (no detail)"]
    if level == "fail":
        out += [f"required change: {c}" for c in review.required_changes]
    return out or [f"the reviewer's overall verdict is {level}"]
