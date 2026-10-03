"""Answer a question the way Claude Code's AskUserQuestion does: pick one, or write your own.

The agent lists its recommended option first. "You decide" hands the choice back
(recorded as an assumption on the recommended option); "Other…" is the only way
to a text field. The value posted is exactly what `domain.interview.resolve_answer`
understands: an option number, "you decide", or the operator's own words.
"""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from factory.domain.interview import is_delegation

DECIDE, OTHER = "decide", "other"


class AnswerPicker(Vertical):
    """↑↓ or 1-9 to choose, Enter to answer; Other… opens a one-line field."""

    DEFAULT_CSS = """
    AnswerPicker { height: auto; }
    AnswerPicker OptionList { height: auto; max-height: 16; border: none; padding: 0; }
    AnswerPicker Input { margin: 0 0 0 4; }
    """

    class Answered(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class DraftChanged(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ids: list[str] = []
        self._draft = ""

    def compose(self) -> ComposeResult:
        yield OptionList()
        yield Input(placeholder="Your own answer, then Enter (Esc: back to the choices)")

    def on_mount(self) -> None:
        self.query_one(Input).display = False

    def load(self, question: dict, draft: str = "") -> None:
        """Show `question` (an InterviewQuestion dump), restoring a saved draft."""
        options = question.get("options") or []
        choices = self.query_one(OptionList)
        choices.clear_options()
        self._ids = []
        for number, option in enumerate(options, 1):
            prompt = Text(f"{number}. {option['label']}", style="bold")
            if number == 1 and len(options) > 1:
                prompt.append("  recommended", style="green")
            if option.get("description"):
                prompt.append(f"\n   {option['description']}", style="dim")
            choices.add_option(Option(prompt))
            self._ids.append(str(number))
        if not any(is_delegation(o["label"]) for o in options):
            choices.add_option(Option(Text.assemble(
                (f"{len(self._ids) + 1}. You decide", "bold"),
                ("\n   The factory picks" + (" the recommended option" if options else "")
                 + "; recorded as an assumption, not your choice", "dim"))))
            self._ids.append(DECIDE)
        choices.add_option(Option(Text.assemble(
            (f"{len(self._ids) + 1}. Other…", "bold"), ("\n   Write your own answer", "dim"))))
        self._ids.append(OTHER)
        field = self.query_one(Input)
        self._draft = draft
        field.value = ""
        if draft in self._ids:
            choices.highlighted = self._ids.index(draft)
        elif draft.strip().lower() == "you decide" and DECIDE in self._ids:
            choices.highlighted = self._ids.index(DECIDE)
        elif draft:
            choices.highlighted = self._ids.index(OTHER)
            field.value = draft
        else:
            # Nothing typed yet: a question without options goes straight to Other.
            choices.highlighted = 0 if options else self._ids.index(OTHER)
        field.display = self._ids[choices.highlighted] == OTHER

    @property
    def value(self) -> str:
        """The answer as `resolve_answer` reads it; "" when Other is still empty."""
        choices = self.query_one(OptionList)
        if choices.highlighted is None:
            return ""
        picked = self._ids[choices.highlighted]
        if picked == OTHER:
            return self.query_one(Input).value.strip()
        return "you decide" if picked == DECIDE else picked

    def focus_choices(self) -> None:
        self.query_one(OptionList).focus()

    def _draft_changed(self) -> None:
        value = self.value if self._ids[self.query_one(OptionList).highlighted or 0] != OTHER \
            else self.query_one(Input).value
        if value != self._draft:
            self._draft = value
            self.post_message(self.DraftChanged(value))

    def on_key(self, event) -> None:
        choices = self.query_one(OptionList)
        if event.key == "escape" and self.query_one(Input).has_focus:
            event.stop()
            choices.focus()
        elif event.character and event.character.isdigit() and choices.has_focus:
            index = int(event.character) - 1
            if 0 <= index < len(self._ids):
                event.stop()
                choices.highlighted = index

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        if not self._ids:
            return
        self.query_one(Input).display = self._ids[event.option_index] == OTHER
        self._draft_changed()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if self._ids[event.option_index] == OTHER:
            field = self.query_one(Input)
            field.display = True
            field.focus()
        else:
            self.post_message(self.Answered(self.value))

    @on(Input.Changed)
    def _typed(self, event: Input.Changed) -> None:
        event.stop()
        self._draft_changed()

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value.strip():
            self.post_message(self.Answered(event.value.strip()))
