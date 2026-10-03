"""Rebuilding what a run needs from what the DB recorded.

Everything a resume or retry reads back — the operator's decision, the latest
usable spec and architecture, the project spec — and the one write that keeps a
failed resume visible (`park_unresumable`). No graph, no rendering.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from factory.domain.agent_output import parse_agent_json
from factory.domain.project_spec import ProjectSpec
from factory.state.db import finish_run, get_agent_logs_for


def decision_from_response(response: str) -> tuple[str, str]:
    """Recover (action, feedback) from a stored `human_response`.

    `respond_to_gate` records the operator's decision as "APPROVED: ..." /
    "REJECTED: ...". Anything unrecognised is treated as an APPROVAL note —
    never guess a rejection, which would silently redo work the operator may
    have accepted.
    """
    text = (response or "").strip()
    for prefix, action in (("REJECTED:", "reject"), ("APPROVED:", "approve")):
        if text.upper().startswith(prefix):
            return action, text[len(prefix):].strip()
    return "approve", text


def build_resume_context(
    conn: sqlite3.Connection, run_id: int
) -> tuple[dict | None, dict | None]:
    """The (spec, architecture) a parked run must resume from — the LATEST of each.

    This used to scan `get_run_logs` (ORDER BY id ASC) with `next(...)`, taking the
    OLDEST log. After a reject → re-architect → park-again cycle that meant
    `factory review` showed the operator the revised design while the coder was
    handed the original, rejected one: the human approves one artifact and a
    different one proceeds. `get_agent_log` is already ORDER BY id DESC LIMIT 1;
    it just wasn't the function being called.

    Returns (None, None) for a stage that never ran — a Checkpoint-1 park has no
    architecture yet.
    """
    spec = _last_usable(conn, run_id, "spec-agent")
    arch = _last_usable(conn, run_id, "architect-agent")
    if spec and arch:
        from factory.domain.contracts import ArchitectOutput, SpecOutput
        from factory.domain.scope_plan import reconcile_scope
        try:
            revised, _ = reconcile_scope(SpecOutput.model_validate(spec), ArchitectOutput.model_validate(arch))
            spec = revised.model_dump()
        except ValueError:
            pass  # the existing missing/malformed-artifact handling owns this refusal
    return spec, arch


def last_boundary_review(conn: sqlite3.Connection, run_id: int) -> dict | None:
    """The newest usable boundary review — its rules travel with the approved design."""
    return _last_usable(conn, run_id, "boundary-agent")


def _last_usable(conn: sqlite3.Connection, run_id: int, agent: str) -> dict | None:
    """The newest output of `agent` that actually parses.

    "Newest row" is not "newest artifact": a provider error is stored as a log
    too. Taking the newest row blindly made a transient 500 erase a perfectly
    good story and abort the resume with "missing spec log", stranding the
    operator's answer. Walk back to the last real one.
    """
    for log in get_agent_logs_for(conn, run_id, agent):  # newest first
        parsed = parse_agent_json(log["output_text"] or "")
        if parsed:
            return parsed
    return None


def park_unresumable(conn: sqlite3.Connection, run_id: int, reason: str) -> None:
    """Record that a resume could not proceed, instead of leaving it 'running'.

    `resume_run` flips the run to 'running' before rebuilding context; an early
    return then left it stuck there — invisible to `factory queue`, and later
    mislabelled by `reconcile` as a dead process. Same orphan class already fixed
    at gate-1 and the architect.
    """
    finish_run(conn, run_id, "blocked", error=f"Cannot resume: {reason}")
    conn.commit()


def load_project_spec_text(project: dict) -> str | None:
    """A registered project's spec, rendered as the architect's context block."""
    spec_path = project.get("spec_path")
    if not spec_path:
        return None
    spec_data = json.loads(Path(spec_path).read_text())
    return ProjectSpec.model_validate(spec_data).to_architect_context()
