"""Accessors for the `projects` table (schema in `state.db.SCHEMA`).

Path columns are stored as given: `workspace.projects` decides whether a location
is written relative to $FACTORY_HOME and resolves it on the way back out.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def next_project_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT id FROM projects WHERE id LIKE 'PROJ-%'").fetchall()
    max_seen = 0
    for row in rows:
        try:
            max_seen = max(max_seen, int(row["id"].split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return f"PROJ-{max_seen + 1:03d}"


def slug_taken(conn: sqlite3.Connection, slug: str) -> bool:
    return conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone() is not None


def insert_project(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    slug: str,
    name: str,
    repo_path: str,
    spec_path: str | None,
) -> None:
    """Register a project. Raises sqlite3.IntegrityError on a duplicate slug/id."""
    now = conn.execute("SELECT datetime('now') AS now").fetchone()["now"]
    conn.execute(
        """
        INSERT INTO projects
            (id, slug, name, repo_path, spec_path, status, created_at, updated_at)
        VALUES
            (?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (project_id, slug, name, repo_path, spec_path, now, now),
    )


def get_project_row(conn: sqlite3.Connection, ref: str) -> dict[str, Any] | None:
    """A project by id or slug, as stored."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ? OR slug = ?", (ref, ref)
    ).fetchone()
    return dict(row) if row else None


def list_project_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id").fetchall()]


def list_projects_with_run_counts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.*, COUNT(pr.id) AS run_count
        FROM projects p
        LEFT JOIN pipeline_runs pr ON pr.project_id = p.id
        GROUP BY p.id
        ORDER BY p.id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def relocate_project(
    conn: sqlite3.Connection, project_id: str, repo_path: str, spec_path: str | None
) -> None:
    """Point a project at a new repository location (legacy import, path migration)."""
    conn.execute(
        "UPDATE projects SET repo_path = ?, spec_path = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (repo_path, spec_path, project_id),
    )


def set_spec_path(conn: sqlite3.Connection, project_id: str, spec_path: str) -> None:
    """Register the project's spec file (one written after the project was created)."""
    conn.execute(
        "UPDATE projects SET spec_path = ?, updated_at = datetime('now') WHERE id = ?",
        (spec_path, project_id),
    )
