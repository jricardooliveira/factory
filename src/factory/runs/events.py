"""What the run service reports while it works.

The service never renders. It hands each step to an `on_event` callback, and
each interface decides what to show: the CLI prints every event live, the TUI
passes no callback at all (it re-reads the DB on its own refresh tick). A
callback is called synchronously, on the thread doing the run, in order.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunStarted:
    """A fresh (or replayed) run has a DB row and is about to enter the graph."""

    run_id: int
    story_id: str
    request: str
    project_spec_text: str | None = None
    replay_of: int | None = None  # source run whose frozen outputs are replayed


@dataclass(frozen=True)
class NodeCompleted:
    """One LangGraph node finished: its name and the state update it returned."""

    node: str
    output: dict[str, Any]


@dataclass(frozen=True)
class RetryStarted:
    """A dead run is being re-driven from the operator's recorded decision."""

    run_id: int
    gate_name: str
    action: str  # "approve" | "reject"
    feedback: str


@dataclass(frozen=True)
class ResumeEntered:
    """A parked run's decision is recorded; the graph re-enters at `entry`."""

    run_id: int
    entry: str  # "spec" | "architect" | "coder" (pipeline.resume_entry_for)
    action: str  # "approve" | "reject"
    decision: str  # the operator's note, or the default approve/reject wording


@dataclass(frozen=True)
class RunOutcome:
    """Where a run ended up once the graph stopped streaming."""

    run_id: int
    story_id: str
    status: str
    error: str | None
    current_stage: str | None
    db_path: Path
    human_questions: list[str] | None = None  # set only when the run parked at gate-2
    final_state: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RunFinished:
    """The last event of every run/resume that reached the graph."""

    outcome: RunOutcome


RunEvent = RunStarted | NodeCompleted | RetryStarted | ResumeEntered | RunFinished
OnEvent = Callable[[RunEvent], None]


class RunError(Exception):
    """The service refused, or could not complete, a request.

    The message is operator-facing and complete ("No run found with id #7"); the
    interface decides how loudly to show it and whether that is a non-zero exit.
    """


def _ignore(_event: RunEvent) -> None:
    """The default callback: a caller that asked for no events gets none."""
