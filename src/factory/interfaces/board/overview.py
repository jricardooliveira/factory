"""Overview, the board's home: what needs you, the ONE next start, what is working, and
what happened since you left. Two equal columns at ≥ 120 columns, stacked below."""

from __future__ import annotations

from datetime import datetime, timezone

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from factory.domain.board import age
from factory.interfaces.board import texts
from factory.interfaces.board.views_base import BoardView
from factory.runs.board import Board


class OverviewView(BoardView):
    DEFAULT_CSS = """
    OverviewView { layout: horizontal; }
    OverviewView #ov-left, OverviewView #ov-right { width: 1fr; height: 1fr; }
    OverviewView #ov-needs { height: 11; }
    OverviewView #ov-next { height: 7; }
    OverviewView #ov-stories { height: 1fr; }
    OverviewView #ov-working { height: 16; }
    OverviewView #ov-since { height: 1fr; }
    OverviewView #ov-needs-list { height: 1fr; }
    OverviewView #ov-next-row { height: 1; margin-top: 1; }
    OverviewView #ov-next-row Static { width: auto; }
    OverviewView.narrow { layout: vertical; overflow-y: auto; }
    OverviewView.narrow #ov-left, OverviewView.narrow #ov-right { height: auto; }
    OverviewView.narrow #ov-needs { height: 9; }
    OverviewView.narrow #ov-next { height: 6; }
    OverviewView.narrow #ov-stories { height: 3; }
    OverviewView.narrow #ov-working { height: 8; }
    OverviewView.narrow #ov-since { height: 12; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.since: str = ""  # the last visit, ISO, set by the board
        self.ids: list[str] = []
        self.next_action = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="ov-left"):
            with Vertical(id="ov-needs", classes="panel"):
                yield OptionList(id="ov-needs-list")
                yield Static("", id="ov-needs-hint")
            with Vertical(id="ov-next", classes="panel"):
                yield Static("", id="ov-next-text")
                with Horizontal(id="ov-next-row"):
                    yield Button("", id="ov-next-button", variant="primary", compact=True)
                    yield Static("", id="ov-next-key")
            with Vertical(id="ov-stories", classes="panel"):
                yield Static("", id="ov-stories-text")
        with Vertical(id="ov-right"):
            with VerticalScroll(id="ov-working", classes="panel"):
                yield Static("", id="ov-working-text")
            with VerticalScroll(id="ov-since", classes="panel"):
                yield Static("", id="ov-since-text")

    def show(self, model: Board) -> None:
        super().show(model)
        narrow = self.screen.has_class("narrow") if self.is_mounted else False
        self.set_class(narrow, "narrow")
        now = datetime.now(timezone.utc)
        self._needs(model, now)
        self._next(model)
        self._stories(model)
        self.query_one("#ov-working").border_title = "Working now"
        self.query_one("#ov-working-text", Static).update(
            Content.from_markup(texts.working_lines(model, now)))
        since = texts.local(self.since) if self.since else "today"
        self.query_one("#ov-since").border_title = f"Since you left · {since}"
        self.query_one("#ov-since-text", Static).update(
            Content.from_markup(texts.activity_lines(model.activity) or "[$dim]Nothing yet.[/]"))

    def _needs(self, model: Board, now: datetime) -> None:
        panel = self.query_one("#ov-needs")
        panel.border_title = f"Needs you · {model.need_count}"
        listing = self.query_one("#ov-needs-list", OptionList)
        hint = self.query_one("#ov-needs-hint", Static)
        width = max(30, (listing.size.width or 70) - 1)
        top = model.decisions[:5]
        ids = [d.id for d in top]
        if not top:
            listing.display = False
            hint.update(Content.from_markup("[$success]✓[/] Nothing needs you.\n"
                                            "[$dim]Questions and approvals will appear here.[/]"))
            self.ids = []
            return
        listing.display = True
        options = []
        for d in top:
            left = f"{texts.kind_label(d)}{' ' * max(1, 12 - len(Content.from_markup(texts.kind_label(d)).plain))}"
            options.append(Option(Content.from_markup(texts._pad(
                left + texts.e(d.short), f"[$dim]{age(d.created_at, now)}[/]", width)), id=d.id))
        keep = listing.highlighted
        if ids == self.ids:
            for i, option in enumerate(options):
                listing.replace_option_prompt_at_index(i, option.prompt)
        else:
            listing.clear_options()
            listing.add_options(options)
            self.ids = ids
            keep = 0
        listing.highlighted = keep or 0
        more = len(model.decisions) > 5
        hint.update(Content.from_markup(
            f"[$dim]enter open · n see all {model.need_count}[/]" if more
            else "[$dim]enter open · n needs you[/]"))

    def _next(self, model: Board) -> None:
        step = model.next
        panel = self.query_one("#ov-next")
        panel.border_title = "Next to start"
        text = self.query_one("#ov-next-text", Static)
        button = self.query_one("#ov-next-button", Button)
        key = self.query_one("#ov-next-key", Static)
        if step is None:
            text.update("")
            button.display = False
            return
        mark = {"interview": "[$success]▶[/] ", "batch": "[$success]▶[/] ", "backlog": "[$success]▶[/] ",
                "pause": "[$warning]⏸[/] "}.get(step.action, "")
        head = (f"{mark}[b $bright]{texts.e(step.title)}[/]" if step.action
                else f"[$dim]{texts.e(step.title)}[/]")
        if step.action and step.reason and step.action != "batch":
            head += f" · {texts.e(step.reason)}" if step.action != "pause" else f" {texts.e(step.reason)}"
        lines = [head]
        if step.action == "batch" and step.reason or not step.action and step.reason:
            lines.append(f"  [$dim]{texts.e(step.reason)}[/]")
        text.update(Content.from_markup("\n".join(lines)))
        button.display = bool(step.button)
        button.label = step.button
        self.next_action = step.action
        key.update(Content.from_markup(f" [$dim]{step.key}[/]" if step.key else ""))

    def _stories(self, model: Board) -> None:
        panel = self.query_one("#ov-stories")
        panel.border_title = "Stories"
        self.query_one("#ov-stories-text", Static).update(
            Content.from_markup(texts.story_counts(model.stories)))

    def keys(self) -> list[tuple[str, str]]:
        return [("↑↓", "move"), ("enter", "open"), ("n", "needs you"), ("b", "batch"),
                ("P", "pause"), ("esc", "all runs")]

    def focus_first(self) -> None:
        listing = self.query_one("#ov-needs-list", OptionList)
        if listing.display:
            listing.focus()

    @on(OptionList.OptionSelected, "#ov-needs-list")
    def opened(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id:
            self.screen.open_decision(event.option.id)

    @on(Button.Pressed, "#ov-next-button")
    def next_pressed(self, event: Button.Pressed) -> None:
        self.screen.start(self.next_action)
