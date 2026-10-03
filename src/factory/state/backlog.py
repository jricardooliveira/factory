"""Accessors for `backlog_stories`: a project's ordered story list.

Rows a run has started are fixed; a new approved proposal replaces only the rest.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from factory.domain.backlog import BacklogStory


def list_backlog(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM backlog_stories WHERE project_id = ? ORDER BY position, id", (project_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_backlog_row(conn: sqlite3.Connection, row_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM backlog_stories WHERE id = ?", (row_id,)).fetchone()
    return dict(row) if row else None


def replace_unstarted(
    conn: sqlite3.Connection, project_id: str, stories: list[BacklogStory]
) -> None:
    """Drop every row not 'started'; append `stories` as 'approved' after the started ones."""
    conn.execute(
        "DELETE FROM backlog_stories WHERE project_id = ? AND status != 'started'", (project_id,)
    )
    last = conn.execute(
        "SELECT COALESCE(MAX(position), 0) FROM backlog_stories WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO backlog_stories
            (project_id, position, title, request, rationale, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'approved', ?)
        """,
        [(project_id, last + n, s.title, s.request, s.rationale, now)
         for n, s in enumerate(stories, 1)],
    )


def next_approved(conn: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM backlog_stories WHERE project_id = ? AND status = 'approved'
        ORDER BY position, id LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def mark_started(
    conn: sqlite3.Connection, row_id: int, *, story_id: str | None, run_id: int | None
) -> None:
    conn.execute(
        "UPDATE backlog_stories SET status = 'started', story_id = ?, run_id = ? WHERE id = ?",
        (story_id, run_id, row_id),
    )


def return_to_backlog(conn: sqlite3.Connection, run_id: int) -> list[str]:
    """Put the story a dismissed failed/blocked run started back to 'approved'.

    It keeps that run's base_commit: the tasks it passed are committed, never
    reviewed, so the story's next run must start its review from there.
    Returns the project ids touched (their BACKLOG.md must be re-rendered).
    """
    projects = [r[0] for r in conn.execute(
        "SELECT DISTINCT project_id FROM backlog_stories WHERE run_id = ? AND status = 'started'",
        (run_id,),
    ).fetchall()]
    conn.execute(
        "UPDATE backlog_stories SET status = 'approved', story_id = NULL, run_id = NULL, "
        "base_commit = (SELECT base_commit FROM pipeline_runs WHERE id = ?) "
        "WHERE run_id = ? AND status = 'started'",
        (run_id, run_id),
    )
    return projects
