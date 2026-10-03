"""The factory board (design handoff 1a "Summary + inbox"): one screen, four tabs.

Chrome (header, lifecycle, tabs, feedback line, keys) around a ContentSwitcher of views.
The read model (`runs.board.board`) loads in a worker thread and arrives as a message,
so the UI thread never touches the database; actions go through `runs` the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import ContentSwitcher

from factory.interfaces.board.chrome import FeedbackLine, HeaderBar, KeyBar, LifecycleBar, TabBar
from factory.interfaces.board.theme import BOARD_CSS, FACTORY_THEME
from factory.interfaces.board.views_base import BoardView
from factory.runs.board import Board, board

WIDE = 120  # the design's two-pane threshold, in columns
REFRESH_SECONDS = 2.0


class _Placeholder(BoardView):
    def __init__(self, keys: list[tuple[str, str]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._keys = keys

    def keys(self) -> list[tuple[str, str]]:
        return self._keys


def _views() -> dict[str, BoardView]:
    """Every view by name; a module per tab, imported here so tests can swap one."""
    from factory.interfaces.board import tabs
    return tabs.build()


class BoardScreen(Screen):
    BINDINGS = [
        Binding("o", "view('overview')", "overview", show=False),
        Binding("n", "view('needs')", "needs you", show=False),
        Binding("t", "view('stories')", "stories", show=False),
        Binding("l", "view('activity')", "activity", show=False),
        Binding("escape", "back", "back", show=False),
    ]

    class Loaded(Message):
        def __init__(self, model: Board) -> None:
            super().__init__()
            self.model = model

    def __init__(self, db_path: Path, project: str | None = None) -> None:
        super().__init__()
        self.db_path, self.project = db_path, project
        self.model: Board | None = None
        self.view = "overview"
        self.views = _views()

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        yield LifecycleBar(id="lifecycle")
        yield TabBar(id="tabs")
        with ContentSwitcher(id="views", initial="overview"):
            for name, view in self.views.items():
                view.id = name
                yield view
        yield FeedbackLine(id="feedback")
        yield KeyBar(id="keys")

    def on_mount(self) -> None:
        self.load()
        self.set_interval(REFRESH_SECONDS, self.load)
        self.set_interval(0.5, self.query_one(FeedbackLine).tick)

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < WIDE, "narrow")

    @property
    def wide(self) -> bool:
        return self.size.width >= WIDE

    # ── model ────────────────────────────────────────────────────────
    @work(thread=True, exclusive=True, group="board-model")
    def load(self) -> None:
        try:
            model = board(self.project, db_path=self.db_path)
        except Exception as exc:  # a locked or broken DB is shown, never a crash
            self.app.call_from_thread(self.say, f"Couldn't read the board: {exc}", "err")
            return
        self.post_message(self.Loaded(model))

    @on(Loaded)
    def loaded(self, message: Loaded) -> None:
        self.model = model = message.model
        if model.project:
            self.project = model.project["slug"]
        self.query_one(HeaderBar).show(self.project or "no project", model.worker, model.queued)
        self.query_one(LifecycleBar).show(model.lifecycle)
        self._show_tabs()
        for view in self.views.values():
            view.show(model)
        self._show_keys()

    def _show_tabs(self) -> None:
        model = self.model
        badges = {"needs": model.need_count, "stories": len(model.stories)} if model else {}
        active = self.view if self.view in dict(TabBar.TABS) else ""
        self.query_one(TabBar).show(active, badges)

    def _show_keys(self) -> None:
        self.query_one(KeyBar).show(self.views[self.view].keys())

    def say(self, text: str, kind: str = "ok") -> None:
        self.query_one(FeedbackLine).say(text, kind)

    # ── navigation ───────────────────────────────────────────────────
    def action_view(self, name: str) -> None:
        self.view = name
        self.query_one(ContentSwitcher).current = name
        self._show_tabs()
        self._show_keys()
        self.views[name].focus_first() if hasattr(self.views[name], "focus_first") else None

    def action_back(self) -> None:
        if self.views[self.view].back():
            self._show_keys()
            return
        self.action_view("allruns" if self.view == "overview" else "overview")


class BoardApp(App):
    CSS = BOARD_CSS
    TITLE = "factory"

    def __init__(self, db_path: Path, project: str | None = None) -> None:
        super().__init__()
        self.db_path = Path(db_path)
        self.project = project or _last_project(self.db_path)
        # Before the CSS is parsed: it uses the theme's own variables ($header-bg, $dim…).
        self.register_theme(FACTORY_THEME)
        self.theme = "factory"

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return dict(FACTORY_THEME.variables)

    def on_mount(self) -> None:
        self.push_screen(BoardScreen(self.db_path, self.project))


def _state_file(db_path: Path) -> Path:
    # A per-viewer convenience next to the factory's DB, not part of the record.
    return db_path.parent / "board-state.json"


def _last_project(db_path: Path) -> str | None:
    try:
        return json.loads(_state_file(db_path).read_text()).get("project")
    except (OSError, ValueError):
        return None
