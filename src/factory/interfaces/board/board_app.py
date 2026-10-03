"""The factory board (design handoff 1a "Summary + inbox"): one screen, four tabs.

Chrome (header, lifecycle, tabs, feedback line, keys) around a ContentSwitcher of views.
The read model (`runs.board.board`) loads in a worker thread and arrives as a message,
so the UI thread never touches the database; actions go through `runs` the same way.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
from factory.runs.batches import queue_release, queue_resume
from factory.runs.board import Board, Decision, board
from factory.runs.dashboard import pause_project, recover_job
from factory.runs.refinement import (answer, dismiss_job, request_changes, retry_job,
                                      save_draft, start_backlog, start_refinement)
from factory.runs.worker import start_worker

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
        Binding("i", "start('interview')", show=False),
        Binding("P", "toggle_pause", show=False),
        Binding("b", "batch_key", show=False),
    ]

    class Loaded(Message):
        def __init__(self, model: Board) -> None:
            super().__init__()
            self.model = model

    def __init__(self, db_path: Path, project: str | None = None) -> None:
        super().__init__()
        self.db_path, self.project = db_path, project
        self.seen: dict[str, str] = _read_state(db_path).get("seen", {})
        self._welcomed = False
        self.model: Board | None = None
        self.acting = False
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
        if self.model is not None:
            for view in self.views.values():
                view.show(self.model)

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
        if self.acting:
            return  # an action reloads when it is done
        self.model = model = message.model
        if model.project:
            self.project = model.project["slug"]
        self.query_one(HeaderBar).show(self.project or "no project", model.worker, model.queued)
        self.query_one(LifecycleBar).show(model.lifecycle)
        self._show_tabs()
        if not self._welcomed and self.project:
            self._welcome(model)
        for view in self.views.values():
            view.show(model)
        self._show_keys()

    def _welcome(self, model: Board) -> None:
        self._welcomed = True
        since = self.seen.get(self.project or "")
        self.views["overview"].since = since or ""
        if since:
            new = sum(d.count for d in model.decisions if d.created_at > since)
            when = datetime.fromisoformat(since).astimezone().strftime("%H:%M")
            self.say(f"Welcome back. {new} new decision{'' if new == 1 else 's'} since {when}."
                     if new else f"Welcome back. Nothing new since {when}.", "info")

    def on_unmount(self) -> None:
        # The next visit's "Since you left" is when this one ended.
        if self.project:
            state = _read_state(self.db_path)
            state.setdefault("seen", {})[self.project] = datetime.now(timezone.utc).isoformat()
            state["project"] = self.project
            try:
                _state_file(self.db_path).write_text(json.dumps(state))
            except OSError:
                pass

    def open_decision(self, decision_id: str) -> None:
        needs = self.views["needs"]
        self.action_view("needs")
        needs.selected = decision_id
        needs.show(self.model)
        needs.open_detail()

    def start(self, action: str) -> None:
        """The Next-to-start button, or its key."""
        model, db, project = self.model, self.db_path, self.project
        if model is None or not action:
            return
        if action == "interview":
            if model.brief or model.intake_open:
                self.say("The interview is already done or waiting for you in Needs you.", "warn")
                return
            self.act(lambda: start_refinement(project, db_path=db),
                     "Interview started. Questions arrive in Needs you.", "info")
        elif action == "backlog":
            self.act(lambda: start_backlog(project, db_path=db),
                     "Backlog proposal started. It arrives in Needs you.", "info")
        elif action == "pause":
            self.action_toggle_pause()
        elif action == "batch":
            self.open_batch()

    def story_action(self, story, key: str) -> None:
        """A key (or its button) on a story's detail."""
        model, db, project = self.model, self.db_path, self.project
        failed = next((d for d in model.decisions if d.story == story.n and d.kind == "fail"), None)
        decision = next((d for d in model.decisions if d.story == story.n and d.kind != "fail"), None)
        if key == "r" and failed is not None:
            self.decide(failed, "r", self.views["needs"])
        elif key == "r" and story.state in ("draft", "notready"):
            self.act(lambda: start_refinement(project, story.n, db_path=db),
                     f"Refining Story #{story.n} {story.title}: its questions arrive in Needs you.", "info")
        elif key == "m" and failed is not None and failed.data["retryable"]:
            self.open_decision(failed.id)
            self.views["needs"].start_typing("note")
        elif key == "b":
            self.open_batch()
        elif key == "enter" and decision is not None:
            self.open_decision(decision.id)
        elif key == "S":
            self.stop_story(story)

    def stop_story(self, story) -> None:
        self.say("Stopping is not built yet.", "warn")

    def action_start(self, action: str) -> None:
        self.start(action)

    def open_batch(self) -> None:
        self.say("No stories are ready yet." if not (self.model and self.model.ready_plans)
                 else "The batch view is not built yet.", "warn")

    def action_toggle_pause(self) -> None:
        if self.model is None or not self.project:
            return
        paused, db, project = self.model.paused, self.db_path, self.project
        self.act(lambda: pause_project(project, not paused, db_path=db),
                 "New starts resumed." if paused else "New starts paused. Running work continues.",
                 "ok" if paused else "warn", worker=False)

    def action_batch_key(self) -> None:
        model = self.model
        if model is None:
            return
        if model.ready_plans:
            self.open_batch()
        elif model.brief and not model.stories and not model.backlog_open:
            self.start("backlog")
        else:
            self.say("No stories are ready yet.", "warn")

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

    # ── actions: every decision goes through `runs`, off the UI thread ───
    def act(self, work, message: str, kind: str = "ok", *, worker: bool = True,
            then=None) -> None:
        if self.acting:
            return
        self.acting = True
        self._act(work, message, kind, worker, then)

    @work(thread=True, group="board-action")
    def _act(self, work, message, kind, worker, then) -> None:
        try:
            work()
            if worker:
                start_worker(db_path=self.db_path)
            ok = True
        except Exception as exc:  # a refusal or a crash: said in red, never a crash
            message, kind, ok = str(exc), "err", False
        self.app.call_from_thread(self._acted, message, kind, then if ok else None)

    def _acted(self, message: str, kind: str, then) -> None:
        self.acting = False
        self.say(message, kind)
        if then is not None:
            then()
        self.load()

    def keep_draft(self, decision_id: str, value: str) -> None:
        self._keep_draft(decision_id, value)

    @work(thread=True, group="board-draft")
    def _keep_draft(self, decision_id: str, value: str) -> None:
        try:
            save_draft(decision_id, value, db_path=self.db_path)
        except Exception:
            pass  # the draft stays in memory; the next keystroke saves it again

    def answer(self, d: Decision, value: str, view) -> None:
        data = d.data
        question = data["pending"][0]
        left, k, total = d.count - 1, data["qi"] + 2, data["total"]
        decided = value.strip().lower() == "you decide"
        if left:
            message = (f"Left to the factory, recorded as an assumption. Question {k} of {total} is next."
                       if decided else f"Answered. Question {k} of {total} is next.")
        elif d.story is not None:
            message = f"{d.title}: all {total} answered. Refinement continues."
        else:
            message = f"{d.title}: all {total} answered. The next step is being prepared."
        self.act(lambda: answer(question["id"], value, db_path=self.db_path), message,
                 then=lambda: view.after_answer(d, finished=not left))

    def decide(self, d: Decision, key: str, view) -> None:
        """A key (or its button) on a decision's detail."""
        db = self.db_path
        if d.kind == "questions" and key == "s":
            view.skip()
            self.say("Skipped for now. It stays in Needs you.", "info")
            return
        if key in ("c", "r", "m") and (d.kind in ("backlog", "brief", "release") and key == "c"
                                       or d.kind == "ckpt" and key == "r"
                                       or d.kind == "fail" and key == "m" and d.data["retryable"]):
            view.start_typing({"c": "changes", "r": "reject", "m": "note"}[key])
            return
        if d.kind == "fail" and key == "d":
            view.show_error = not view.show_error
            view._fill_detail()
            return
        if key != "a" and not (d.kind == "fail" and key in ("r", "x")):
            return
        if d.kind == "backlog":
            n = len(d.data["decision"]["context"].get("stories", []))
            self.act(lambda: answer(d.data["decision"]["id"], "approve", db_path=db),
                     f"Backlog approved · {n} stories added as drafts.")
        elif d.kind == "brief":
            self.act(lambda: answer(d.data["decision"]["id"], "approve", db_path=db),
                     "Brief updated. Affected stories will be re-checked." if d.data.get("change")
                     else "Brief approved. Next: propose a backlog.")
        elif d.kind == "ckpt":
            name = d.data["stage_name"]
            self.act(lambda: queue_resume(d.data["run"]["id"], "approve", db_path=db),
                     f"{name.capitalize()} approved. {d.title} continues.")
        elif d.kind == "release":
            if "run" in d.data:
                self.act(lambda: queue_resume(d.data["run"]["id"], "approve", db_path=db),
                         f"Releasing {d.title.removeprefix('Release ')}…", "info")
            else:
                self.act(lambda: queue_release(d.data["decision"]["id"], db_path=db),
                         "Merging the combined batch…", "info")
        elif d.kind == "fail" and key == "r":
            if d.data["retryable"]:
                self.act(lambda: retry_job(d.data["job"]["id"], db_path=db),
                         f"Retrying {d.sub.lower().replace(' failed', '')} for {d.title}…", "info")
            else:
                self.act(lambda: recover_job(d.data["job"]["id"], db_path=db),
                         "Stopped work settled; nothing was repeated.", worker=False)
        elif d.kind == "fail" and key == "x":
            self.act(lambda: dismiss_job(d.data["job"]["id"], db_path=db),
                     f"Dismissed. {d.title} stays as it was; refine it later from Stories.",
                     worker=False)

    def send_text(self, d: Decision, kind: str, text: str, view) -> None:
        db = self.db_path
        if kind == "note":
            self.act(lambda: retry_job(d.data["job"]["id"], note=text, db_path=db),
                     f"Retrying {d.title} with your note…", "info")
        elif kind == "reject":
            self.act(lambda: queue_resume(d.data["run"]["id"], "reject", text, db_path=db),
                     f"Feedback sent. The {d.data['stage_name']} runs again.")
        elif d.kind == "backlog":
            self.act(lambda: request_changes(d.data["decision"]["id"], text, db_path=db),
                     "Changes sent. A new proposal is being written.")
        elif d.kind == "brief":
            self.act(lambda: request_changes(d.data["decision"]["id"], text, db_path=db),
                     "Changes sent. The brief is being redrafted.")
        elif d.kind == "release" and "run" in d.data:
            self.act(lambda: queue_resume(d.data["run"]["id"], "reject", text, db_path=db),
                     "Sent back. It returns here once verified again.")
        else:
            self.say("A combined batch can only be approved here; reject its stories instead.", "warn")


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


def _read_state(db_path: Path) -> dict:
    try:
        data = json.loads(_state_file(db_path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _last_project(db_path: Path) -> str | None:
    return _read_state(db_path).get("project")
