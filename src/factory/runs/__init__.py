"""Application service: run / resume / retry / replay, dismiss / reconcile, and the
read side (`runs.queries`) — shared by the CLI and the TUI.

Layering: interfaces -> runs -> {pipeline, verification, evidence, workspace, ...}.
Interfaces reach the database only through here. Nothing here renders; progress
is reported through an `on_event` callback.
"""

from factory.runs.backlog import BacklogOutcome, mark_started, next_story, propose_backlog
from factory.runs.context import (
    build_resume_context,
    decision_from_response,
    load_project_spec_text,
    park_unresumable,
)
from factory.runs.events import (
    NodeCompleted,
    NodeStarted,
    OnEvent,
    ResumeEntered,
    RetryStarted,
    RunError,
    RunEvent,
    RunFinished,
    RunOutcome,
    RunStarted,
)
from factory.runs.interview import (
    InterviewOutcome,
    has_brief,
    import_answers,
    run_interview,
    run_story_interview,
)
from factory.runs.lifecycle import dismiss_run, reconcile_stale
from factory.runs.status import ProjectStatus, all_project_status, project_status
from factory.runs.service import (
    replay_run,
    resume_run,
    retry_run,
    run_pipeline,
    run_project_pipeline,
)

__all__ = [
    "BacklogOutcome",
    "InterviewOutcome",
    "ProjectStatus",
    "all_project_status",
    "project_status",
    "NodeCompleted",
    "NodeStarted",
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
    "has_brief",
    "load_project_spec_text",
    "mark_started",
    "next_story",
    "park_unresumable",
    "propose_backlog",
    "reconcile_stale",
    "replay_run",
    "resume_run",
    "retry_run",
    "import_answers",
    "run_interview",
    "run_story_interview",
    "run_pipeline",
    "run_project_pipeline",
]
