"""Gate logic — automatic checks between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field

from factory.domain.ambiguity import _is_bound, unbound_criteria
from factory.domain.contracts import ArchitectOutput, CoderOutput, SpecOutput, TesterOutput


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
# A task may be re-attempted up to MAX_CODER_ATTEMPTS times OR until the
# cumulative spend crosses MAX_TASK_COST_USD, whichever comes first.
MAX_CODER_ATTEMPTS = 2
MAX_TASK_COST_USD = 1.0

# How many times a tester (QA/security) failure may route back to the coder for a
# remediation pass before the run is parked for the human. Bounded like the build
# loop so tester findings don't dead-end, but also don't loop forever.
MAX_TESTER_REMEDIATIONS = 1

# How many times the coder may bounce an infeasible design back to the architect
# (coder → architect feedback) before the run is parked for the human.
MAX_REARCHITECT_LOOPS = 1


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
                f"  • '{term}' — {'the story INVENTED a value your request never supplied' if _is_bound(crit) else 'still undefined'}"
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

def gate_after_architect(architect: ArchitectOutput) -> GateResult:
    """Validates architect-agent output before coder-agent.

    This gate checks both automatic pass/fail conditions AND whether
    human approval is needed before proceeding.
    """
    failures: list[str] = []
    human_questions: list[str] = []

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

    # Breaking changes need human approval
    if architect.breaking_changes:
        human_questions.append(
            f"⚠️ BREAKING CHANGES detected — these will affect existing API consumers:\n"
            + "\n".join(f"  • {bc}" for bc in architect.breaking_changes)
            + "\n  → Approve these breaking changes?"
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
        human_questions.append(
            f"🛡️ SENSITIVE WORK detected [{tags}] — this may require domain expertise "
            f"(legal review, security audit, compliance check) that agents cannot provide.\n"
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
