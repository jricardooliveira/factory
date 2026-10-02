"""The graph state every pipeline node reads and returns a partial update of."""

from __future__ import annotations

from typing import Any, TypedDict


class PipelineState(TypedDict, total=False):
    request: str
    story_id: str
    run_id: int
    db_path: str
    opencode_cwd: str
    project_spec: str  # rendered project spec context for agents
    project_dir: str  # project root (parent of repo/) — enables ADRs + decision memory
    replay_run_id: int  # if set, feed stored agent outputs instead of calling opencode

    # Agent outputs (raw + parsed)
    spec_raw: str
    spec: dict[str, Any]
    architect_raw: str
    architect: dict[str, Any]
    coder_raw: str
    coder: dict[str, Any]
    tester_raw: str
    tester: dict[str, Any]

    # Gate results
    gate_1: dict[str, Any]
    gate_2: dict[str, Any]
    gate_build: dict[str, Any]
    gate_test: dict[str, Any]

    # Committed artifact chain (INTENT -> SPEC -> PLAN -> ADR)
    intent_path: str
    spec_path: str
    plan_path: str
    adr_path: str

    # Remediation loop
    attempt_number: int  # coder attempt count for the CURRENT task (1-based)
    triggered_by: str  # what caused a re-entry, e.g. "gate-build"
    prior_findings: list[str]  # findings from the last failed attempt, fed into the retry
    next_action: str  # coder routing: "complete" | "retry" | "next_task" | "give_up"
    remediation: bool  # coder is in a tester-driven remediation pass (not a task)
    tester_attempt: int  # how many times the tester has run for this story (1-based)
    rearchitect_count: int  # times the coder has bounced an infeasible design back

    # Per-task execution
    task_index: int  # index into the dependency-ordered task list
    tasks_completed: list[str]  # task ids already implemented

    # Overall
    status: str
    error: str
