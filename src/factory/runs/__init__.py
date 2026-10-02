"""Application service: run / resume / retry / replay, dismiss / reconcile, and the
read side (`runs.queries`) — shared by the CLI and the TUI.

Layering: interfaces -> runs -> {pipeline, verification, evidence, workspace, ...}.
Interfaces reach the database only through here. Nothing here renders; progress
is reported through an `on_event` callback.
"""

from factory.runs.context import (
    build_resume_context,
    decision_from_response,
    load_project_spec_text,
    park_unresumable,
)
from factory.runs.events import (
    NodeCompleted,
    OnEvent,
    ResumeEntered,
    RetryStarted,
    RunError,
    RunEvent,
    RunFinished,
    RunOutcome,
    RunStarted,
)
from factory.runs.lifecycle import dismiss_run, reconcile_stale
from factory.runs.service import (
    replay_run,
    resume_run,
    retry_run,
    run_pipeline,
    run_project_pipeline,
)

__all__ = [
    "NodeCompleted",
    "OnEvent",
    "ResumeEntered",
    "RetryStarted",
    "RunError",
    "RunEvent",
    "RunFinished",
    "RunOutcome",
    "RunStarted",
    "build_resume_context",
    "decision_from_response",
    "dismiss_run",
    "load_project_spec_text",
    "park_unresumable",
    "reconcile_stale",
    "replay_run",
    "resume_run",
    "retry_run",
    "run_pipeline",
    "run_project_pipeline",
]
