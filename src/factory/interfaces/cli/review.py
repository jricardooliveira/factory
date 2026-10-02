"""Commands that inspect and curate runs: review, list, queue, dismiss, reconcile."""

from __future__ import annotations

from factory.domain.agent_output import parse_agent_json
from factory.evidence import trust_package
from factory.evidence.progress import (
    render_flow,
    render_timeline,
    run_pipeline_progress,
    run_timeline,
)
from factory.interfaces import render
from factory.interfaces.cli.common import db_path, fail, run_id_arg
from factory.state.db import (
    archive_run,
    get_db,
    get_pending_human_gate,
    get_run_cost,
    get_run_gates,
    get_run_logs,
    get_runs_by_status,
    init_db,
    reconcile_stale_runs,
)


def review_command(args: list[str]) -> None:
    """`factory review <run_id> [--raw]`: everything a run did, agent by agent."""
    run_id = run_id_arg(args, "Usage: factory review <run_id> [--raw]")
    show_raw = "--raw" in args
    with get_db(db_path()) as conn:
        run = conn.execute(
            "SELECT pr.*, s.request, s.title as story_title FROM pipeline_runs pr "
            "JOIN stories s ON pr.story_id = s.id WHERE pr.id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            render.print_error(f"No run found with id #{run_id}")
            return
        run = dict(run)
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)

    pkg = trust_package.assemble(db_path(), run_id)
    render.print_run_review(
        run,
        logs,
        gates,
        flow=render_flow(run_pipeline_progress(db_path(), run_id)),
        timeline=render_timeline(run_timeline(db_path(), run_id)),
        trust_package=pkg,
        trust_issues=trust_package.validate(pkg) if pkg else [],
        parse_output=parse_agent_json,
        show_raw=show_raw,
    )


def list_command(args: list[str]) -> None:
    """`factory list`: every pipeline run."""
    init_db(db_path())
    with get_db(db_path()) as conn:
        rows = conn.execute("""
            SELECT pr.id, pr.story_id, s.request, pr.status, pr.current_stage, pr.started_at
            FROM pipeline_runs pr
            JOIN stories s ON pr.story_id = s.id
            ORDER BY pr.id
        """).fetchall()
    render.print_runs([dict(row) for row in rows])


def queue_command(args: list[str]) -> None:
    """`factory queue`: parked runs awaiting sign-off, plus runs needing attention."""
    init_db(db_path())
    with get_db(db_path()) as conn:
        parked = get_runs_by_status(conn, ["waiting_human"])
        attention = get_runs_by_status(conn, ["failed", "blocked"])
        parked_ctx = []
        for run in parked:
            gate = get_pending_human_gate(conn, run["id"])
            parked_ctx.append((run, gate, get_run_cost(conn, run["id"])))
        attention_ctx = [(run, get_run_cost(conn, run["id"])) for run in attention]
    render.print_queue(parked_ctx, attention_ctx)


def dismiss_command(args: list[str]) -> None:
    """`factory dismiss <run_id>`: archive a run off the board (non-destructive)."""
    run_id = run_id_arg(args, "Usage: factory dismiss <run_id>")
    init_db(db_path())
    with get_db(db_path()) as conn:
        row = conn.execute("SELECT id FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            fail(f"No run found with id #{run_id}")
        archive_run(conn, run_id)
    render.console.print(f"[green]Run #{run_id} dismissed (archived).[/green]")


def reconcile_command(args: list[str]) -> None:
    """`factory reconcile [--older-than S]`: mark stale 'running' runs (process died) failed."""
    older_than = 3600.0
    if "--older-than" in args:
        idx = args.index("--older-than")
        if idx + 1 < len(args):
            try:
                older_than = float(args[idx + 1])
            except ValueError:
                fail("--older-than expects seconds (a number)")
    init_db(db_path())
    with get_db(db_path()) as conn:
        ids = reconcile_stale_runs(conn, older_than_secs=older_than)
    if ids:
        render.console.print(f"[yellow]Reconciled {len(ids)} stale run(s): {ids}[/yellow]")
    else:
        render.console.print(f"[dim]No runs stuck 'running' longer than {int(older_than)}s.[/dim]")
