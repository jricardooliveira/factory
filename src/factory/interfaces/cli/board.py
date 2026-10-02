"""Commands that show the whole factory: the board (TUI or plain) and the HTML report."""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Group
from rich.live import Live

from factory.interfaces import render
from factory.interfaces.cli.common import DB_PATH, fail
from factory.state.db import (
    get_db,
    get_pending_human_gate,
    get_run_cost,
    get_runs_by_status,
    init_db,
)


def board_command(args: list[str]) -> None:
    """`factory board [--once | --plain] [--interval S]`."""
    interval = 2.0
    if "--interval" in args:
        idx = args.index("--interval")
        if idx + 1 < len(args):
            interval = float(args[idx + 1])
    if "--once" in args:
        show_board(once=True)
    elif "--plain" in args:
        show_board(once=False, interval=interval)
    else:
        try:
            from factory.interfaces.board.tui import run_board_tui

            run_board_tui(DB_PATH)
        except ImportError:
            render.console.print("[yellow]textual not installed — falling back to plain board[/yellow]")
            show_board(once=False, interval=interval)


def board_snapshot() -> Group:
    """Read the factory's current state and build the plain board renderable."""
    with get_db(DB_PATH) as conn:
        running = get_runs_by_status(conn, ["running", "waiting_human"])
        attention = get_runs_by_status(conn, ["failed", "blocked"])
        active = [
            (r, get_pending_human_gate(conn, r["id"]) if r["status"] == "waiting_human" else None,
             get_run_cost(conn, r["id"]))
            for r in running
        ]
        attention_rows = [(r, get_run_cost(conn, r["id"])) for r in attention]
    return render.board_renderable(active, attention_rows)


def show_board(once: bool = False, interval: float = 2.0) -> None:
    """Live status board. --once prints a single snapshot."""
    init_db(DB_PATH)
    console = render.console
    if once:
        console.print(board_snapshot())
        return
    console.print("[dim]Live board — press Ctrl-C to exit.[/dim]")
    try:
        with Live(board_snapshot(), console=console, refresh_per_second=4, screen=False) as live:
            while True:
                time.sleep(interval)
                live.update(board_snapshot())
    except KeyboardInterrupt:
        console.print("\n[dim]board closed[/dim]")


def visualize_command(args: list[str]) -> None:
    """`factory visualize [--output path]`: a static HTML report of factory state."""
    output = Path("factory-visualization.html")
    i = 0
    while i < len(args):
        if args[i] == "--output" and i + 1 < len(args):
            output = Path(args[i + 1])
            i += 2
        else:
            fail(f"Unknown visualize option: {args[i]}")

    from factory.interfaces.board.html_report import generate_factory_visualization

    path = generate_factory_visualization(DB_PATH, output)
    render.print_created(path, "Factory Visualization Created")
