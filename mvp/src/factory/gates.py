"""Gate logic — automatic checks between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field

from factory.models import ArchitectOutput, CoderOutput, SpecOutput, TesterOutput


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


# ── Gate 1: After spec-agent ──────────────────────────────────────

def gate_after_spec(spec: SpecOutput) -> GateResult:
    """Validates spec-agent output before architect-agent."""
    failures: list[str] = []

    if spec.verdict != "pass":
        failures.append(f"Spec verdict is '{spec.verdict}', not 'pass'")

    if not spec.title:
        failures.append("Story has no title")

    if len(spec.acceptance_criteria) < 2:
        failures.append(
            f"Need at least 2 acceptance criteria, got {len(spec.acceptance_criteria)}"
        )

    if not spec.tasks:
        failures.append("No tasks defined")

    if spec.questions:
        failures.append(f"Open questions remain: {spec.questions}")

    # Budget check: too many tasks = scope explosion
    if len(spec.tasks) > MAX_TASKS_PER_STORY:
        failures.append(
            f"Story has {len(spec.tasks)} tasks (max {MAX_TASKS_PER_STORY}). "
            "Split into multiple stories."
        )

    if failures:
        return GateResult(
            gate="gate-1-spec",
            passed=False,
            reason="; ".join(failures),
        )

    return GateResult(
        gate="gate-1-spec",
        passed=True,
        reason=f"Story '{spec.title}' has {len(spec.acceptance_criteria)} AC and {len(spec.tasks)} tasks",
    )


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
