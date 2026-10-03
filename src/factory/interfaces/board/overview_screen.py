"""Visual lifecycle overview and primary next action for one project."""

from __future__ import annotations

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from factory.runs import ProjectStatus


class ProjectOverviewScreen(Screen[bool | None]):
    """Show lifecycle progress and return True when the primary action is chosen."""

    BINDINGS = [("escape", "close", "Back to board")]
    DEFAULT_CSS = """
    ProjectOverviewScreen { background: $background; }
    #overview { width: 100%; height: 1fr; padding: 1 3; }
    #overview-title { text-style: bold; color: $accent; padding: 0 0 1 0; }
    #phase-list { height: auto; border: round $panel; padding: 1 2; }
    .phase-row { height: auto; padding: 0 0 1 0; }
    #next-box { height: auto; border: round $accent; padding: 1 2; margin-top: 1; }
    #actions { height: auto; margin-top: 1; }
    #next-button { margin-right: 1; }
    """

    _PHASE_STYLE = {"done": ("✓", "green"), "now": ("▶", "bold yellow"),
                    "todo": ("○", "dim")}
    _ACTION_LABEL = {
        "interview": "Continue interview",
        "approve": "Review run",
        "wait": "Run in progress",
        "review": "Review failed run",
        "backlog": "Propose backlog",
        "next": "Start next story",
        "amend": "Amend project brief",
    }

    def __init__(self, status: ProjectStatus) -> None:
        super().__init__()
        self.status = status
        step = status.next
        self.action_label = self._ACTION_LABEL.get(step.action, "Do next step")
        self.action_disabled = step.action == "wait"

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="overview"):
            yield Static(
                f"Project overview  ·  {escape(self.status.name)} ({escape(self.status.facts.slug)})",
                id="overview-title",
            )
            with Vertical(id="phase-list"):
                for phase in self.status.phases:
                    icon, style = self._PHASE_STYLE.get(phase.state, ("·", "dim"))
                    yield Static(
                        f"[{style}]{icon}  {escape(phase.name)}[/{style}]\n"
                        f"    {escape(phase.detail)}",
                        classes="phase-row",
                    )
            with Vertical(id="next-box"):
                yield Static("NEXT STEP", classes="next-heading")
                yield Static(escape(self.status.next.why), id="next-why")
                yield Static(f"Command: {escape(self.status.next.command)}", id="next-command")
            with Horizontal(id="actions"):
                yield Button(self.action_label, id="next-button", variant="primary",
                             disabled=self.action_disabled)
                yield Button("Back to board", id="close-button")
        yield Footer()

    @on(Button.Pressed, "#next-button")
    def _do_next(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#close-button")
    def _close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
