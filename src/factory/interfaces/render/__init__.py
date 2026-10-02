"""Every rich rendering helper the CLI uses — presentation only.

Functions here take data and print it; they never open the DB or drive a run.
The command modules in `interfaces/cli/` fetch (from `factory.runs`, `evidence`,
...) and hand the result here. One module per command group, mirroring `cli/`:
`run`, `review`, `board`, `project`, `interview`, `workspace`, `selftest`, plus `output` (the
shared Console and the helpers they all use). This package re-exports them all, so
callers write `render.print_queue(...)`.

Output is byte-for-byte what the single-module CLI printed before the split; keep
it that way unless a change to the operator-facing text is the point.
"""

from __future__ import annotations

from typing import Any

from factory.interfaces.render import output
from factory.interfaces.render.output import (
    print_created,
    print_error,
    print_report_written,
)
from factory.interfaces.render.run import (
    print_agent_done,
    print_agent_start,
    print_architect_summary,
    print_coder_summary,
    print_final_status,
    print_gate,
    print_header,
    print_resume_entered,
    print_resume_node,
    print_retry_started,
    print_review_table,
    print_run_finished,
    print_run_node,
    print_run_started,
    print_spec_summary,
)
from factory.interfaces.render.review import (
    print_run_review,
    render_flow,
    render_timeline,
)
from factory.interfaces.render.board import (
    board_renderable,
    print_queue,
    print_runs,
)
from factory.interfaces.render.project import (
    print_project,
    print_project_created,
    print_projects,
)
from factory.interfaces.render.interview import (
    APPROVE_PROMPT,
    print_brief_for_approval,
    print_interview_approved,
    print_interview_paused,
    print_interview_question,
    print_topics_still_required,
)
from factory.interfaces.render.workspace import (
    print_legacy_import,
    print_workspace,
)
from factory.interfaces.render.selftest import (
    print_doctor,
    print_eval_captured,
    print_eval_replayed,
    print_evals,
    print_metrics,
    print_simulation,
    print_tiers,
)

__all__ = [
    "APPROVE_PROMPT",
    "board_renderable",
    "console",
    "output",
    "print_agent_done",
    "print_agent_start",
    "print_architect_summary",
    "print_coder_summary",
    "print_created",
    "print_doctor",
    "print_error",
    "print_eval_captured",
    "print_eval_replayed",
    "print_evals",
    "print_brief_for_approval",
    "print_final_status",
    "print_gate",
    "print_header",
    "print_interview_approved",
    "print_interview_paused",
    "print_interview_question",
    "print_legacy_import",
    "print_metrics",
    "print_project",
    "print_project_created",
    "print_projects",
    "print_queue",
    "print_report_written",
    "print_resume_entered",
    "print_resume_node",
    "print_retry_started",
    "print_review_table",
    "print_run_finished",
    "print_run_node",
    "print_run_review",
    "print_run_started",
    "print_runs",
    "print_simulation",
    "print_spec_summary",
    "print_tiers",
    "print_topics_still_required",
    "print_workspace",
    "render_flow",
    "render_timeline",
]


def __getattr__(name: str) -> Any:
    # `render.console` is looked up on each access, so swapping
    # `render.output.console` (tests capture output this way) reaches every caller.
    if name == "console":
        return output.console
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
