"""Data layer for the interactive board.

Pure and TUI-free so it can be unit-tested without Textual. The Textual view
(factory/tui.py) renders these dataclasses; the CLI rich board can use them too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from factory.state.db import (
    get_agent_log,
    get_db,
    get_pending_human_gate,
    get_run_cost,
    get_run_gates,
    get_run_logs,
    get_runs_by_status,
    init_db,
)

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
    init_db(db_path)
    groups = [PARKED_STATUSES, ACTIVE_STATUSES, ATTENTION_STATUSES]
    if include_done:
        groups.append(DONE_STATUSES)
    out: list[BoardRun] = []
    with get_db(db_path) as conn:
        for status_group in groups:
            for run in get_runs_by_status(conn, status_group):
                parked = run["status"] == "waiting_human"
                gate = get_pending_human_gate(conn, run["id"]) if parked else None
                arch_notes, modules = "", []
                if parked:  # show the design being decided
                    alog = get_agent_log(conn, run["id"], "architect-agent")
                    if alog and alog.get("output_text"):
                        arch_notes, modules = _summarize_architecture(alog["output_text"])
                out.append(
                    BoardRun(
                        id=run["id"],
                        story_id=run["story_id"],
                        title=run.get("story_title") or run["story_id"],
                        status=run["status"],
                        stage=run.get("current_stage") or "?",
                        cost=get_run_cost(conn, run["id"]),
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


@dataclass
class StageStatus:
    label: str
    status: str  # done | failed | waiting | current | pending
    detail: str = ""


# Canonical pipeline order. Gate stages use their gate_results name; agent stages
# use the agent name. "release" isn't built yet (always pending).
_PIPELINE = [
    ("spec-agent", "Spec", "agent"),
    ("gate-1-spec", "Gate 1", "gate"),
    ("architect-agent", "Architect", "agent"),
    ("gate-2-architect", "Gate 2", "gate"),
    ("coder-agent", "Coder", "agent"),
    ("gate-build", "Build", "gate"),
    ("tester-agent", "Tester", "agent"),
    ("gate-test", "Test", "gate"),
    ("release", "Release", "release"),
]
_DONE_VERDICTS = {"pass", "warn", "complete"}  # warn = passed-with-warnings, stage done
_FAIL_VERDICTS = {"blocked", "error", "fail"}


def run_pipeline_progress(db_path: Path, run_id: int) -> list[StageStatus]:
    """Per-stage status for one run, derived from its agent logs + gate results."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT status, current_stage FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row:
            return []
        status, current = row["status"], (row["current_stage"] or "")
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)

    by_agent: dict[str, list[dict]] = {}
    for log in logs:
        by_agent.setdefault(log["agent"], []).append(log)
    gate_by_name: dict[str, dict] = {g["gate_name"]: g for g in gates}  # latest wins
    running = status == "running"

    out: list[StageStatus] = []
    for key, label, kind in _PIPELINE:
        detail = ""
        if kind == "release":
            st = "pending"  # release-agent not built yet
        elif kind == "agent":
            ls = by_agent.get(key, [])
            verdicts = [(x.get("verdict") or "") for x in ls]
            if key == "coder-agent" and ls:
                slots = {x.get("stage_type") or "?": x.get("verdict") for x in ls}
                done = sum(1 for v in slots.values() if v == "complete")
                detail = f"{done}/{len(slots)} tasks"
            if running and current == key:
                st = "current"
            elif not ls:
                st = "pending"
            elif any(v in _FAIL_VERDICTS for v in verdicts):
                st = "failed"
            elif any(v in _DONE_VERDICTS for v in verdicts):
                st = "done"
            else:
                st = "pending"
        else:  # gate
            g = gate_by_name.get(key)
            if running and current == key:
                st = "current"
            elif g is None:
                st = "pending"
            elif g.get("needs_human") and status == "waiting_human" and not g.get("human_response"):
                st = "waiting"
            elif g.get("passed"):
                st = "done"
            else:
                st = "failed"
        out.append(StageStatus(label, st, detail))
    return out


_STAGE_ICON = {
    "done": ("✓", "green"),
    "failed": ("✗", "red"),
    "waiting": ("⏸", "yellow"),
    "current": ("▶", "cyan"),
    "pending": ("○", "dim"),
}


def render_flow(stages: list[StageStatus]) -> str:
    """One-line agent pipeline flow with per-stage status icons (rich markup)."""
    if not stages:
        return ""
    parts = []
    for s in stages:
        icon, style = _STAGE_ICON.get(s.status, ("○", "dim"))
        label = s.label + (f" {s.detail}" if s.detail else "")
        parts.append(f"[{style}]{icon} {label}[/{style}]")
    return "  [dim]→[/dim]  ".join(parts)


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


@dataclass
class TimelineEvent:
    when: str  # ISO timestamp
    kind: str  # "run" | "agent" | "gate"
    label: str
    detail: str = ""
    status: str = "info"  # done | failed | waiting | info — for styling


def _hms(iso: str) -> str:
    return iso[11:19] if iso and len(iso) >= 19 else (iso or "")


def run_timeline(db_path: Path, run_id: int) -> list[TimelineEvent]:
    """Chronological story of a run: start, each agent, each gate, finish."""
    with get_db(db_path) as conn:
        run = conn.execute(
            "SELECT status, started_at, finished_at, error FROM pipeline_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            return []
        run = dict(run)
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)

    events: list[TimelineEvent] = []
    if run["started_at"]:
        events.append(TimelineEvent(run["started_at"], "run", "run started"))

    for log in logs:
        verdict = (log.get("verdict") or "").lower()
        status = "failed" if verdict in ("blocked", "error", "fail") else "done"
        bits = [verdict or "?"]
        if log.get("stage_type") and log["stage_type"] not in ("agent", None):
            bits.append(log["stage_type"])
        if log.get("duration_secs"):
            bits.append(f"{log['duration_secs']:.1f}s")
        if log.get("cost_usd"):
            bits.append(f"${log['cost_usd']:.4f}")
        events.append(TimelineEvent(log["created_at"], "agent", log["agent"], " · ".join(bits), status))

    for g in gates:
        if g.get("needs_human") and not g.get("human_response"):
            status, detail = "waiting", "awaiting human sign-off"
        elif g.get("human_response"):
            status = "done" if g["human_response"].startswith("APPROVED") else "failed"
            detail = g["human_response"]
        else:
            status = "done" if g["passed"] else "failed"
            detail = g.get("reason") or ""
        events.append(TimelineEvent(g["checked_at"], "gate", g["gate_name"], detail, status))

    if run["finished_at"]:
        st = run["status"]
        status = "done" if st == "completed" else ("waiting" if st == "waiting_human" else "failed")
        events.append(TimelineEvent(run["finished_at"], "run", f"run {st}", run.get("error") or "", status))

    events.sort(key=lambda e: e.when)
    return events


_TIMELINE_ICON = {"done": ("✓", "green"), "failed": ("✗", "red"),
                  "waiting": ("⏸", "yellow"), "info": ("•", "cyan")}


def render_timeline(events: list[TimelineEvent]) -> str:
    """Render a timeline as multi-line rich markup."""
    lines = []
    for e in events:
        icon, style = _TIMELINE_ICON.get(e.status, ("•", "dim"))
        detail = f"  [dim]{e.detail}[/dim]" if e.detail else ""
        lines.append(f"[dim]{_hms(e.when)}[/dim] [{style}]{icon}[/{style}] {e.label}{detail}")
    return "\n".join(lines)


def list_projects_on_board(runs: list[BoardRun]) -> list[str]:
    """Distinct project slugs present among the given runs, in stable order."""
    seen: list[str] = []
    for r in runs:
        if r.project not in seen:
            seen.append(r.project)
    return seen
