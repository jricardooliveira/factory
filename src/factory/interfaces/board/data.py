"""Data layer for the interactive board.

TUI-free so it can be unit-tested without Textual; it reads through
`runs.queries`, never the database directly. The Textual view
(`interfaces/board/tui.py`) renders these dataclasses. Per-run stage progress and
the timeline live in `factory.evidence.progress`, because surfaces below
`interfaces` (the scenario matrix) need them too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from factory.runs.queries import board_entries

# Order matters for display grouping.
PARKED_STATUSES = ["waiting_human"]
ACTIVE_STATUSES = ["running"]
ATTENTION_STATUSES = ["failed", "blocked"]
DONE_STATUSES = ["completed"]

# Kanban columns, left → right, mirroring the agent pipeline.
KANBAN_COLUMNS = ["Needs You", "Spec", "Architect", "Coder", "Done", "Blocked"]


@dataclass
class BoardRun:
    id: int
    story_id: str
    title: str
    status: str
    stage: str
    cost: float
    started_at: str
    project: str = "—"
    request: str = ""
    questions: list[str] = field(default_factory=list)
    architecture: str = ""  # summary of the design under review
    modules: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def needs_input(self) -> bool:
        return self.status == "waiting_human"

    @property
    def decision(self) -> str:
        """What approving/rejecting this run actually decides."""
        if self.status != "waiting_human":
            return ""
        stage = (self.stage or "").lower()
        if "release" in stage:
            return "the RELEASE — ship this reviewed change (Checkpoint 3)"
        if "architect" in stage or "gate-2" in stage:
            return "the ARCHITECTURE / design (before any code is written)"
        if "spec" in stage:
            return "the STORY scope (before design)"
        return "this checkpoint"

    @property
    def state_label(self) -> str:
        return {
            "waiting_human": "NEEDS YOU",
            "running": "running",
            "failed": "failed",
            "blocked": "blocked",
        }.get(self.status, self.status)


def _split_questions(text: str | None) -> list[str]:
    if not text:
        return []
    return [q.strip() for q in text.split("\n\n") if q.strip()]


def load_board_runs(db_path: Path, include_done: bool = False) -> list[BoardRun]:
    """Return the runs worth showing on the board, parked ones first.

    `include_done` adds recently-completed runs (for the kanban 'Done' column);
    the table view omits them to stay focused on actionable work.
    """
    groups = [PARKED_STATUSES, ACTIVE_STATUSES, ATTENTION_STATUSES]
    if include_done:
        groups.append(DONE_STATUSES)
    out: list[BoardRun] = []
    for entry in board_entries(groups, db_path=db_path):
        run, gate = entry.run, entry.gate
        arch_notes, modules = "", []
        if entry.architect_output:  # a parked run: show the design being decided
            arch_notes, modules = _summarize_architecture(entry.architect_output)
        out.append(
            BoardRun(
                id=run["id"],
                story_id=run["story_id"],
                title=run.get("story_title") or run["story_id"],
                status=run["status"],
                stage=run.get("current_stage") or "?",
                cost=entry.cost,
                started_at=run.get("started_at") or "",
                project=run.get("project_slug") or "—",
                request=run.get("request") or "",
                questions=_split_questions(gate.get("human_questions") if gate else None),
                architecture=arch_notes,
                modules=modules,
                error=run.get("error"),
            )
        )
    return out


def _summarize_architecture(output_text: str) -> tuple[str, list[str]]:
    """Pull architecture notes + affected modules from a stored architect output."""
    try:
        start, end = output_text.find("{"), output_text.rfind("}")
        data = json.loads(output_text[start : end + 1]) if start != -1 else {}
    except (ValueError, TypeError):
        return "", []
    return str(data.get("architecture_notes", "")), list(data.get("modules_affected", []))


def find_run(db_path: Path, run_id: int) -> BoardRun | None:
    for r in load_board_runs(db_path):
        if r.id == run_id:
            return r
    return None


def column_for(run: BoardRun) -> str:
    """Which kanban column a run belongs in, based on status + current stage."""
    if run.status == "waiting_human":
        return "Needs You"
    if run.status == "completed":
        return "Done"
    if run.status in ("blocked", "failed"):
        return "Blocked"
    stage = (run.stage or "").lower()  # running
    if "architect" in stage or "gate-2" in stage:
        return "Architect"
    if "coder" in stage or "gate-build" in stage:
        return "Coder"
    return "Spec"


def group_by_column(runs: list[BoardRun]) -> dict[str, list[BoardRun]]:
    cols: dict[str, list[BoardRun]] = {c: [] for c in KANBAN_COLUMNS}
    for r in runs:
        cols.setdefault(column_for(r), []).append(r)
    return cols


def list_projects_on_board(runs: list[BoardRun]) -> list[str]:
    """Distinct project slugs present among the given runs, in stable order."""
    seen: list[str] = []
    for r in runs:
        if r.project not in seen:
            seen.append(r.project)
    return seen
