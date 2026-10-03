"""Stories: grouped by state (NEEDS ANSWERS … DONE) with a detail per story, and the board
view (`v`): seven columns of cards from TO REFINE to DONE."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from factory.interfaces.board import texts
from factory.interfaces.board.views_base import BoardView
from factory.runs.board import Board, Story


class _BoardCanvas(Static, can_focus=True):
    """The board view: one focusable drawing, moved with the arrow keys."""


class StoriesView(BoardView):
    BINDINGS = [
        Binding("v", "toggle_board", show=False),
        Binding("r", "story('r')", show=False), Binding("m", "story('m')", show=False),
        Binding("S", "story('S')", show=False),
        Binding("left", "board_move(-1, 0)", show=False), Binding("right", "board_move(1, 0)", show=False),
        Binding("up", "board_move(0, -1)", show=False), Binding("down", "board_move(0, 1)", show=False),
    ]
    DEFAULT_CSS = """
    StoriesView { layout: horizontal; }
    StoriesView #st-list-panel { width: 64; height: 1fr; }
    StoriesView #st-detail { width: 1fr; height: 1fr; }
    StoriesView #st-list { height: 1fr; }
    StoriesView #st-top { height: auto; margin-bottom: 1; }
    StoriesView #st-actions { height: 2; }
    StoriesView #st-actions Static { width: auto; }
    StoriesView #st-board { width: 1fr; height: 1fr; display: none; padding: 0 1; }
    StoriesView.board #st-board { display: block; }
    StoriesView.board #st-list-panel, StoriesView.board #st-detail { display: none; }
    StoriesView.narrow #st-list-panel { width: 1fr; }
    StoriesView.narrow #st-detail { display: none; }
    StoriesView.narrow.open #st-detail { display: block; }
    StoriesView.narrow.open #st-list-panel { display: none; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.ids: list[int | None] = []
        self.selected: int | None = None
        self.open = False
        self.board_mode = False
        self.col = self.row = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="st-list-panel", classes="panel"):
            yield OptionList(id="st-list")
        with Vertical(id="st-detail", classes="panel"):
            yield Static("", id="st-top", classes="top")
            with VerticalScroll(classes="body", id="st-scroll"):
                yield Static("", id="st-body")
            yield Horizontal(id="st-actions", classes="actions")
        yield _BoardCanvas("", id="st-board")

    # ── model → screen ─────────────────────────────────────────────
    def current(self) -> Story | None:
        stories = self.model.stories if self.model else []
        return next((s for s in stories if s.n == self.selected), None)

    def show(self, model: Board) -> None:
        super().show(model)
        narrow = self.screen.has_class("narrow") if self.is_mounted else False
        self.set_class(narrow, "narrow")
        self.set_class(self.open, "open")
        self.set_class(self.board_mode, "board")
        self._fill_list()
        self._fill_detail()
        if self.board_mode:
            self._fill_board()

    def _fill_list(self) -> None:
        listing = self.query_one("#st-list", OptionList)
        stories = texts.ordered_stories(self.model.stories)
        panel = self.query_one("#st-list-panel")
        panel.border_title = f"Stories · {len(stories)}"
        panel.border_subtitle = "v board view"
        width = max(30, (listing.size.width or 60) - 2)
        options: list[Option] = []
        ids: list[int | None] = []
        if not stories:
            options = [Option(Content.from_markup("[$dim]No stories yet.[/] Approve a backlog to add some."),
                              disabled=True)]
            ids = [None]
        last = ""
        for s in stories:
            group = texts.story_group(s.state)
            if group != last:
                if last:
                    options.append(Option("", disabled=True))
                    ids.append(None)
                n = sum(texts.story_group(x.state) == group for x in stories)
                options.append(Option(Content.from_markup(f"[$dim]{texts.STORY_GROUP[group]} · {n}[/]"),
                                      disabled=True))
                ids.append(None)
                last = group
            options.append(Option(Content.from_markup(texts.story_row(s, width)), id=str(s.n)))
            ids.append(s.n)
        if ids == self.ids and listing.option_count == len(options):
            for i, option in enumerate(options):
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
        s = self.current()
        panel = self.query_one("#st-detail")
        if s is None:
            panel.border_title = ""
            self.query_one("#st-top", Static).update("")
            self.query_one("#st-body", Static).update(Content.from_markup("[$dim]Select a story.[/]"))
            self._set_buttons([])
            return
        detail = texts.story_detail(s, self.model, width=max(40, (panel.size.width or 90) - 4))
        panel.border_title = ("‹ Stories · " if self.has_class("narrow") else "") + detail.title
        panel.border_subtitle = detail.right
        self.query_one("#st-top", Static).update(Content.from_markup(detail.top))
        self.query_one("#st-body", Static).update(Content.from_markup(detail.body))
        self._set_buttons(detail.buttons)

    def _set_buttons(self, buttons: list[tuple[str, str, bool]]) -> None:
        bar = self.query_one("#st-actions", Horizontal)
        wanted = [f"{l}|{k}" for l, k, _p in buttons]
        if getattr(bar, "_wanted", None) == wanted:
            return
        bar._wanted = wanted  # type: ignore[attr-defined]
        bar.remove_children()
        for label, key, primary in buttons:
            bar.mount(Button(label, variant="primary" if primary else "default", compact=True,
                             name=key, classes="act"))
            bar.mount(Static(Content.from_markup(f" [$dim]{key}[/]   ")))

    def _fill_board(self) -> None:
        board = self.query_one("#st-board", _BoardCanvas)
        width = (self.size.width or 160) - 2
        board.update(Content.from_markup(
            "[b $ink on $accent] Stories · board [/]  [$dim]columns are pipeline stages[/]\n\n"
            + texts.board_text(self.model.stories, self.col, self.row, width, (self.size.height or 40))))

    def keys(self) -> list[tuple[str, str]]:
        if self.board_mode:
            return [("←→", "column"), ("↑↓", "card"), ("enter", "open"), ("v", "list view"), ("esc", "back")]
        s = self.current()
        if self.open and s is not None:
            return texts.story_detail(s, self.model).keys
        return [("↑↓", "move"), ("enter", "open"), ("v", "board view"), ("b", "batch"), ("esc", "back")]

    # ── navigation ─────────────────────────────────────────────────
    def focus_first(self) -> None:
        if self.board_mode:
            self.query_one("#st-board").focus()
        elif self.open:
            self.query_one("#st-scroll").focus()
        else:
            self.query_one("#st-list", OptionList).focus()

    @on(OptionList.OptionHighlighted, "#st-list")
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id and int(event.option.id) != self.selected:
            self.selected = int(event.option.id)
            self._fill_detail()

    @on(OptionList.OptionSelected, "#st-list")
    def chosen(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id:
            self.selected = int(event.option.id)
            self.open_detail()

    def open_detail(self) -> None:
        self.open = True
        self.board_mode = False
        self.set_class(False, "board")
        self.set_class(True, "open")
        self._fill_list()
        self._fill_detail()
        self.query_one("#st-scroll").focus()
        self.screen._show_keys()

    def back(self) -> bool:
        if self.open:
            self.open = False
            self.set_class(False, "open")
            self.focus_first()
            return True
        if self.board_mode:
            self.action_toggle_board()
            return True
        return False

    def action_toggle_board(self) -> None:
        if self.open:
            return
        self.board_mode = not self.board_mode
        self.set_class(self.board_mode, "board")
        if self.board_mode:
            self._fill_board()
        self.focus_first()
        self.screen._show_keys()

    def action_board_move(self, dx: int, dy: int) -> None:
        if not self.board_mode:
            if dy and not self.open:
                listing = self.query_one("#st-list", OptionList)
                listing.action_cursor_down() if dy > 0 else listing.action_cursor_up()
            return
        columns = texts.board_columns(self.model.stories)
        self.col = max(0, min(6, self.col + dx))
        self.row = 0 if dx else max(0, min(len(columns[self.col][1]) - 1, self.row + dy))
        self._fill_board()

    def on_key(self, event) -> None:
        if self.board_mode and event.key == "enter":
            event.stop()
            items = texts.board_columns(self.model.stories)[self.col][1]
            if items:
                self.selected = items[min(self.row, len(items) - 1)].n
                self.open_detail()
        elif self.open and event.key == "enter":
            event.stop()
            self.action_story("enter")

    # ── actions ────────────────────────────────────────────────────
    def action_story(self, key: str) -> None:
        s = self.current()
        if s is None or not self.open:
            return
        self.screen.story_action(s, key)

    @on(Button.Pressed, ".act")
    def pressed(self, event: Button.Pressed) -> None:
        s = self.current()
        if s is not None:
            self.screen.story_action(s, event.button.name or "")
