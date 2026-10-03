"""Curating runs: dismiss and reconcile, with ONE policy for every surface.

The CLI and the Textual board used to carry their own copies — and they
disagreed: the board refused to dismiss a run awaiting a decision, while
`factory dismiss` archived anything, a parked or a still-running run included.
"""

from __future__ import annotations

from pathlib import Path

from factory.agent_config.settings import settings
from factory.runs.backlog import return_story_to_backlog
from factory.runs.events import RunError
from factory.state.db import archive_run, get_db, get_run, init_db, reconcile_stale_runs

# Statuses a run may NOT be dismissed from, and what the operator should do instead.
_NOT_DISMISSIBLE = {
    "waiting_human": "is awaiting your decision — approve or reject it",
    "running": "is still running — let it finish, or `factory reconcile` it if its process died",
}


def dismiss_run(run_id: int, *, db_path: Path) -> bool:
    """Archive a finished run off the board (non-destructive). Raises RunError.

    A failed/blocked run's backlog story goes back to 'approved' for `factory next`;
    True when that happened.
    """
    init_db(db_path)
    with get_db(db_path) as conn:
        run = get_run(conn, run_id)
        if not run:
            raise RunError(f"No run found with id #{run_id}")
        why = _NOT_DISMISSIBLE.get(run["status"])
        if why:
            raise RunError(f"Run #{run_id} {why}.")
        archive_run(conn, run_id)
    if run["status"] in ("failed", "blocked"):
        return return_story_to_backlog(run_id, db_path=db_path)
    return False


def reconcile_stale(older_than_secs: float | None = None, *, db_path: Path) -> list[int]:
    """Mark runs stuck 'running' longer than the cutoff (default: factory.toml
    [timeouts] stale_run) as failed (their process died)."""
    if older_than_secs is None:
        older_than_secs = float(settings().timeouts.stale_run)
    init_db(db_path)
    with get_db(db_path) as conn:
        return reconcile_stale_runs(conn, older_than_secs=older_than_secs)
