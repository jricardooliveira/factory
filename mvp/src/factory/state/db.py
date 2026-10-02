"""SQLite state management for the factory pipeline."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

DEFAULT_DB_PATH = Path("factory.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    spec_path TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    request TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL REFERENCES stories(id),
    project_id TEXT REFERENCES projects(id),
    status TEXT NOT NULL DEFAULT 'running',
    current_stage TEXT NOT NULL DEFAULT 'spec-agent',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES pipeline_runs(id),
    agent TEXT NOT NULL,
    stage_type TEXT NOT NULL DEFAULT 'agent',
    input_text TEXT,
    output_text TEXT,
    verdict TEXT,
    duration_secs REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL,
    model_name TEXT,
    prompt_hash TEXT,
    agent_prompt_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES pipeline_runs(id),
    gate_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    reason TEXT,
    needs_human INTEGER NOT NULL DEFAULT 0,
    human_questions TEXT,
    human_response TEXT,
    checked_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db(path: Path = DEFAULT_DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path = DEFAULT_DB_PATH) -> None:
    with get_db(path) as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "stories", "project_id", "TEXT REFERENCES projects(id)")
        _ensure_column(conn, "pipeline_runs", "project_id", "TEXT REFERENCES projects(id)")
        _ensure_column(conn, "agent_logs", "tokens_in", "INTEGER")
        _ensure_column(conn, "agent_logs", "tokens_out", "INTEGER")
        _ensure_column(conn, "agent_logs", "cost_usd", "REAL")
        _ensure_column(conn, "agent_logs", "model_name", "TEXT")
        _ensure_column(conn, "agent_logs", "prompt_hash", "TEXT")
        _ensure_column(conn, "agent_logs", "agent_prompt_hash", "TEXT")
        # The commit the run started from: lets the trust package measure THIS
        # run's change set from git instead of every factory commit ever made.
        _ensure_column(conn, "pipeline_runs", "base_commit", "TEXT")
        # When the operator answered a checkpoint. Without it the factory's own
        # north-star metric ("trust per interruption") is not just uncomputed
        # but unrecordable — there is no time to measure the interruption from.
        _ensure_column(conn, "gate_results", "responded_at", "TEXT")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_story(
    conn: sqlite3.Connection,
    story_id: str,
    title: str,
    request: str,
    project_id: str | None = None,
) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO stories (id, project_id, title, request, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'new', ?, ?)",
        (story_id, project_id, title, request, now, now),
    )


def start_run(
    conn: sqlite3.Connection,
    story_id: str,
    project_id: str | None = None,
    base_commit: str | None = None,
) -> int:
    """Open a run. `base_commit` is the target repo's HEAD at start, so the run's
    change set can later be measured from git against its own baseline."""
    now = _now()
    cursor = conn.execute(
        "INSERT INTO pipeline_runs (story_id, project_id, status, current_stage, started_at, base_commit)"
        " VALUES (?, ?, 'running', 'spec-agent', ?, ?)",
        (story_id, project_id, now, base_commit),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def log_agent(
    conn: sqlite3.Connection,
    run_id: int,
    agent: str,
    input_text: str,
    output_text: str,
    verdict: str | None = None,
    duration_secs: float | None = None,
    stage_type: str = "agent",
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    model_name: str | None = None,
    agent_prompt_hash: str | None = None,
) -> int:
    now = _now()
    prompt_hash = (
        hashlib.sha256(input_text.encode("utf-8")).hexdigest()[:16] if input_text else None
    )
    cursor = conn.execute(
        "INSERT INTO agent_logs (run_id, agent, stage_type, input_text, output_text, verdict, "
        "duration_secs, tokens_in, tokens_out, cost_usd, model_name, prompt_hash, agent_prompt_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id, agent, stage_type, input_text, output_text, verdict, duration_secs,
            tokens_in, tokens_out, cost_usd, model_name, prompt_hash, agent_prompt_hash, now,
        ),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def log_gate(
    conn: sqlite3.Connection,
    run_id: int,
    gate_name: str,
    passed: bool,
    reason: str,
    needs_human: bool = False,
    human_questions: str | None = None,
) -> int:
    now = _now()
    cursor = conn.execute(
        "INSERT INTO gate_results (run_id, gate_name, passed, reason, needs_human, human_questions, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, gate_name, int(passed), reason, int(needs_human), human_questions, now),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def respond_to_gate(conn: sqlite3.Connection, gate_id: int, response: str) -> None:
    """Record the operator's checkpoint decision, stamped with when they answered."""
    conn.execute(
        "UPDATE gate_results SET human_response = ?, responded_at = ? WHERE id = ?",
        (response, _now(), gate_id),
    )


def get_answered_human_gate(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    """The most recently ANSWERED checkpoint for a run.

    Lets a run that died after the operator answered be picked back up. Without
    it, a transient provider error between "reject" and the re-run left the
    decision stranded: the run was no longer `waiting_human`, so approve/reject
    refused it, and nothing else read `human_response`. An interruption is the
    scarcest thing this factory spends; losing one to someone else's 500 is not
    acceptable. Newest first, matching the resume rule everywhere else.
    """
    row = conn.execute(
        "SELECT * FROM gate_results WHERE run_id = ? AND needs_human = 1 "
        "AND human_response IS NOT NULL ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return dict(row) if row else None


def get_pending_human_gate(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM gate_results WHERE run_id = ? AND needs_human = 1 AND human_response IS NULL ORDER BY id LIMIT 1",
        (run_id,),
    ).fetchone()
    return dict(row) if row else None


def update_run_stage(conn: sqlite3.Connection, run_id: int, stage: str) -> None:
    conn.execute(
        "UPDATE pipeline_runs SET current_stage = ? WHERE id = ?",
        (stage, run_id),
    )


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, error: str | None = None) -> None:
    now = _now()
    conn.execute(
        "UPDATE pipeline_runs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
        (status, now, error, run_id),
    )


def update_story_status(conn: sqlite3.Connection, story_id: str, status: str) -> None:
    now = _now()
    conn.execute(
        "UPDATE stories SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, story_id),
    )


def update_story_title(conn: sqlite3.Connection, story_id: str, title: str) -> None:
    now = _now()
    conn.execute(
        "UPDATE stories SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, story_id),
    )


def get_runs_by_status(
    conn: sqlite3.Connection, statuses: list[str]
) -> list[dict[str, Any]]:
    """Runs in the given statuses, joined with story + project, newest first."""
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT pr.*, s.title AS story_title, s.request,
               p.slug AS project_slug, p.name AS project_name
        FROM pipeline_runs pr
        JOIN stories s ON pr.story_id = s.id
        LEFT JOIN projects p ON pr.project_id = p.id
        WHERE pr.status IN ({placeholders})
        ORDER BY pr.id DESC
        """,
        statuses,
    ).fetchall()
    return [dict(r) for r in rows]


def archive_run(conn: sqlite3.Connection, run_id: int) -> None:
    """Dismiss a run from the board (terminal, hidden state). Non-destructive."""
    now = _now()
    conn.execute(
        "UPDATE pipeline_runs SET status = 'archived', finished_at = COALESCE(finished_at, ?) "
        "WHERE id = ?",
        (now, run_id),
    )


def reconcile_stale_runs(conn: sqlite3.Connection, older_than_secs: float = 3600) -> list[int]:
    """Mark runs stuck in 'running' beyond a cutoff as failed (their process died).

    Conservative by default (1h) so a genuinely long multi-task run is never
    killed. Returns the ids that were reconciled.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_secs)
    rows = conn.execute(
        "SELECT id, started_at FROM pipeline_runs WHERE status = 'running'"
    ).fetchall()
    reconciled: list[int] = []
    for row in rows:
        try:
            started = datetime.fromisoformat(row["started_at"])
        except (ValueError, TypeError):
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started < cutoff:
            finish_run(
                conn, row["id"], "failed",
                error=f"reconciled: stale running run (>{int(older_than_secs)}s, process likely died)",
            )
            reconciled.append(row["id"])
    return reconciled


def get_run_cost(conn: sqlite3.Connection, run_id: int) -> float:
    """Sum cost_usd across a run's agent logs (for the remediation budget)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM agent_logs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return float(row["total"]) if row else 0.0


def get_agent_log(conn: sqlite3.Connection, run_id: int, agent: str) -> dict[str, Any] | None:
    """Return the most recent stored log for an agent in a run (for replay)."""
    row = conn.execute(
        "SELECT * FROM agent_logs WHERE run_id = ? AND agent = ? ORDER BY id DESC LIMIT 1",
        (run_id, agent),
    ).fetchone()
    return dict(row) if row else None


def get_agent_log_by_stage(
    conn: sqlite3.Connection, run_id: int, agent: str, stage_type: str
) -> dict[str, Any] | None:
    """Return the most recent stored log for an agent+stage (per-task replay)."""
    row = conn.execute(
        "SELECT * FROM agent_logs WHERE run_id = ? AND agent = ? AND stage_type = ? "
        "ORDER BY id DESC LIMIT 1",
        (run_id, agent, stage_type),
    ).fetchone()
    return dict(row) if row else None


def get_agent_logs_for(
    conn: sqlite3.Connection, run_id: int, agent: str
) -> list[dict[str, Any]]:
    """All of one agent's logs for a run, NEWEST FIRST.

    Lets a caller walk back to the last USABLE output. A failed call is also a
    row, so "newest row" and "newest artifact" are not the same thing.
    """
    rows = conn.execute(
        "SELECT * FROM agent_logs WHERE run_id = ? AND agent = ? ORDER BY id DESC",
        (run_id, agent),
    ).fetchall()
    return [dict(r) for r in rows]


def get_run_logs(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agent_logs WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_run_gates(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM gate_results WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]
