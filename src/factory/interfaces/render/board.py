"""Queue, plain board and run list."""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from factory.interfaces.render import output


def print_queue(
    parked: list[tuple[dict, dict | None, float]], attention: list[tuple[dict, float]]
) -> None:
    """Parked runs awaiting sign-off, plus runs that need attention."""
    if not parked and not attention:
        output.console.print("[dim]Queue is empty — nothing awaiting review.[/dim]")
        return

    if parked:
        output.console.print(Rule("[bold]⏸  Awaiting your sign-off[/bold]"))
        for run, gate, cost in parked:
            stage = run.get("current_stage", "?")
            title = run.get("story_title") or run["story_id"]
            output.console.print(
                f"  [bold]#{run['id']}[/bold] [cyan]{run['story_id']}[/cyan] "
                f"{title}  [dim](stage: {stage}, spent ${cost:.4f})[/dim]"
            )
            if gate and gate.get("reason"):
                output.console.print(f"    [dim]why:[/dim] {gate['reason']}")
            if gate and gate.get("human_questions"):
                for q in gate["human_questions"].split("\n\n"):
                    if q.strip():
                        output.console.print(f"    [yellow]?[/yellow] {q.strip()}")
            output.console.print(
                f"    [green]factory approve {run['id']}[/green] · "
                f"[red]factory reject {run['id']} \"<feedback>\"[/red]"
            )
        output.console.print()

    if attention:
        output.console.print(Rule("[bold]⚠  Needs attention[/bold]"))
        for run, cost in attention:
            title = run.get("story_title") or run["story_id"]
            err = (run.get("error") or "").strip()
            if len(err) > 100:
                err = err[:100] + "…"
            output.console.print(
                f"  [bold]#{run['id']}[/bold] [cyan]{run['story_id']}[/cyan] {title}  "
                f"[{output.verdict_style(run['status'])}]{run['status'].upper()}[/{output.verdict_style(run['status'])}] "
                f"[dim](spent ${cost:.4f})[/dim]"
            )
            if err:
                output.console.print(f"    [dim]{err}[/dim]")
            output.console.print(f"    [dim]factory review {run['id']} · factory replay {run['id']}[/dim]")
        output.console.print()


def _elapsed(iso_start: str) -> str:
    from datetime import datetime, timezone

    try:
        start = datetime.fromisoformat(iso_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - start).total_seconds())
    except (ValueError, TypeError):
        return "?"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def board_renderable(
    active: list[tuple[dict, dict | None, float]], attention_rows: list[tuple[dict, float]]
) -> Group:
    """A snapshot renderable of the factory's current state (the plain board)."""
    blocks: list = [Rule("[bold]🏭 Factory Board[/bold]")]

    active_tbl = Table(title="In flight", border_style="cyan", expand=True)
    active_tbl.add_column("#", justify="right", style="bold")
    active_tbl.add_column("Story", style="cyan")
    active_tbl.add_column("State")
    active_tbl.add_column("Stage / waiting on")
    active_tbl.add_column("Elapsed", justify="right")
    active_tbl.add_column("Cost", justify="right")
    active_tbl.add_column("Your move", style="green")
    if not active:
        active_tbl.add_row("—", "[dim]nothing running[/dim]", "", "", "", "", "")
    for run, gate, cost in active:
        parked = run["status"] == "waiting_human"
        state = "[yellow]⏸ NEEDS YOU[/yellow]" if parked else "[cyan]running[/cyan]"
        detail = (gate.get("reason", "")[:48] if gate else run.get("current_stage", "?"))
        move = (
            f"approve {run['id']}  ·  reject {run['id']} \"…\"" if parked else "[dim]wait[/dim]"
        )
        active_tbl.add_row(
            str(run["id"]), run.get("story_title") or run["story_id"], state,
            detail, _elapsed(run["started_at"]), f"${cost:.4f}", move,
        )
    blocks.append(active_tbl)

    # Detail of what each parked run is asking
    for run, gate, _ in active:
        if run["status"] == "waiting_human" and gate and gate.get("human_questions"):
            qs = [q.strip() for q in gate["human_questions"].split("\n\n") if q.strip()]
            body = "\n".join(f"[yellow]?[/yellow] {q}" for q in qs)
            blocks.append(
                Panel(body, title=f"⏸ Run #{run['id']} needs your input", border_style="yellow")
            )

    if attention_rows:
        att = Table(title="Needs attention", border_style="red", expand=True)
        att.add_column("#", justify="right", style="bold")
        att.add_column("Story", style="cyan")
        att.add_column("Status")
        att.add_column("Why")
        att.add_column("Your move", style="dim")
        for run, cost in attention_rows[:10]:
            err = (run.get("error") or "").strip().replace("\n", " ")
            if len(err) > 60:
                err = err[:60] + "…"
            att.add_row(
                str(run["id"]), run.get("story_title") or run["story_id"],
                f"[{output.verdict_style(run['status'])}]{run['status']}[/{output.verdict_style(run['status'])}]",
                err, f"review {run['id']} · replay {run['id']}",
            )
        blocks.append(att)

    blocks.append(Text("commands: factory approve/reject <#> · review <#> · replay <#>", style="dim"))
    return Group(*blocks)


def print_runs(rows: list[dict]) -> None:
    """`factory list`: every pipeline run."""
    if not rows:
        output.console.print("[dim]No runs found. Run a pipeline first.[/dim]")
        return

    table = Table(title="📦 Pipeline Runs", border_style="cyan")
    table.add_column("#", style="bold", justify="right")
    table.add_column("Story", style="cyan")
    table.add_column("Request")
    table.add_column("Status", justify="center")
    table.add_column("Last Stage")
    table.add_column("Started", style="dim")

    for row in rows:
        req = row["request"]
        if len(req) > 60:
            req = req[:57] + "..."
        st = row["status"]
        style = output.verdict_style(st)
        table.add_row(
            str(row["id"]),
            row["story_id"],
            req,
            Text(st.upper(), style=style),
            row["current_stage"],
            row["started_at"][:19],
        )

    output.console.print(table)
    output.console.print("\n  [dim]Use[/dim] factory review <id> [dim]to inspect a run[/dim]")
    output.console.print("  [dim]Use[/dim] factory review <id> --raw [dim]to see full agent output[/dim]")
