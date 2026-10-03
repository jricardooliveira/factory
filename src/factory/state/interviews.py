"""Accessors for the intake interview (`interview_answers`, `interview_turns`).

Keyed by project, not run: the interview happens before any story or run exists.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_answer(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    topic: str,
    question: str,
    options: list[str],
    answer: str,
    assumed: bool,
) -> None:
    conn.execute(
        """
        INSERT INTO interview_answers
            (project_id, topic, question, options, answer, assumed, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, topic, question, json.dumps(options), answer, int(assumed), _now()),
    )


def list_answers(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """A project's answers in the order they were given."""
    rows = conn.execute(
        "SELECT * FROM interview_answers WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    return [
        {**dict(r), "options": json.loads(r["options"]), "assumed": bool(r["assumed"])}
        for r in rows
    ]


def log_turn(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    prompt: str,
    output_text: str | None,
    model_name: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    duration_secs: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO interview_turns
            (project_id, prompt, output_text, model_name, tokens_in, tokens_out,
             cost_usd, duration_secs, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, prompt, output_text, model_name, tokens_in, tokens_out,
         cost_usd, duration_secs, _now()),
    )


def turn_usage_rows(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """Usage of every intake call (interview, stack, backlog) — priced like agent_logs rows."""
    rows = conn.execute(
        "SELECT tokens_in, tokens_out, cost_usd, model_name FROM interview_turns"
        " WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]
