"""The board's overlays: the project menu, amend brief in two steps, stop, help."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static, TextArea
from textual.widgets.option_list import Option

_CSS = """
ProjectMenu, AmendModal, StopModal, HelpModal { align: center middle; background: $background 60%; }
.modal { width: 70; height: auto; max-height: 80%; background: $background; }
.modal OptionList { height: auto; max-height: 20; }
.modal .row { height: 1; margin-top: 1; }
.modal .row Static { width: auto; }
.modal TextArea { height: 4; }
"""


class ProjectMenu(ModalScreen[str | None]):
    """`p`: switch project and the project-level actions; a disabled one says why."""

    DEFAULT_CSS = _CSS
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, project: str, items: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self.project, self.items = project, items  # (label, action, disabled-reason)

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel modal") as box:
            box.border_title = f"Project · {self.project}"
            yield OptionList(*[Option(Content.from_markup(
                f"[$dim]{label} · {why}[/]") if why else label) for label, _a, why in self.items])
            yield Static(Content.from_markup("[$dim]↑↓ choose · enter · esc close[/]"))

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        label, action, why = self.items[event.option_index]
        self.dismiss(f"refused:{label}: {why}." if why else action)


class AmendModal(ModalScreen[str | None]):
    """Two steps: what changed (nothing paid runs), then what drafting it costs."""

    DEFAULT_CSS = _CSS
    BINDINGS = [Binding("escape", "back", show=False),
                Binding("ctrl+enter,ctrl+j", "continue", show=False, priority=True)]

    def __init__(self, may_affect: str = "") -> None:
        super().__init__()
        self.step, self.may_affect = 1, may_affect

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel modal") as box:
            box.border_title = "Amend brief"
            yield Static("What changed? Nothing paid runs until you confirm.", id="am-text")
            yield TextArea(id="am-input")
            with Horizontal(classes="row"):
                yield Button("Continue", id="am-continue", variant="primary", compact=True)
                yield Static(Content.from_markup(" [$dim]ctrl+enter[/]   "), id="am-key")
                yield Button("Cancel", id="am-cancel", compact=True)
                yield Static(Content.from_markup(" [$dim]esc[/]"))

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_continue(self) -> None:
        if self.step == 1:
            if not self.query_one(TextArea).text.strip():
                return
            self.step = 2
            self.query_one(TextArea).display = False
            affect = f"\n\n[$dim]May affect[/]  {self.may_affect}" if self.may_affect else ""
            self.query_one("#am-text", Static).update(Content.from_markup(
                "This drafts a revised brief (one model call). You approve it before any story "
                "is re-planned." + affect))
            self.query_one("#am-continue", Button).label = "Draft revised brief"
            self.query_one("#am-key", Static).update(Content.from_markup(" [$dim]enter[/]   "))
            self.query_one("#am-continue").focus()
        else:
            self.dismiss(self.query_one(TextArea).text.strip())

    def action_back(self) -> None:
        if self.step == 2:
            self.step = 1
            self.query_one(TextArea).display = True
            self.query_one("#am-text", Static).update("What changed? Nothing paid runs until you confirm.")
            self.query_one("#am-continue", Button).label = "Continue"
            self.query_one("#am-key", Static).update(Content.from_markup(" [$dim]ctrl+enter[/]   "))
            self.query_one(TextArea).focus()
        else:
            self.dismiss(None)

    @on(Button.Pressed, "#am-continue")
    def pressed_continue(self) -> None:
        self.action_continue()

    @on(Button.Pressed, "#am-cancel")
    def pressed_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        if self.step == 2 and event.key == "enter":
            event.stop()
            self.action_continue()


class StopModal(ModalScreen[bool]):
    DEFAULT_CSS = _CSS
    BINDINGS = [Binding("escape", "dismiss(False)", show=False),
                Binding("enter", "dismiss(True)", show=False)]

    def __init__(self, title: str, spend: str) -> None:
        super().__init__()
        self.title_text, self.spend = title, spend

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel modal") as box:
            box.border_title = f"Stop {self.title_text} at a safe point?"
            yield Static("It stops after the current step and keeps everything done so far."
                         + (f"\nSpend so far {self.spend}." if self.spend else ""))
            with Horizontal(classes="row"):
                yield Button("Stop after this step", id="stop-yes", variant="primary", compact=True)
                yield Static(Content.from_markup(" [$dim]enter[/]   "))
                yield Button("Keep running", id="stop-no", compact=True)
                yield Static(Content.from_markup(" [$dim]esc[/]"))

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "stop-yes")


HELP = """[b $accent]o[/] Overview  [b $accent]n[/] Needs you  [b $accent]t[/] Stories  [b $accent]l[/] Activity
[b $accent]p[/] Project menu  [b $accent]P[/] Pause/resume new starts  [b $accent]esc[/] back
[b $accent]b[/] Propose batch  [b $accent]S[/] Stop at a safe point  [b $accent]i[/] Start the interview
In a detail: [b $accent]a[/] approve  [b $accent]c[/] request changes  [b $accent]r[/] retry/reject
Questions: [b $accent]↑↓[/] or [b $accent]1-9[/], [b $accent]enter[/] answers, [b $accent]s[/] skips
The operator's other verbs (doctor, evals, metrics…): [b $accent]ctrl+p[/]

[$dim]esc closes[/]"""


class HelpModal(ModalScreen[None]):
    DEFAULT_CSS = _CSS
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel modal") as box:
            box.border_title = "Keys"
            yield Static(Content.from_markup(HELP))
