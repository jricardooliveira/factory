"""Activity (every event, newest first, local time, by day) and All runs (the history)."""

from __future__ import annotations

from datetime import datetime, timedelta

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.content import Content
from textual.widgets import Static

from factory.interfaces.board import texts
from factory.interfaces.board.views_base import BoardView
from factory.runs.board import Board


def _day(stamp: str) -> str:
    day = datetime.fromisoformat(stamp).astimezone().date()
    today = datetime.now().astimezone().date()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return day.strftime("%a %-d %b")


class ActivityView(BoardView):
    DEFAULT_CSS = "ActivityView #ac-panel { height: 1fr; }"

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="ac-panel", classes="panel"):
            yield Static("", id="ac-text")

    def show(self, model: Board) -> None:
        super().show(model)
        self.query_one("#ac-panel").border_title = "Activity · local time"
        lines, last = [], ""
        for stamp, text in model.activity:
            day = _day(stamp)
            if day != last:
                if lines:
                    lines.append("")
                lines.append(f"[$dim]{day}[/]")
                last = day
            body = f"[$error]✗[/] {texts.e(text[2:])}" if text.startswith("✗ ") else texts.e(text)
            lines.append(f"  [$dim]{texts.local(stamp)}[/]  {body}")
        self.query_one("#ac-text", Static).update(
            Content.from_markup("\n".join(lines) or "[$dim]Nothing has happened yet.[/]"))

    def keys(self) -> list[tuple[str, str]]:
        return [("↑↓", "scroll"), ("o", "overview"), ("esc", "back")]

    def focus_first(self) -> None:
        self.query_one("#ac-panel").focus()


class AllRunsView(BoardView):
    DEFAULT_CSS = "AllRunsView #ar-panel { height: 1fr; }"

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="ar-panel", classes="panel"):
            yield Static("", id="ar-text")

    def show(self, model: Board) -> None:
        super().show(model)
        self.query_one("#ar-panel").border_title = "All runs"
        lines = [f"[$dim]{'Story':<34}{'State':<36}Spend[/]"]
        for run in model.history:
            name = f"#{run['n']} {run['title']}" if run["n"] is not None else f"Run #{run['run']} {run['title']}"
            spend = f"${run['spend']:.2f}" if run["spend"] else "—"
            lines.append(f"{texts.e(texts._clip(name, 33)):<34}{texts.e(run['state']):<36}{spend}")
        if not model.history:
            lines.append("[$dim]No runs yet.[/]")
        lines += ["", "[$dim]Checkpoint approvals now live in Needs you. This view is history.[/]"]
        self.query_one("#ar-text", Static).update(Content.from_markup("\n".join(lines)))

    def keys(self) -> list[tuple[str, str]]:
        return [("↑↓", "scroll"), ("o", "overview"), ("esc", "back")]

    def focus_first(self) -> None:
        self.query_one("#ar-panel").focus()
