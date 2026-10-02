"""The boss: every agent stage is authorized before it starts — as code, not an LLM.

`graph.py` wraps each agent node in `authorized(stage, node)`. Before the node
runs, the wrapper reads the run's RECORDED gate verdicts (newest per gate), asks
the pure rules in `domain.authorization` whether the stage's prerequisites exist,
and stores the decision. A refusal blocks the run — persisted, so it never sits
'running' — and the agent is never called, so no token is spent on a stage that
had nothing valid to work from.

Verdicts are read from the DB, not graph state: a resumed run rebuilds its state
from agent logs and carries no gate dicts, and the operator's checkpoint answer
lives only in `gate_results.human_response`.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factory.domain.authorization import (
    Authorization,
    GateRecord,
    authorize_architect,
    authorize_boundary,
    authorize_release,
    authorize_release_notes,
    authorize_remediation,
    authorize_task,
    authorize_tester,
    latest_gates,
)
from factory.domain.contracts import ArchitectOutput, SpecOutput
from factory.domain.task_order import order_tasks
from factory.pipeline.agent_calls import budget_refusal_for, db_conn
from factory.pipeline.state import PipelineState, factory_owned_paths
from factory.workspace.git import code_changed_since
from factory.state.db import (
    finish_run,
    get_run,
    get_run_gates,
    log_authorization,
    update_run_stage,
    update_story_status,
)

Node = Callable[[PipelineState], dict[str, Any]]

# A run in one of these states is already stopped; its nodes pass it through.
_STOPPED = ("failed", "blocked")


def _parsed(model: type, data: Any) -> Any:
    if not data:
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


def _candidate_changed(state: PipelineState) -> bool | None:
    """Did the code change after Checkpoint 3 pinned the reviewed candidate?"""
    conn = db_conn(state)
    try:
        run = get_run(conn, state["run_id"]) or {}
    finally:
        conn.close()
    candidate = run.get("candidate_commit")
    if not candidate:
        return None
    return code_changed_since(
        Path(state.get("opencode_cwd") or "."), candidate, exclude=factory_owned_paths(state)
    )


def authorization_for(
    state: PipelineState, stage: str, gates: dict[str, GateRecord]
) -> Authorization:
    """The boss's decision for `stage` given this state and the recorded verdicts."""
    spec: SpecOutput | None = _parsed(SpecOutput, state.get("spec"))
    completed = list(state.get("tasks_completed") or [])
    if stage == "architect-agent":
        return authorize_architect(spec, gates.get("gate-1-spec"))
    if stage == "coder-agent":
        if state.get("remediation"):
            return authorize_remediation(
                gates.get("gate-test"), state.get("prior_findings") or [],
                gate_release=gates.get("gate-release"),
            )
        tasks = order_tasks(list(spec.tasks)) if spec else []
        index = state.get("task_index", 0)
        task = tasks[index] if 0 <= index < len(tasks) else None
        return authorize_task(
            task, completed, _parsed(ArchitectOutput, state.get("architect")),
            gates.get("gate-2-architect"),
        )
    if stage == "tester-agent":
        task_ids = [t.id for t in spec.tasks] if spec else []
        return authorize_tester(task_ids, completed, gates.get("gate-build"))
    if stage == "boundary-agent":
        return authorize_boundary(_parsed(ArchitectOutput, state.get("architect")))
    if stage == "release-agent":
        return authorize_release_notes(gates.get("gate-test"))
    if stage == "release":
        return authorize_release(gates.get("gate-release"), _candidate_changed(state))
    raise ValueError(f"the boss has no authorization rule for stage {stage!r}")


def authorized(stage: str, node: Node) -> Node:
    """Wrap an agent node so it only runs once the boss has authorized it."""

    @functools.wraps(node)
    def run(state: PipelineState) -> dict[str, Any]:
        if state.get("status") in _STOPPED:
            return node(state)
        # `release` makes no model call; every other stage would spend.
        refusal = budget_refusal_for(state) if stage != "release" else None
        conn = db_conn(state)
        try:
            decision = (
                Authorization(stage=stage, allowed=False, missing=(refusal,)) if refusal
                else authorization_for(
                    state, stage, latest_gates(get_run_gates(conn, state["run_id"]))
                )
            )
            log_authorization(
                conn, state["run_id"], decision.stage, decision.allowed,
                granted_by=decision.granted_by, missing=decision.missing,
                warnings=decision.warnings,
            )
            if not decision.allowed:
                error = f"Boss: {decision.summary()}"
                update_run_stage(conn, state["run_id"], decision.stage)
                update_story_status(conn, state["story_id"], "blocked")
                finish_run(conn, state["run_id"], "blocked", error=error)
            conn.commit()
        finally:
            conn.close()
        if not decision.allowed:
            return {"status": "blocked", "error": error, "next_action": "give_up"}
        return node(state)

    return run
