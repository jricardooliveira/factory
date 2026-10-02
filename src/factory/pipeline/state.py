"""The graph state every pipeline node reads and returns a partial update of."""

from __future__ import annotations

from typing import Any, TypedDict

from factory.workspace.layout import EVIDENCE_PATHS


class PipelineState(TypedDict, total=False):
    request: str
    story_id: str
    run_id: int
    db_path: str
    opencode_cwd: str
    project_spec: str  # rendered project spec context for agents
    project_dir: str  # project root == its git repo (== opencode_cwd) — ADRs + decision memory
    replay_run_id: int  # if set, feed stored agent outputs instead of calling opencode

    # Agent outputs (raw + parsed)
    spec_raw: str
    spec: dict[str, Any]
    architect_raw: str
    architect: dict[str, Any]
    boundary_raw: str
    boundary: dict[str, Any]  # the boundary review (BoundaryOutput) of the CURRENT design
    boundary_status: str  # "" (none needed / not yet) | reviewed | skipped (replay) | unavailable
    boundary_redesign: bool  # the review failed: route back to the architect
    boundary_redesigns: int  # how many redesigns a failed review has triggered
    coder_raw: str
    coder: dict[str, Any]
    tester_raw: str
    tester: dict[str, Any]
    release_raw: str
    release: dict[str, Any]  # release notes (ReleaseOutput); {} when none were written

    # Gate results
    gate_1: dict[str, Any]
    gate_2: dict[str, Any]
    gate_build: dict[str, Any]
    gate_test: dict[str, Any]
    gate_release: dict[str, Any]

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


def factory_owned_paths(state: PipelineState) -> tuple[str, ...]:
    """Repo paths the FACTORY owns in this run's working tree ('' off-project).

    A project's evidence (docs/work, ADRs, releases, PROJECT_RULES.md,
    project-spec.json) lives inside its repository and is committed by the
    factory, so every code measurement — scope check, tester diff — looks past
    it, and the coder may not write it. Off-project there is no evidence, so
    nothing is excluded or reserved.
    """
    return EVIDENCE_PATHS if state.get("project_dir") else ()
