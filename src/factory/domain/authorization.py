"""The boss: may a stage START, given what the run has produced so far? (pure, no I/O)

Gates judge a stage's OUTPUT after it ran. Authorization judges its INPUTS before
a token is spent — the original brief's "boss may call X only if …" rules, and
its failure mode "boss starts stages with missing artifacts". On the happy path
the graph's routing makes most of these hold by construction; they exist for the
paths that rebuild state from the database (resume, retry, replay) and for bad
agent data that the forgiving helpers (`order_tasks`) would otherwise absorb.

The boss is code, not an LLM, on purpose: it decides from recorded verdicts, so
two runs with the same record always get the same answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory.domain.contracts import ArchitectOutput, SpecOutput, TaskDef
from factory.domain.gates import boundary_review_reasons


@dataclass(frozen=True)
class GateRecord:
    """One recorded gate verdict, plus the operator's answer if it parked."""

    name: str
    passed: bool
    needs_human: bool = False
    human_response: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> GateRecord:
        return cls(
            name=row["gate_name"],
            passed=bool(row["passed"]),
            needs_human=bool(row.get("needs_human")),
            human_response=row.get("human_response"),
        )

    @property
    def awaiting_operator(self) -> bool:
        return self.needs_human and self.human_response is None

    @property
    def rejected_by_operator(self) -> bool:
        # Same reading as runs.context.decision_from_response: only an explicit
        # REJECTED counts — never guess a rejection.
        return self.needs_human and (self.human_response or "").strip().upper().startswith(
            "REJECTED:"
        )


def latest_gates(rows: Iterable[Mapping[str, Any]]) -> dict[str, GateRecord]:
    """The newest verdict per gate name (rows in insertion order).

    Newest is what counts: after a rejected design is re-architected, the fresh
    gate-2 row — not the rejected one — is what authorizes the coder.
    """
    return {row["gate_name"]: GateRecord.from_row(row) for row in rows}


@dataclass(frozen=True)
class Authorization:
    stage: str
    allowed: bool
    granted_by: str = ""
    missing: tuple[str, ...] = ()  # blocking: the stage must not start
    warnings: tuple[str, ...] = ()  # recorded for the reviewer, not blocking

    def summary(self) -> str:
        if not self.allowed:
            return f"{self.stage} refused — missing: " + "; ".join(self.missing)
        text = f"{self.stage} authorized by {self.granted_by}"
        if self.warnings:
            text += " (warnings: " + "; ".join(self.warnings) + ")"
        return text


def _decide(
    stage: str, granted_by: str, missing: list[str], warnings: list[str] | None = None
) -> Authorization:
    return Authorization(
        stage=stage,
        allowed=not missing,
        granted_by=granted_by if not missing else "",
        missing=tuple(missing),
        warnings=tuple(warnings or ()),
    )


def _grant(gate: GateRecord | None, name: str, checkpoint: str) -> tuple[str, list[str]]:
    """(who authorized, what is missing) for a stage that needs `name` to have passed."""
    if gate is None:
        return "", [f"a {name} verdict (the gate never ran)"]
    if not gate.passed:
        return "", [f"{name} to pass (it failed)"]
    if gate.awaiting_operator:
        return "", [f"the operator's decision at {checkpoint}"]
    if gate.rejected_by_operator:
        return "", [f"{checkpoint} was rejected by the operator — re-run the stage it rejected"]
    if gate.needs_human:
        return f"{name}, approved by the operator at {checkpoint}", []
    return f"{name} passed", []


def authorize_architect(spec: SpecOutput | None, gate_1: GateRecord | None) -> Authorization:
    """Design may start only from an accepted story (brief: "boss may call architect-agent only if…")."""
    missing: list[str] = []
    if spec is None:
        missing.append("a story (no usable spec-agent output)")
    else:
        if not spec.acceptance_criteria:
            missing.append("acceptance criteria")
        if not spec.tasks:
            missing.append("tasks")
    granted_by, gate_missing = _grant(gate_1, "gate-1-spec", "Checkpoint 1")
    return _decide("architect-agent", granted_by, missing + gate_missing)


def authorize_task(
    task: TaskDef | None,
    completed: Sequence[str],
    architect: ArchitectOutput | None,
    gate_2: GateRecord | None,
) -> Authorization:
    """One coder task may start only on an approved design with its dependencies built."""
    if task is None:
        return _decide("coder-agent", "", ["a task to implement"])
    missing: list[str] = []
    if architect is None:
        missing.append("an architecture (no usable architect-agent output)")
    else:
        if not architect.architecture_notes.strip():
            missing.append("architecture notes")
        if not architect.modules_affected:
            missing.append("the affected modules")
    granted_by, gate_missing = _grant(gate_2, "gate-2-architect", "Checkpoint 2")
    missing += gate_missing
    if not task.purpose.strip():
        missing.append(f"{task.id}'s purpose")
    missing += [
        f"{dep} (a dependency of {task.id}) to be implemented first"
        for dep in task.depends_on
        if dep not in completed
    ]
    warnings: list[str] = []
    if not task.scope:
        warnings.append(f"{task.id} declares no allowed scope — the coder is unrestricted")
    if not task.completion_evidence.strip():
        warnings.append(f"{task.id} has no completion evidence — 'done' means 'it builds'")
    return _decide(f"coder-agent:{task.id}", granted_by, missing, warnings)


def authorize_remediation(
    gate_test: GateRecord | None,
    findings: Sequence[str],
    gate_release: GateRecord | None = None,
) -> Authorization:
    """A remediation pass fixes a FAILED review — the tester's, or the operator's
    rejection at Checkpoint 3. Without one there is nothing to fix."""
    missing: list[str] = []
    if gate_release is not None and gate_release.rejected_by_operator and (
        gate_test is None or gate_test.passed
    ):
        granted_by = f"the operator's rejection at Checkpoint 3 ({len(findings)} finding(s))"
    else:
        granted_by = f"gate-test failed with {len(findings)} finding(s)"
        if gate_test is None:
            missing.append("a gate-test verdict to remediate")
        elif gate_test.passed:
            missing.append("a failed gate-test (it passed — there is nothing to remediate)")
    if not findings:
        missing.append("findings to resolve")
    return _decide("coder-agent:remediation", granted_by, missing)


def authorize_tester(
    task_ids: Sequence[str], completed: Sequence[str], gate_build: GateRecord | None
) -> Authorization:
    """Review may start only once every task is built and the last build is green."""
    missing = [f"{tid} to be implemented" for tid in task_ids if tid not in completed]
    if gate_build is None:
        missing.append("a gate-build verdict (nothing was built)")
    elif not gate_build.passed:
        missing.append("the last gate-build to pass")
    return _decide(
        "tester-agent",
        f"gate-build passed with all {len(task_ids)} task(s) implemented",
        missing,
    )


def authorize_release_notes(gate_test: GateRecord | None) -> Authorization:
    """Release notes describe a change that passed review — nothing else."""
    if gate_test is None:
        return _decide("release-agent", "", ["a gate-test verdict (nothing was reviewed)"])
    if not gate_test.passed:
        return _decide("release-agent", "", ["gate-test to pass"])
    return _decide("release-agent", "gate-test passed", [])


def authorize_release(gate_release: GateRecord | None) -> Authorization:
    """Only the operator releases: the newest gate-release must carry their APPROVAL.

    Approving a release the gate judged NOT ready is allowed — the gaps were named
    at the checkpoint — and is recorded as the operator accepting them.
    """
    if gate_release is None:
        return _decide("release", "", ["a gate-release verdict (the release gate never ran)"])
    if not gate_release.needs_human:
        return _decide("release", "", [
            "an operator decision — no agent may pass the release gate"
        ])
    if gate_release.awaiting_operator:
        return _decide("release", "", ["the operator's decision at Checkpoint 3"])
    if gate_release.rejected_by_operator:
        return _decide("release", "", ["Checkpoint 3 was rejected by the operator"])
    warnings = [] if gate_release.passed else [
        "released NOT READY: the evidence gaps named at Checkpoint 3 were accepted by the operator"
    ]
    return _decide("release", "the operator's approval at Checkpoint 3", [], warnings)


def authorize_boundary(architect: ArchitectOutput | None) -> Authorization:
    """The boundary review runs on a real design that declares a boundary impact."""
    if architect is None or not architect.architecture_notes.strip():
        return _decide("boundary-agent", "", ["an architecture to review"])
    reasons = boundary_review_reasons(architect)
    if not reasons:
        return _decide("boundary-agent", "", [
            "a reason to review: the design declares no API, data, breaking or sensitive impact"
        ])
    return _decide("boundary-agent", "the design requires a boundary review: " + "; ".join(reasons), [])
