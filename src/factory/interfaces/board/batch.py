"""The batch proposal: ready stories pre-ticked by a greedy non-overlapping pick; ticking
refuses a clash or a full batch inline; Launch proposes and launches exactly the ticks."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Button, Static

from factory.domain.board import pick_batch
from factory.domain.workflow import conflicts
from factory.interfaces.board import texts
from factory.interfaces.board.views_base import BoardView
from factory.runs.board import Board

_NOT_IN = {"needs": "needs answers", "notready": "not ready", "refining": "still refining",
           "draft": "draft · not refined yet"}


class BatchView(BoardView, can_focus=True):
    BINDINGS = [Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
                Binding("space", "tick", show=False), Binding("enter", "launch", show=False)]
    DEFAULT_CSS = """
    BatchView #bt-panel { height: 1fr; }
    BatchView #bt-actions { height: 2; }
    BatchView #bt-actions Static { width: auto; }
    BatchView #bt-actions .hint { width: 1fr; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.ticks: dict[int, bool] = {}
        self.cursor = 0
        self.message = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="bt-panel", classes="panel"):
            yield Static("", id="bt-top", classes="top")
            with VerticalScroll(classes="body"):
                yield Static("", id="bt-body")
            with Horizontal(id="bt-actions", classes="actions"):
                yield Button("Launch", id="bt-launch", variant="primary", compact=True)
                yield Static(Content.from_markup(" [$dim]enter[/]   "))
                yield Button("Cancel", id="bt-cancel", compact=True)
                yield Static(Content.from_markup(" [$dim]esc[/]"))
                yield Static(Content.from_markup("[$dim]space ticks · max 2[/]"), classes="hint")

    def reset(self, model: Board) -> None:
        """Opening the view: tick the design's greedy pick."""
        picked, _why = pick_batch(model.ready_plans, limit=model.limit)
        self.ticks = {p.backlog_id: p in picked for p in model.ready_plans}
        self.cursor, self.message = 0, ""

    def show(self, model: Board) -> None:
        super().show(model)
        ready = model.ready_plans
        for p in ready:
            self.ticks.setdefault(p.backlog_id, False)
        self.cursor = min(self.cursor, max(0, len(ready) - 1))
        panel = self.query_one("#bt-panel")
        panel.border_title, panel.border_subtitle = "Batch proposal", f"{len(ready)} ready"
        self.query_one("#bt-top", Static).update(
            f"Runs up to {model.limit} stories at the same time. Picked so they don’t change the same files.")
        titles = {s.n: s for s in model.stories}
        width = max(40, (panel.size.width or 150) - 6)
        body = ["[b $bright]Ready to launch[/]"]
        for i, plan in enumerate(ready):
            story = titles.get(plan.backlog_id)
            mark = "❯" if i == self.cursor else " "
            box = "x" if self.ticks.get(plan.backlog_id) else " "
            left = f"{mark} [{box}] #{plan.backlog_id} {story.title if story else ''}"
            line = texts._pad(texts.e(left), f"up to ${plan.budget_usd:g}", width)
            files = ", ".join(r.name for r in plan.resources if r.kind == "file")
            if i == self.cursor:
                body += [f"[on $selected-bg]{line}[/]", f"[on $selected-bg]        {texts.e(files)}[/]"]
            else:
                body += [line, f"        [$dim]{texts.e(files)}[/]"]
        body.append(f"        [$warning]{texts.e(self.message)}[/]" if self.message else "")
        body += ["", "[b $bright]Not in this batch[/]"]
        for s in model.stories:
            if s.state in _NOT_IN:
                reason = _NOT_IN[s.state] + (f" · {s.meta}" if s.state == "notready" and s.meta else "")
                body.append(f"    {texts.STORY_ICON[s.state]} {texts.e(f'#{s.n} {s.title}'):<30}"
                            f"[$dim]{texts.e(reason)}[/]")
        k = sum(self.ticks.values())
        each = max((p.budget_usd for p in ready), default=model.budget_usd)
        body += ["", f"[b $bright]Budget[/]  {k} {'story' if k == 1 else 'stories'} × ${each:g} = "
                     f"[b]up to ${k * each:g}[/]",
                 f"[$dim]An estimate. Each story stops at its ${each:g} budget.[/]"]
        self.query_one("#bt-body", Static).update(Content.from_markup("\n".join(body)))
        launch = self.query_one("#bt-launch", Button)
        launch.label = f"Launch {k} {'story' if k == 1 else 'stories'}" if k else "Launch"
        launch.disabled = not k

    def keys(self) -> list[tuple[str, str]]:
        return [("↑↓", "move"), ("space", "tick"), ("enter", "launch"), ("esc", "cancel")]

    def focus_first(self) -> None:
        self.focus()

    def back(self) -> bool:
        return False  # esc: the board returns to Overview

    def action_move(self, delta: int) -> None:
        if self.model:
            self.cursor = max(0, min(len(self.model.ready_plans) - 1, self.cursor + delta))
            self.show(self.model)

    def action_tick(self) -> None:
        if not self.model or not self.model.ready_plans:
            return
        ready = self.model.ready_plans
        plan = ready[self.cursor]
        self.message = ""
        if self.ticks.get(plan.backlog_id):
            self.ticks[plan.backlog_id] = False
        else:
            on = [p for p in ready if self.ticks.get(p.backlog_id)]
            clash = next((p for p in on if conflicts(plan, p)), None)
            if clash is not None:
                shared = texts.e(next(iter(conflicts(plan, clash))).split(": ", 1)[-1].split(" / ")[0])
                self.message = (f"! Can’t run with #{clash.backlog_id}: both change {shared}. "
                                f"Untick #{clash.backlog_id} to swap.")
            elif len(on) >= self.model.limit:
                self.message = f"! {self.model.limit} run at a time. Untick one first."
            else:
                self.ticks[plan.backlog_id] = True
        self.show(self.model)

    def action_launch(self) -> None:
        chosen = [n for n, on in self.ticks.items() if on]
        if not chosen:
            self.message = "! Tick at least one story."
            self.show(self.model)
            return
        self.screen.launch(chosen)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "bt-launch":
            self.action_launch()
        elif event.button.id == "bt-cancel":
            self.screen.action_view("overview")
