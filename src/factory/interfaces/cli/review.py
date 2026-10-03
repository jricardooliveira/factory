"""Commands that inspect and curate runs: review, list, queue, dismiss, reconcile."""

from __future__ import annotations

from factory import runs
from factory.domain.agent_output import parse_agent_json
from factory.interfaces import render
from factory.interfaces.cli.common import db_path, fail, run_id_arg
from factory.interfaces.render.review import render_flow, render_timeline
from factory.runs import queries
from factory.runs.lifecycle import DEFAULT_STALE_SECS


def review_command(args: list[str]) -> None:
    """`factory review <run_id> [--raw]`: everything a run did, agent by agent."""
    run_id = run_id_arg(args, "Usage: factory review <run_id> [--raw]")
    show_raw = "--raw" in args
    review = queries.review_bundle(run_id, db_path=db_path())
    if review is None:
        render.print_error(f"No run found with id #{run_id}")
        return
    render.print_run_review(
        review.run,
        review.logs,
        review.gates,
        flow=render_flow(review.stages),
        timeline=render_timeline(review.timeline),
        trust_package=review.trust_package,
        trust_issues=review.trust_issues,
        parse_output=parse_agent_json,
        show_raw=show_raw,
    )


def list_command(args: list[str]) -> None:
    """`factory list`: every pipeline run."""
    render.print_runs(queries.all_runs(db_path=db_path()))


def queue_command(args: list[str]) -> None:
    """`factory queue`: parked runs awaiting sign-off, plus runs needing attention."""
    parked_ctx, attention_ctx = queries.queue(db_path=db_path())
    render.print_queue(parked_ctx, attention_ctx)


def dismiss_command(args: list[str]) -> None:
    """`factory dismiss <run_id>`: archive a finished run off the board (non-destructive).

    The policy (which runs may be dismissed) is `runs.dismiss_run`'s, shared with
    the board.
    """
    run_id = run_id_arg(args, "Usage: factory dismiss <run_id>")
    try:
        returned = runs.dismiss_run(run_id, db_path=db_path())
    except runs.RunError as exc:
        fail(str(exc))
    render.console.print(f"[green]Run #{run_id} dismissed (archived).[/green]"
                         + (" Its story is back in the backlog: factory next <project>."
                            if returned else ""))


def reconcile_command(args: list[str]) -> None:
    """`factory reconcile [--older-than S]`: mark stale 'running' runs (process died) failed."""
    older_than = DEFAULT_STALE_SECS
    if "--older-than" in args:
        idx = args.index("--older-than")
        if idx + 1 < len(args):
            try:
                older_than = float(args[idx + 1])
            except ValueError:
                fail("--older-than expects seconds (a number)")
    ids = runs.reconcile_stale(older_than, db_path=db_path())
    if ids:
        render.console.print(f"[yellow]Reconciled {len(ids)} stale run(s): {ids}[/yellow]")
    else:
        render.console.print(f"[dim]No runs stuck 'running' longer than {int(older_than)}s.[/dim]")
