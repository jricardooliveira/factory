"""The read side the interfaces render: run lists, the queue, the board, a run's
review bundle, the factory report and metrics.

Interfaces never open the database themselves (the layering guard enforces it):
they ask here, and render what comes back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factory.evidence import metrics, trust_package
from factory.evidence.progress import (
    StageStatus,
    TimelineEvent,
    run_pipeline_progress,
    run_timeline,
)
from factory.state import projects as project_rows
from factory.state import reports
from factory.state.db import (
    get_agent_log,
    get_db,
    get_pending_human_gate,
    get_run,
    get_run_cost,
    get_run_gates,
    get_run_logs,
    get_runs_by_status,
    init_db,
    list_runs,
)
from factory.workspace.projects import project_repository

Gate = dict[str, Any] | None


def all_runs(*, db_path: Path) -> list[dict[str, Any]]:
    """Every run, oldest first (`factory list`)."""
    init_db(db_path)
    with get_db(db_path) as conn:
        return list_runs(conn)


def run_record(run_id: int, *, db_path: Path) -> tuple[list[dict], list[dict]]:
    """(agent logs, gate results) of one run, in order."""
    with get_db(db_path) as conn:
        return get_run_logs(conn, run_id), get_run_gates(conn, run_id)


def queue(
    *, db_path: Path
) -> tuple[list[tuple[dict, Gate, float]], list[tuple[dict, float]]]:
    """(parked runs with their pending gate and cost, runs needing attention + cost)."""
    init_db(db_path)
    with get_db(db_path) as conn:
        parked = [
            (run, get_pending_human_gate(conn, run["id"]), get_run_cost(conn, run["id"]))
            for run in get_runs_by_status(conn, ["waiting_human"])
        ]
        attention = [
            (run, get_run_cost(conn, run["id"]))
            for run in get_runs_by_status(conn, ["failed", "blocked"])
        ]
    return parked, attention


def board(
    *, db_path: Path
) -> tuple[list[tuple[dict, Gate, float]], list[tuple[dict, float]]]:
    """(active runs — running or parked — with gate + cost, attention runs + cost)."""
    init_db(db_path)
    with get_db(db_path) as conn:
        active = [
            (
                r,
                get_pending_human_gate(conn, r["id"]) if r["status"] == "waiting_human" else None,
                get_run_cost(conn, r["id"]),
            )
            for r in get_runs_by_status(conn, ["running", "waiting_human"])
        ]
        attention = [
            (r, get_run_cost(conn, r["id"])) for r in get_runs_by_status(conn, ["failed", "blocked"])
        ]
    return active, attention


@dataclass
class BoardEntry:
    """One run as the interactive board needs it."""

    run: dict[str, Any]
    cost: float
    gate: Gate = None
    architect_output: str = ""  # the design under review, for a parked run


def board_entries(status_groups: list[list[str]], *, db_path: Path) -> list[BoardEntry]:
    """Runs in each status group (in group order), with what the board shows."""
    init_db(db_path)
    out: list[BoardEntry] = []
    with get_db(db_path) as conn:
        for group in status_groups:
            for run in get_runs_by_status(conn, group):
                parked = run["status"] == "waiting_human"
                entry = BoardEntry(run=run, cost=get_run_cost(conn, run["id"]))
                if parked:
                    entry.gate = get_pending_human_gate(conn, run["id"])
                    alog = get_agent_log(conn, run["id"], "architect-agent")
                    entry.architect_output = (alog or {}).get("output_text") or ""
                out.append(entry)
    return out


@dataclass
class RunReview:
    """Everything `factory review` shows for one run."""

    run: dict[str, Any]
    logs: list[dict[str, Any]]
    gates: list[dict[str, Any]]
    stages: list[StageStatus]
    timeline: list[TimelineEvent]
    trust_package: dict[str, Any]
    trust_issues: list[str] = field(default_factory=list)


def review_bundle(run_id: int, *, db_path: Path) -> RunReview | None:
    """A run's full record, or None if there is no such run."""
    init_db(db_path)
    with get_db(db_path) as conn:
        run = get_run(conn, run_id)
        if not run:
            return None
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)
    pkg = trust_package.assemble(db_path, run_id)
    return RunReview(
        run=run,
        logs=logs,
        gates=gates,
        stages=run_pipeline_progress(db_path, run_id),
        timeline=run_timeline(db_path, run_id),
        trust_package=pkg,
        trust_issues=trust_package.validate(pkg) if pkg else [],
    )


def run_stages(run_id: int, *, db_path: Path) -> list[StageStatus]:
    return run_pipeline_progress(db_path, run_id)


@dataclass
class FactoryReport:
    """What the static HTML report renders."""

    projects: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    logs_by_run: dict[int, list[dict[str, Any]]]
    gates_by_run: dict[int, list[dict[str, Any]]]


def factory_report(*, db_path: Path) -> FactoryReport:
    init_db(db_path)
    with get_db(db_path) as conn:
        projects = [
            {**row, "repo_path": str(project_repository(row, db_path) or "")}
            for row in project_rows.list_projects_with_run_counts(conn)
        ]
        return FactoryReport(
            projects=projects,
            runs=reports.runs_overview(conn),
            logs_by_run=reports.agent_logs_by_run(conn),
            gates_by_run=reports.gate_results_by_run(conn),
        )


def factory_metrics(*, db_path: Path) -> metrics.FactoryMetrics:
    init_db(db_path)
    return metrics.compute(db_path)
