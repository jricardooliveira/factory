"""Read-only aggregate queries: the metrics, the HTML report, the trust package's
usage totals and the doctor's peek at a home database.

Every query here only READS. Keeping them beside the schema means a schema change
is checked against every reader in one place, instead of in SQL strings scattered
through `evidence/`, `interfaces/` and `preflight/`.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

# ── metrics ───────────────────────────────────────────────────────


# Outcome metrics count LIVE work. A replay re-drives frozen outputs (and logs their
# usage again): counting it inflated throughput, gate rates and token totals.
_LIVE_RUNS = "SELECT id FROM pipeline_runs WHERE replay_of IS NULL"


def replay_run_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM pipeline_runs WHERE replay_of IS NOT NULL"
    ).fetchone()[0]


def run_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) n FROM pipeline_runs WHERE replay_of IS NULL GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def gate_tallies(conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
    """(gate_name, times run, times passed) per gate."""
    rows = conn.execute(
        "SELECT gate_name, COUNT(*) n, SUM(passed) p FROM gate_results "
        f"WHERE run_id IN ({_LIVE_RUNS}) GROUP BY gate_name"
    ).fetchall()
    return [(r["gate_name"], r["n"], r["p"] or 0) for r in rows]


def coder_attempts(conn: sqlite3.Connection) -> list[tuple[int, str, int]]:
    """(run_id, task slot, coder calls) — one row per task a coder worked on."""
    rows = conn.execute(
        "SELECT run_id, stage_type, COUNT(*) n FROM agent_logs "
        f"WHERE agent = 'coder-agent' AND run_id IN ({_LIVE_RUNS}) GROUP BY run_id, stage_type"
    ).fetchall()
    return [(r["run_id"], r["stage_type"], r["n"]) for r in rows]


def checkpoint_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """(checkpoints reached, still pending an answer)."""
    row = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN human_response IS NULL THEN 1 ELSE 0 END) pending "
        f"FROM gate_results WHERE needs_human = 1 AND run_id IN ({_LIVE_RUNS})"
    ).fetchone()
    return row["n"] or 0, row["pending"] or 0


def usage_totals(conn: sqlite3.Connection, run_id: int | None = None) -> dict[str, Any]:
    """Agent-call usage, over one run or the whole history.

    Keys: calls, calls_with_cost, cost_usd, tokens_in, tokens_out.
    """
    where, params = (
        ("WHERE run_id = ?", (run_id,)) if run_id is not None
        else (f"WHERE run_id IN ({_LIVE_RUNS})", ())
    )
    row = conn.execute(
        "SELECT COUNT(*) n, COUNT(cost_usd) with_cost, "
        "COALESCE(SUM(cost_usd),0) cost, COALESCE(SUM(tokens_in),0) ti, "
        f"COALESCE(SUM(tokens_out),0) to_ FROM agent_logs {where}",
        params,
    ).fetchone()
    return {
        "calls": row["n"] or 0,
        "calls_with_cost": row["with_cost"] or 0,
        "cost_usd": row["cost"],
        "tokens_in": row["ti"],
        "tokens_out": row["to_"],
    }


# ── HTML report ───────────────────────────────────────────────────


def runs_overview(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every run with its request and project, newest first."""
    rows = conn.execute(
        """
        SELECT
            pr.id,
            pr.story_id,
            pr.project_id,
            pr.status,
            pr.current_stage,
            pr.started_at,
            pr.finished_at,
            pr.error,
            s.request,
            COALESCE(p.name, 'Unassigned') AS project_name,
            COALESCE(p.slug, '') AS project_slug
        FROM pipeline_runs pr
        JOIN stories s ON s.id = pr.story_id
        LEFT JOIN projects p ON p.id = pr.project_id
        ORDER BY pr.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _grouped(conn: sqlite3.Connection, table: str) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute(f"SELECT * FROM {table} ORDER BY run_id, id").fetchall():
        data = dict(row)
        grouped[data["run_id"]].append(data)
    return grouped


def agent_logs_by_run(conn: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    return _grouped(conn, "agent_logs")


def gate_results_by_run(conn: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    return _grouped(conn, "gate_results")


# ── doctor / legacy import: look at a database without migrating it ─


def read_only_summary(db: Path) -> tuple[int, list[tuple[str, str]]]:
    """(run count, [(slug, repo_path)]) from a home DB opened READ-ONLY, so the
    caller never creates or migrates it."""
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        runs = conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
        projects = conn.execute(
            "SELECT slug, repo_path FROM projects ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return int(runs), [(str(slug), str(repo or "")) for slug, repo in projects]


def read_only_projects(db: Path) -> list[dict[str, Any]]:
    """Every projects row from a DB opened READ-ONLY (not even a WAL checkpoint)."""
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id")]
    finally:
        conn.close()


def has_history(db: Path) -> bool:
    """Whether a DB holds any project, story or run (unreadable counts as yes, so a
    caller refuses rather than overwrites it)."""
    if not db.is_file():
        return False
    try:
        conn = sqlite3.connect(str(db))
        try:
            for table in ("projects", "stories", "pipeline_runs"):
                try:
                    if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                        return True
                except sqlite3.OperationalError:
                    continue  # table not created yet
        finally:
            conn.close()
    except sqlite3.Error:
        return True
    return False


def copy_database(source: Path, target: Path) -> None:
    """Copy a live SQLite DB with the backup API (WAL-safe, unlike a file copy)."""
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
