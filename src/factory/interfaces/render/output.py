"""The one shared Console and the helpers every render module uses.

Render modules reach the console as `output.console` at CALL time, never via a
`from ... import console` copy, so a test (or a caller) that swaps
`factory.interfaces.render.output.console` captures every module's output.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()


def print_error(message: str) -> None:
    console.print(f"[red]{message}[/red]")


def verdict_style(verdict: str) -> str:
    v = verdict.lower()
    if v in ("pass", "complete", "done", "completed"):
        return "bold green"
    elif v in ("warn", "waiting_human"):
        return "bold yellow"
    elif v in ("fail", "failed", "error", "blocked"):
        return "bold red"
    return "white"


def gate_icon(passed: bool) -> str:
    return "✅" if passed else "❌"


def print_created(path: Path, title: str) -> None:
    """A green panel naming a file a command just wrote."""
    console.print(Panel(str(path), title=title, border_style="green"))


def print_report_written(path: Path) -> None:
    console.print(f"  [dim]Report written to {path}[/dim]")
