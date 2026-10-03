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
    tests_run: bool = False  # FACTORY_RUN_TESTS is on: gate-build executes the suite


@dataclass(frozen=True)
class NodeStarted:
    """One LangGraph node is about to run (an agent node may take minutes)."""

    node: str
    detail: str = ""  # e.g. "task 2/5 T-0002 Routes (attempt 1)"; "" when none applies


@dataclass(frozen=True)
class NodeCompleted:
    """One LangGraph node finished: its name and the state update it returned.

    `duration_secs` / `cost_usd` total the agent calls the node logged; None when
    it made none (a gate) or the figure is unknown — never a made-up zero.
    """

    node: str
    output: dict[str, Any]
    duration_secs: float | None = None
    cost_usd: float | None = None


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
    # pipeline.resume_entry_for: "spec" | "architect" | "coder" | "release" |
    # "remediation"; or "tester" when a resume into the coder finds every task built.
    entry: str
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


RunEvent = RunStarted | NodeStarted | NodeCompleted | RetryStarted | ResumeEntered | RunFinished
OnEvent = Callable[[RunEvent], None]


class RunError(Exception):
    """The service refused, or could not complete, a request.

    The message is operator-facing and complete ("No run found with id #7"); the
    interface decides how loudly to show it and whether that is a non-zero exit.
    """


class RunInterrupted(KeyboardInterrupt):
    """The operator interrupted run #run_id (Ctrl-C); it is already marked failed.

    Still a KeyboardInterrupt, so anything that does not know it exits as before.
    """

    def __init__(self, run_id: int) -> None:
        super().__init__(run_id)
        self.run_id = run_id


def ignore_events(_event: RunEvent) -> None:
    """The default callback: a caller that asked for no events gets none."""
