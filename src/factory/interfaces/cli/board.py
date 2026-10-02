"""Commands that show the whole factory: the board (TUI or plain) and the HTML report."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from rich.console import Group
from rich.live import Live

from factory.interfaces import render
from factory.interfaces.cli.common import db_path, fail
from factory.runs import queries


def _has_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _board_interval(args: list[str]) -> float:
    """Validate board options and return `--interval` (seconds, default 2)."""
    interval, i = 2.0, 0
    while i < len(args):
        if args[i] in ("--once", "--plain"):
            i += 1
        elif args[i] == "--interval":
            value = args[i + 1] if i + 1 < len(args) else ""
            try:
                interval = float(value)
            except ValueError:
                fail(f"--interval needs a number of seconds, got '{value}'")
            i += 2
        else:
            # An ignored option used to fall through to the TUI (`board --help`).
            fail(f"Unknown board option: {args[i]}")
    return interval


def board_command(args: list[str]) -> None:
    """`factory board [--once | --plain] [--interval S]`."""
    interval = _board_interval(args)
    if "--once" in args:
        show_board(once=True)
    elif "--plain" in args:
        show_board(once=False, interval=interval)
    elif not _has_terminal():
        # A full-screen TUI with no terminal (a pipe, a script) never exits and
        # spins a CPU core; one snapshot is the only useful thing to print.
        show_board(once=True)
    else:
        try:
            from factory.interfaces.board.tui import run_board_tui

            run_board_tui(db_path())
        except ImportError:
            render.console.print("[yellow]textual not installed — falling back to plain board[/yellow]")
            show_board(once=False, interval=interval)


def board_snapshot() -> Group:
    """Read the factory's current state and build the plain board renderable."""
    active, attention_rows = queries.board(db_path=db_path())
    return render.board_renderable(active, attention_rows)


def show_board(once: bool = False, interval: float = 2.0) -> None:
    """Live status board. --once prints a single snapshot."""
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

    path = generate_factory_visualization(db_path(), output)
    render.print_created(path, "Factory Visualization Created")
