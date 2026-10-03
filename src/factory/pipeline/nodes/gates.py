"""Gate nodes: apply the deterministic policy in `domain.gates` and persist the verdict.

gate-1 (after the spec) and gate-2 (after the architect) may park the run for a
human; gate-test (after the tester) completes the story or routes it back to the
coder for a bounded remediation pass.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from factory.domain.ambiguity import defined_threshold_terms
from factory.domain.contracts import (
    ArchitectOutput,
    BoundaryOutput,
    ReleaseOutput,
    SpecOutput,
    TesterOutput,
)
from factory.agent_config.settings import settings
from factory.domain.gates import (
    gate_after_architect,
    gate_after_release,
    gate_after_spec,
    gate_after_tester,
)
from factory.domain.traceability import trace_criteria, unassessed_criteria
from factory.pipeline.agent_calls import budget_refusal_for, db_conn, spend_so_far
from factory.pipeline.evidence_writers import release_evidence_gaps, write_trust_package
from factory.pipeline.state import PipelineState
from factory.workspace.git import git_head
from factory.state.db import (
    finish_run,
    set_candidate_commit,
    get_human_responses,
    log_gate,
    update_run_stage,
    update_story_status,
)

# ── gate-1 ────────────────────────────────────────────────────────


def settled_threshold_terms(state: PipelineState) -> frozenset[str]:
    """Threshold terms a HUMAN has actually settled for this project.

    Deliberately NOT the whole project-memory block. ADRs are authored by the
    architect-agent and `evidence.adr.render_adr` stamps every one
    `Status: proposed (pending architecture sign-off)` — never approved. Reading
    them here let an agent-invented number launder itself into a decision:
    observed live, one run's architect chose "24 hours" and wrote an ADR, and the
    NEXT run then passed the ambiguity gate on the strength of that ADR and chose
    48 hours instead. Two runs, two contradictory undocumented business rules,
    neither approved by anyone.

    Sources that count:
      - the project spec (operator-authored JSON),
      - PROJECT_RULES.md (operator-authored),
      - an ADR whose Status line records an actual approval.
    """
    parts: list[str] = [state.get("project_spec") or ""]

    # The operator's OWN answers at this run's checkpoints. These are the most
    # authoritative source there is — they were typed by the person the gate
    # exists to protect. Omitting them made the gate un-dischargeable: the
    # operator defined "overdue", the spec-agent used the definition correctly,
    # and the gate asked the same question again. An interruption that cannot be
    # answered spends the scarcest resource this factory has and buys nothing.
    run_id, db_path = state.get("run_id"), state.get("db_path")
    if run_id and db_path:
        try:
            conn = db_conn(state)
            try:
                parts.extend(get_human_responses(conn, run_id))
            finally:
                conn.close()
        except sqlite3.Error:
            pass  # evidence is best-effort; never let it break the gate

    project_dir = state.get("project_dir")
    if project_dir:
        root = Path(project_dir)
        rules = root / "PROJECT_RULES.md"
        if rules.is_file():
            try:
                parts.append(rules.read_text(encoding="utf-8"))
            except OSError:
                pass
        adr_dir = root / "docs" / "architecture" / "adr"
        if adr_dir.is_dir():
            for adr in sorted(adr_dir.glob("ADR-*.md")):
                if f"ADR-{state.get('story_id')}-" in adr.name:
                    continue  # never let this story's own ADR settle its own question
                try:
                    text = adr.read_text(encoding="utf-8")
                except OSError:
                    continue
                status = next(
                    (ln for ln in text.splitlines() if ln.lower().startswith("- status:")),
                    "",
                ).lower()
                if "approved" in status and "pending" not in status:
                    parts.append(text)
    return defined_threshold_terms("\n".join(parts))


def node_gate_1(state: PipelineState) -> dict[str, Any]:
    if state.get("status") in ("failed", "blocked"):
        return state

    spec = SpecOutput.model_validate(state["spec"])
    result = gate_after_spec(
        spec,
        defined_terms=settled_threshold_terms(state),
        # Keyed off the operator's own words: a term THEY used without a
        # number needs sign-off however the agents resolve it.
        request=state.get("request", ""),
    )

    error = f"Gate 1 failed: {result.reason}" if not result.passed else None
    conn = db_conn(state)
    try:
        human_q_text = "\n\n".join(result.human_questions) if result.human_questions else None
        log_gate(
            conn, state["run_id"], result.gate, result.passed, result.reason,
            needs_human=result.needs_human, human_questions=human_q_text,
        )
        # A rejection must be PERSISTED, not just returned in graph state. Without
        # this the run stayed 'running' with a NULL error forever: invisible to
        # `factory queue`, and eventually mopped up by `reconcile` as a dead
        # process — misreporting a policy decision as a crash.
        if not result.passed:
            update_story_status(conn, state["story_id"], "failed")
            finish_run(conn, state["run_id"], "failed", error=error)
        elif result.needs_human:
            finish_run(conn, state["run_id"], "waiting_human", error=None)
            update_run_stage(conn, state["run_id"], "gate-1-human")
        conn.commit()
    finally:
        conn.close()

    gate_dict = {
        "gate": result.gate,
        "passed": result.passed,
        "reason": result.reason,
        "needs_human": result.needs_human,
        "human_questions": result.human_questions,
    }

    if not result.passed:
        return {"gate_1": gate_dict, "status": "failed", "error": error}

    if result.needs_human:
        return {"gate_1": gate_dict, "status": "waiting_human"}

    return {"gate_1": gate_dict}


# ── gate-2 ────────────────────────────────────────────────────────


def node_gate_2(state: PipelineState) -> dict[str, Any]:
    # `blocked` arrives when the boss refused the architect: there is no design to judge.
    if state.get("status") in ("failed", "blocked"):
        return state

    arch = ArchitectOutput.model_validate(state["architect"])
    status = state.get("boundary_status")
    result = gate_after_architect(
        arch,
        _parsed_or_none(BoundaryOutput, state.get("boundary")) if status == "reviewed" else None,
        boundary_unavailable=status == "unavailable",
    )

    if state.get("plan_id"):
        from factory.domain.workflow import design_conflicts
        from factory.state.workflow import get_plan
        conn = db_conn(state)
        try:
            plan = get_plan(conn, state["plan_id"])
        finally:
            conn.close()
        conflicts = design_conflicts(plan, arch.modules_affected,
                                     api=arch.api_impact.lower() not in ("no", "none"),
                                     data=arch.db_impact.lower() not in ("no", "none"),
                                     dependencies=bool(arch.external_dependencies))
        if conflicts:
            result.passed = False
            result.needs_human = False
            result.reason = "; ".join(conflicts) + ". Refine and approve a revised batch before coding."

    conn = db_conn(state)
    try:
        human_q_text = "\n\n".join(result.human_questions) if result.human_questions else None
        log_gate(
            conn, state["run_id"], result.gate, result.passed, result.reason,
            needs_human=result.needs_human, human_questions=human_q_text,
        )
        if not result.passed:
            finish_run(conn, state["run_id"], "failed", error=f"Gate 2 failed: {result.reason}")
        elif result.needs_human:
            finish_run(conn, state["run_id"], "waiting_human", error=None)
            update_run_stage(conn, state["run_id"], "gate-2-human")
        conn.commit()
    finally:
        conn.close()

    gate_dict = {
        "gate": result.gate,
        "passed": result.passed,
        "reason": result.reason,
        "needs_human": result.needs_human,
        "human_questions": result.human_questions,
    }

    if not result.passed:
        return {"gate_2": gate_dict, "status": "failed", "error": f"Gate 2 failed: {result.reason}"}

    if result.needs_human:
        return {"gate_2": gate_dict, "status": "waiting_human"}

    return {"gate_2": gate_dict}


# ── gate-test ─────────────────────────────────────────────────────


def _tester_findings(tester: TesterOutput) -> list[str]:
    """Flatten a failed tester's sub-verdicts into actionable findings for the coder."""
    findings: list[str] = []
    if tester.missing_coverage:
        findings.append("Missing test coverage: " + ", ".join(tester.missing_coverage))
    if tester.security_findings:
        findings.append("Security: " + ", ".join(tester.security_findings))
    if tester.performance_findings:
        findings.append("Performance: " + ", ".join(tester.performance_findings))
    if tester.summary:
        findings.append(tester.summary)
    return findings or ["Tester failed; address the failing QA/security/performance verdicts"]


def node_gate_test(state: PipelineState) -> dict[str, Any]:
    if state.get("status") in ("failed", "blocked"):
        return state

    tester = TesterOutput.model_validate(state["tester"])
    result = gate_after_tester(tester)

    # Surface any acceptance criterion the tester never assessed (a silent drop).
    # Non-blocking here — recorded as evidence; the trust package carries the full
    # mapping and the release checkpoint is where it should hard-enforce.
    reason = result.reason
    try:
        spec_obj = SpecOutput.model_validate(state["spec"])
        unassessed = unassessed_criteria(
            trace_criteria(
                spec_obj.acceptance_criteria, tester.ac_coverage, tester.missing_coverage
            )
        )
        if unassessed:
            reason += f"; ⚠️ {len(unassessed)} AC unassessed by tester: {unassessed}"
    except Exception:
        pass  # never let traceability annotation fail the gate

    gate_dict = {"gate": result.gate, "passed": result.passed, "reason": reason}

    conn = db_conn(state)
    try:
        log_gate(conn, state["run_id"], result.gate, result.passed, reason)

        if result.passed:
            # Reviewed is not released: the release-agent writes the notes and
            # gate-release parks for the operator (Checkpoint 3). This node used to
            # mark the story completed — the factory approving its own work.
            conn.commit()
            return {"gate_test": gate_dict, "remediation": False}

        # Tester failed — route back to the coder for a bounded remediation pass
        # carrying the findings, unless the remediation or cost budget is spent.
        tester_attempt = state.get("tester_attempt", 1)
        conn.commit()  # the spend below is read on its own connection
        budget = settings().budget
        if tester_attempt <= budget.max_tester_remediations and budget_refusal_for(state) is None:
            conn.commit()  # leave the run 'running'; route_after_gate_test → coder
            return {
                "gate_test": gate_dict,
                "remediation": True,
                "triggered_by": "tester-agent",
                "prior_findings": _tester_findings(tester),
                "tester_attempt": tester_attempt + 1,
                "attempt_number": 2,  # escalate the remediation coder to the frontier tier
            }

        error = (
            f"Gate test failed after {tester_attempt} tester pass(es) "
            f"(remediation budget {budget.max_tester_remediations}; story spend "
            f"~${spend_so_far(state).estimated_usd:.2f} of ${budget.max_story_cost_usd:.2f}): "
            f"{result.reason}"
        )
        update_story_status(conn, state["story_id"], "failed")
        finish_run(conn, state["run_id"], "failed", error=error)
        conn.commit()
        return {"gate_test": gate_dict, "status": "failed", "error": error}
    finally:
        conn.close()


# ── gate-release → Checkpoint 3 ───────────────────────────────────


def _parsed_or_none(model: type, data: Any) -> Any:
    try:
        return model.model_validate(data) if data else None
    except ValueError:
        return None


def node_gate_release(state: PipelineState) -> dict[str, Any]:
    """Judge release readiness and ALWAYS park: only the operator releases."""
    if state.get("status") in ("failed", "blocked"):
        return state

    # Pin the candidate under review FIRST: the package names it and measures its
    # change set up to it, and the boss will release exactly this code or nothing.
    conn = db_conn(state)
    try:
        set_candidate_commit(conn, state["run_id"],
                             git_head(Path(state.get("opencode_cwd") or ".")))
        conn.commit()
    finally:
        conn.close()
    # Saved next: the package is what the operator reads at the checkpoint, and an
    # unsaved package is not evidence. Its content does not depend on the park.
    gaps = release_evidence_gaps(state)
    if state.get("project_dir") and write_trust_package(state) is None:
        gaps.append("The trust package could not be saved to docs/releases/ for you to read")
    result = gate_after_release(
        gaps,
        _parsed_or_none(ReleaseOutput, state.get("release")),
        _parsed_or_none(ArchitectOutput, state.get("architect")),
    )
    conn = db_conn(state)
    try:
        log_gate(
            conn, state["run_id"], result.gate, result.passed, result.reason,
            needs_human=True, human_questions="\n\n".join(result.human_questions),
        )
        finish_run(conn, state["run_id"], "waiting_human", error=None)
        update_run_stage(conn, state["run_id"], "gate-release-human")
        conn.commit()
    finally:
        conn.close()
    return {
        "gate_release": {
            "gate": result.gate, "passed": result.passed, "reason": result.reason,
            "needs_human": True, "human_questions": result.human_questions,
        },
        "status": "waiting_human",
    }
