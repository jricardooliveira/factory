"""Needs you: every decision, grouped FAILED → APPROVE → ANSWER, and its detail.

Two panes at ≥ 120 columns (Enter focuses the detail, Esc returns); below that the list
and the detail are separate stacked views. A story's questions are ONE row, answered in
sequence in place. Text-box mode (Request changes / Reject / Note) replaces the action
bar; Esc keeps the draft.
"""

from __future__ import annotations

from datetime import datetime, timezone

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Button, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from factory.domain.board import inbox_group
from factory.interfaces.board import texts
from factory.interfaces.board.answer import AnswerPicker
from factory.interfaces.board.views_base import BoardView
from factory.runs.board import Board, Decision

LIST_KEYS = [("↑↓", "move"), ("enter", "open"), ("o", "overview"), ("esc", "back")]


class NeedsView(BoardView):
    BINDINGS = [
        Binding("a", "act('a')", show=False), Binding("c", "act('c')", show=False),
        Binding("r", "act('r')", show=False), Binding("m", "act('m')", show=False),
        Binding("x", "act('x')", show=False), Binding("d", "act('d')", show=False),
        Binding("s", "act('s')", show=False),
        Binding("ctrl+enter,ctrl+j", "send", show=False, priority=True),
    ]
    DEFAULT_CSS = """
    NeedsView { layout: horizontal; }
    NeedsView #nd-list-panel { width: 64; height: 1fr; }
    NeedsView #nd-detail { width: 1fr; height: 1fr; }
    NeedsView.narrow #nd-list-panel { width: 1fr; }
    NeedsView.narrow.open #nd-list-panel { display: none; }
    NeedsView.narrow #nd-detail { display: none; }
    NeedsView.narrow.open #nd-detail { display: block; }
    NeedsView #nd-list { height: 1fr; }
    NeedsView #nd-top { height: auto; margin-bottom: 1; }
    NeedsView #nd-picker { height: auto; margin-bottom: 1; }
    NeedsView #nd-textbox { height: auto; display: none; border-top: solid $dim; }
    NeedsView #nd-textbox TextArea { height: 4; }
    NeedsView #nd-textbox .row { height: 1; }
    NeedsView #nd-textbox Button { margin-right: 2; }
    NeedsView #nd-textbox .label { color: $accent; }
    NeedsView .typing #nd-actions { display: none; }
    NeedsView .typing #nd-textbox { display: block; }
    NeedsView #nd-actions { height: 2; }
    NeedsView #nd-actions Static, NeedsView #nd-textbox .row Static { width: auto; }
    NeedsView #nd-actions .hint, NeedsView #nd-textbox .row .hint { width: 1fr; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.ids: list[str | None] = []  # decision id per list option (None: header/blank)
        self.selected: str | None = None
        self.open = False  # the detail is shown (narrow) / focused (wide)
        self.typing: str | None = None  # text-box mode: changes | reject | note
        self.show_error = False
        self._drafts: dict[str, str] = {}
        self._loaded_question: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="nd-list-panel", classes="panel"):
            yield OptionList(id="nd-list")
        with Vertical(id="nd-detail", classes="panel"):
            yield Static("", id="nd-top", classes="top")
            yield AnswerPicker(id="nd-picker")
            with VerticalScroll(classes="body", id="nd-scroll"):
                yield Static("[$dim]Select an item.[/]", id="nd-body")
            with Horizontal(id="nd-actions", classes="actions"):
                yield Static("", classes="hint", id="nd-hint")
            with Vertical(id="nd-textbox"):
                yield Static("What should change?", classes="label", id="nd-label")
                yield TextArea(id="nd-text")
                with Horizontal(classes="row"):
                    yield Button("Send", id="nd-send", variant="primary", compact=True)
                    yield Static("[$dim]ctrl+enter[/]  ", markup=True)
                    yield Button("Cancel", id="nd-cancel", compact=True)
                    yield Static("[$dim]esc[/]   ", markup=True)
                    yield Static("", classes="hint", id="nd-texthint")

    # ── model → screen ─────────────────────────────────────────────
    @property
    def decisions(self) -> list[Decision]:
        return self.model.decisions if self.model else []

    def current(self) -> Decision | None:
        return next((d for d in self.decisions if d.id == self.selected), None)

    def show(self, model: Board) -> None:
        super().show(model)
        narrow = self.screen.has_class("narrow") if self.is_mounted else False
        self.set_class(narrow, "narrow")
        if not narrow and self.screen.size.width:
            # The design's list width: min(64, 38% of the terminal).
            self.query_one("#nd-list-panel").styles.width = min(64, int(self.screen.size.width * 0.38))
        self.set_class(self.open, "open")
        self._fill_list()
        self._fill_detail()

    def _fill_list(self) -> None:
        listing = self.query_one("#nd-list", OptionList)
        panel = self.query_one("#nd-list-panel")
        panel.border_title = f"Needs you · {self.model.need_count}"
        width = max(30, (listing.size.width or 60) - 2)
        now = datetime.now(timezone.utc)
        options: list[Option] = []
        ids: list[str | None] = []
        if not self.decisions:
            options = [Option(Content.from_markup("[$success]✓[/] Nothing needs you."), disabled=True),
                       Option(Content.from_markup("[$dim]Work continues in the background. "
                                                  "Questions and approvals will appear here.[/]"),
                              disabled=True)]
            ids = [None, None]
        last = -1
        for d in self.decisions:
            group = inbox_group(d.kind)
            if group != last:
                if last != -1:
                    options.append(Option("", disabled=True))
                    ids.append(None)
                options.append(Option(Content.from_markup(texts.group_header(d, self.decisions)),
                                      disabled=True))
                ids.append(None)
                last = group
            prompt = Content.from_markup(texts.row_title(d, width, now) + f"\n  [$dim]{texts.e(d.sub)}[/]")
            options.append(Option(prompt, id=d.id))
            ids.append(d.id)
        if self.decisions:
            options += [Option("", disabled=True), Option(Content.from_markup(
                "[$dim]Oldest first within each group. Questions from one story share a row.[/]"),
                disabled=True)]
            ids += [None, None]
        if ids == self.ids and listing.option_count == len(options):
            for i, option in enumerate(options):  # same rows: refresh text, keep the cursor
                listing.replace_option_prompt_at_index(i, option.prompt)
        else:
            listing.clear_options()
            listing.add_options(options)
            self.ids = ids
        keep = self.selected if self.selected in ids else next((i for i in ids if i), None)
        self.selected = keep
        if keep is not None:
            listing.highlighted = ids.index(keep)

    def _fill_detail(self) -> None:
        d = self.current()
        top = self.query_one("#nd-top", Static)
        body = self.query_one("#nd-body", Static)
        picker = self.query_one("#nd-picker", AnswerPicker)
        panel = self.query_one("#nd-detail")
        if d is None:
            panel.border_title, panel.border_subtitle = "", ""
            top.update("")
            body.update(Content.from_markup("[$dim]Select an item.[/]"))
            picker.display = False
            self._set_buttons([], "")
            return
        detail = texts.detail(d, self.model, now=datetime.now(timezone.utc),
                              width=max(40, (panel.size.width or 90) - 4), show_error=self.show_error)
        crumb = "" if not self.has_class("narrow") else "‹ Needs you · "
        panel.border_title = crumb + detail.title
        panel.border_subtitle = detail.right
        top.update(Content.from_markup(detail.top))
        body.update(Content.from_markup(detail.body))
        if d.kind == "questions":
            question = d.data["pending"][0]
            picker.display = True
            if self._loaded_question != question["id"]:
                self._loaded_question = question["id"]
                picker.load(question["context"]["question"],
                            self._drafts.get(question["id"], question.get("draft_text") or ""))
        else:
            picker.display = False
            self._loaded_question = None
        self._set_buttons(detail.buttons, detail.hint)
        self.query_one("#nd-label", Static).update(detail.text_label)
        self.query_one("#nd-texthint", Static).update(Content.from_markup(f"[$dim]{detail.text_hint}[/]"))

    def _set_buttons(self, buttons: list[tuple[str, str, bool]], hint: str) -> None:
        bar = self.query_one("#nd-actions", Horizontal)
        wanted = [f"{label}|{key}|{primary}" for label, key, primary in buttons]
        if getattr(bar, "_wanted", None) == wanted:
            self.query_one("#nd-hint", Static).update(Content.from_markup(f"[$dim]{hint}[/]"))
            return
        bar._wanted = wanted  # type: ignore[attr-defined]
        for child in list(bar.children):
            if child.id != "nd-hint":
                child.remove()
        for label, key, primary in buttons:
            button = Button(label, variant="primary" if primary else "default", compact=True,
                            name=key, classes="act")
            bar.mount(button, before=self.query_one("#nd-hint"))
            bar.mount(Static(Content.from_markup(f"[$dim]{key}[/]   ")), before=self.query_one("#nd-hint"))
        self.query_one("#nd-hint", Static).update(Content.from_markup(f"[$dim]{hint}[/]"))

    def keys(self) -> list[tuple[str, str]]:
        if self.typing:
            return [("type", ""), ("ctrl+enter", "send"), ("esc", "cancel")]
        d = self.current()
        if self.open and d is not None:
            return texts.detail(d, self.model, now=datetime.now(timezone.utc)).keys
        return [("↑↓", "move"), ("enter", "focus detail" if not self.has_class("narrow") else "open"),
                ("o", "overview"), ("esc", "back")]

    # ── navigation ─────────────────────────────────────────────────
    def focus_first(self) -> None:
        if self.open and self.current() is not None:
            self._focus_detail()
        else:
            self.query_one("#nd-list", OptionList).focus()

    @on(OptionList.OptionHighlighted, "#nd-list")
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id and event.option.id != self.selected:
            self.selected = event.option.id
            self.show_error = False
            self._fill_detail()

    @on(OptionList.OptionSelected, "#nd-list")
    def selected_row(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id:
            self.selected = event.option.id
            self.open_detail()

    def open_detail(self) -> None:
        self.open = True
        self.set_class(True, "open")
        self._fill_detail()
        self._focus_detail()
        self.screen._show_keys()

    def _focus_detail(self) -> None:
        d = self.current()
        if d is not None and d.kind == "questions":
            self.query_one("#nd-picker", AnswerPicker).focus_choices()
        else:
            self.query_one("#nd-scroll").focus()

    def back(self) -> bool:
        if self.typing:
            self._keep_text()
            self._end_typing()
            return True
        if self.open:
            self.open = False
            self.set_class(False, "open")
            self.query_one("#nd-list", OptionList).focus()
            return True
        return False

    # ── actions ────────────────────────────────────────────────────
    def action_act(self, key: str) -> None:
        d = self.current()
        if d is None or not self.open or self.typing:
            return
        self.screen.decide(d, key, self)

    @on(Button.Pressed, ".act")
    def pressed(self, event: Button.Pressed) -> None:
        d = self.current()
        if d is not None:
            if not self.open:
                self.open_detail()
            key = event.button.name or ""
            if key == "enter" and d.kind == "questions":
                value = self.query_one("#nd-picker", AnswerPicker).value
                if value:
                    self.screen.answer(d, value, self)
                return
            self.screen.decide(d, key, self)

    @on(AnswerPicker.Answered)
    def answered(self, event: AnswerPicker.Answered) -> None:
        d = self.current()
        if d is not None and d.kind == "questions":
            self.screen.answer(d, event.value, self)

    @on(AnswerPicker.DraftChanged)
    def draft(self, event: AnswerPicker.DraftChanged) -> None:
        if self._loaded_question:
            self._drafts[self._loaded_question] = event.value
            self.screen.keep_draft(self._loaded_question, event.value)

    def start_typing(self, kind: str) -> None:
        d = self.current()
        self.typing = kind
        self.query_one("#nd-detail").add_class("typing")
        area = self.query_one("#nd-text", TextArea)
        area.load_text(self._drafts.get(f"{kind}:{d.id}", "") if d else "")
        area.focus()
        self.screen._show_keys()

    def _keep_text(self) -> None:
        d = self.current()
        if d is not None and self.typing:
            self._drafts[f"{self.typing}:{d.id}"] = self.query_one("#nd-text", TextArea).text

    def _end_typing(self) -> None:
        self.typing = None
        self.query_one("#nd-detail").remove_class("typing")
        self._focus_detail()
        self.screen._show_keys()

    def action_send(self) -> None:
        d = self.current()
        if not self.typing or d is None:
            return
        text = self.query_one("#nd-text", TextArea).text.strip()
        if not text:
            self.screen.say("Write something first, or press esc.", "warn")
            return
        kind = self.typing
        self._drafts.pop(f"{kind}:{d.id}", None)
        self._end_typing()
        self.screen.send_text(d, kind, text, self)

    @on(Button.Pressed, "#nd-send")
    def send_pressed(self) -> None:
        self.action_send()

    @on(Button.Pressed, "#nd-cancel")
    def cancel_pressed(self) -> None:
        self.back()

    def after_answer(self, d: Decision, finished: bool) -> None:
        """The answered group: next question in place, or back to the list on the next row."""
        if finished:
            rows = [i for i in self.ids if i]
            after = rows[rows.index(d.id) + 1:] if d.id in rows else []
            self.selected = after[0] if after else None
            self.open = False
            self.set_class(False, "open")
            self.query_one("#nd-list", OptionList).focus()
        self._loaded_question = None

    def skip(self) -> None:
        rows = [i for i in self.ids if i]
        if self.selected in rows and rows.index(self.selected) + 1 < len(rows):
            self.selected = rows[rows.index(self.selected) + 1]
        self.open = False
        self.set_class(False, "open")
        self._fill_list()
        self._fill_detail()
        self.query_one("#nd-list", OptionList).focus()
