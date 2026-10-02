"""Modals for the intake interview on the board.

`factory.runs.run_interview` drives the interview from a worker thread; these
screens are the board's `ask` / `approve` / `confirm_stack` answers. Each one
dismisses with exactly what the terminal prompt would have returned, so the
service cannot tell the two surfaces apart.
"""

from __future__ import annotations

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from factory.domain.interview import TOPIC_TITLES, InterviewQuestion

_CSS = """
QuestionScreen, ReviewScreen { align: center middle; }
#box { width: 90%; height: auto; max-height: 90%; border: round $accent;
       padding: 1 2; background: $surface; }
#body { height: auto; max-height: 30; }
.buttons { height: auto; }
"""


class QuestionScreen(ModalScreen[str | None]):
    """One question; dismisses with the raw answer, an option number, "you decide",
    or None for "done" — the inputs `domain.interview.resolve_answer` understands."""

    DEFAULT_CSS = _CSS

    def __init__(self, question: InterviewQuestion, missing: list[str]) -> None:
        super().__init__()
        self.question = question
        title = TOPIC_TITLES.get(question.topic, question.topic)
        self.text = f"{title}\n\n{question.question}"
        if missing:
            self.text += "\n\nStill required: " + ", ".join(TOPIC_TITLES[t] for t in missing)

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.text, markup=False)
            # The agent lists its recommended option first.
            for i, o in enumerate(self.question.options, 1):
                label = f"{i}. {o.label}" + (f" — {o.description}" if o.description else "")
                yield Button(escape(label), id=f"opt-{i}")
            yield Input(placeholder="Your answer, then Enter", id="answer")
            with Horizontal(classes="buttons"):
                yield Button("You decide", id="decide")
                yield Button("Done", id="done", variant="warning")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def _typed(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("opt-"):
            self.dismiss(bid.removeprefix("opt-"))
        elif bid == "decide":
            self.dismiss("you decide")
        elif bid == "done":
            self.dismiss(None)


class ReviewScreen(ModalScreen[bool | str | None]):
    """A document to read. With `review`: Approve (True), Stop (False) or a typed
    correction (str), as the brief and stack prompts expect; without it, Close."""

    DEFAULT_CSS = _CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, title: str, body: str, *, review: bool) -> None:
        super().__init__()
        self.text = f"{title}\n\n{body}"
        self.review = review

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            with VerticalScroll(id="body"):
                yield Static(self.text, markup=False)
            if self.review:
                yield Input(placeholder="Or type what to change, then Enter", id="correction")
            with Horizontal(classes="buttons"):
                if self.review:
                    yield Button("Approve", id="approve", variant="success")
                    yield Button("Stop for now", id="stop", variant="warning")
                else:
                    yield Button("Close", id="close")

    @on(Input.Submitted)
    def _typed(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"approve": True, "stop": False}.get(event.button.id or ""))

    def action_close(self) -> None:
        # Escape on a review stops (nothing approved); on a plain view it just closes.
        self.dismiss(False if self.review else None)
