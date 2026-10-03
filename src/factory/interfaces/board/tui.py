"""Interactive Textual board: see status live, give input in place.

Select a parked run, read what it's asking, type feedback, and approve/reject
without leaving the screen. Resume work runs in a worker thread (the pipeline can
take minutes) through `factory.runs` with no event callback: the service never
prints, so nothing can corrupt the screen, and the board's own refresh tick picks
up the progress from the DB.

`i` runs the intake interview for the filtered project in a worker thread whose
callbacks block on the modals in `interview_screen`; `B` shows its brief and backlog.
"""

from __future__ import annotations

import queue
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)
from textual.worker import get_current_worker

from factory.interfaces.board.data import (
    KANBAN_COLUMNS,
    BoardRun,
    group_by_column,
    list_projects_on_board,
    load_board_runs,
)
from factory.domain.interview import InterviewQuestion
from factory.domain.project_spec import ProjectSpec
from factory.evidence.backlog import BACKLOG_RELPATH
from factory.evidence.brief import load_brief
from factory.interfaces.board.interview_screen import PromptScreen, QuestionScreen, ReviewScreen
from factory.interfaces.board.workflow_screen import WorkflowScreen
from factory.runs.batches import queue_resume
from factory.runs.refinement import start_backlog, start_refinement
from factory.runs.worker import start_worker
from factory.interfaces.render.review import render_flow
from factory.interfaces.render.status import status_lines
from factory.preflight.doctor import run_doctor
from factory.runs import (
    RunError,
    all_project_status,
    dismiss_run,
    has_brief,
    next_story,
    project_status,
    propose_backlog,
    reconcile_stale,
    replay_run,
    resume_run,
    retry_run,
    run_backlog_story,
    run_interview,
    run_project_pipeline,
    run_story_interview,
)
from factory.runs.queries import run_stages
from factory.workspace.projects import create_project, get_project, list_projects


def _col_slug(name: str) -> str:
    return name.replace(" ", "-").replace("/", "-").lower()

_STATE_STYLE = {
    "NEEDS YOU": "bold yellow",
    "running": "cyan",
    "failed": "red",
    "blocked": "red",
    "completed": "green",
}


class FactoryBoard(App):
    CSS = """
    #body { height: 1fr; }
    #runs { width: 55%; border: round $panel; }
    #kanban { width: 55%; }
    .kcol { width: 1fr; border: round $panel; }
    .kcol-title { text-style: bold; padding: 0 1; }
    #detail { width: 45%; border: round $panel; padding: 0 1; }
    #questions { color: $warning; margin: 1 0; }
    #feedback { height: 8; margin: 1 0; }
    #result { color: $text-muted; margin-top: 1; }
    .actions { height: auto; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("v", "toggle_view", "Table/Kanban"),
        ("left", "focus_left", "◀ col"),
        ("right", "focus_right", "col ▶"),
        ("p", "cycle_project", "Project filter"),
        ("a", "approve", "Approve"),
        ("x", "reject", "Reject"),
        ("d", "dismiss", "Dismiss"),
        ("i", "interview", "Interview"),
        ("B", "brief", "Brief/backlog"),
        ("s", "status", "Status"),
        ("o", "project_overview", "Home"),
        ("m", "command_palette", "Menu"),
    ]

    def __init__(self, db_path: Path, *, start_overview: bool = True) -> None:
        super().__init__()
        self.db_path = db_path
        self._start_overview = start_overview
        self.selected_id: int | None = None
        self._runs: dict[int, BoardRun] = {}
        self._busy = False
        self._detail_text = ""
        self._last_sig: tuple | None = None
        self._project_filter: str | None = None  # None = all projects
        self._all_projects: list[str] = []
        self._view = "table"  # "table" | "kanban"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield DataTable(id="runs", cursor_type="row")
            with Horizontal(id="kanban"):
                for col in KANBAN_COLUMNS:
                    slug = _col_slug(col)
                    with Vertical(classes="kcol"):
                        yield Label(col, classes="kcol-title", id=f"ktitle-{slug}")
                        yield ListView(id=f"kcol-{slug}")
            with VerticalScroll(id="detail"):
                yield Static("Select a run", id="qtitle")
                yield Static("", id="pipeline")
                yield Static("", id="questions")
                yield Label("Feedback (required to reject):")
                yield TextArea(id="feedback", disabled=True)
                with Horizontal(classes="actions"):
                    yield Button("Approve", id="approve", variant="success", disabled=True)
                    yield Button("Reject", id="reject", variant="error", disabled=True)
                    yield Button("Dismiss", id="dismiss", variant="warning", disabled=True)
                yield Static("", id="result")
        yield Footer()

    # The run board's own keys. With the home (workflow) screen on top they stay inert
    # and out of its footer: "a" approved whatever run was selected underneath.
    _RUN_BOARD_ACTIONS = frozenset({
        "approve", "reject", "dismiss", "toggle_view", "focus_left", "focus_right",
        "cycle_project", "brief", "interview", "status", "refresh",
    })

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in self._RUN_BOARD_ACTIONS and isinstance(self.screen, WorkflowScreen):
            return False
        return True

    def on_mount(self) -> None:
        table = self.query_one("#runs", DataTable)
        table.add_columns("#", "Project", "Story", "State", "Stage / waiting on", "Cost")
        self.query_one("#kanban").display = False  # table is the detailed run view
        if self._start_overview:
            self.call_after_refresh(self.action_project_overview)
        self.sub_title = "Project: all  ·  table"
        self.reload()
        self.set_interval(2.0, self.reload)

    # ── data ──────────────────────────────────────────────────────
    def reload(self) -> None:
        all_runs = load_board_runs(self.db_path, include_done=(self._view == "kanban"))
        self._all_projects = list_projects_on_board(all_runs)
        runs = [r for r in all_runs if self._project_filter in (None, r.project)]
        # Skip the rebuild entirely when nothing visible changed — avoids flicker
        # and keeps the cursor/selection where the user put it.
        sig = (self._view, self._project_filter) + tuple(
            (r.id, r.status, round(r.cost, 4), len(r.questions)) for r in runs
        )
        if sig == self._last_sig:
            return
        self._last_sig = sig
        self._runs = {r.id: r for r in runs}

        if self._view == "table":
            self._populate_table(runs)
        else:
            self._populate_kanban(runs)

        if self.selected_id in self._runs:
            self._show_detail(self._runs[self.selected_id])
        elif self.selected_id is not None:
            self.selected_id = None

    def _populate_table(self, runs: list[BoardRun]) -> None:
        table = self.query_one("#runs", DataTable)
        prev_id: str | None = None
        try:
            if table.row_count:
                prev_id = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            prev_id = None
        table.clear()
        for r in runs:
            detail = (r.questions[0] if r.questions
                      else (r.error or r.stage or "").split("\n", 1)[0])[:40]
            table.add_row(
                str(r.id), r.project[:14], r.title[:24],
                Text(r.state_label, style=_STATE_STYLE.get(r.state_label, "")),
                detail, f"${r.cost:.4f}", key=str(r.id),
            )
        if prev_id is not None:
            for i, r in enumerate(runs):
                if str(r.id) == prev_id:
                    table.move_cursor(row=i)
                    break

    @work(exclusive=True, group="kanban")
    async def _populate_kanban(self, runs: list[BoardRun]) -> None:
        # Exclusive worker + awaited clear(): ListView.clear() is async, so doing
        # it inline races (old cards linger and collide on the next append). The
        # run id lives on the ListItem as an attribute, not a widget id, to avoid
        # DuplicateIds across refreshes.
        cols = group_by_column(runs)
        for col in KANBAN_COLUMNS:
            slug = _col_slug(col)
            lv = self.query_one(f"#kcol-{slug}", ListView)
            await lv.clear()
            self.query_one(f"#ktitle-{slug}", Label).update(f"{col} ({len(cols[col])})")
            for r in cols[col]:
                style = _STATE_STYLE.get(r.state_label) or "white"
                card = (
                    f"[b]#{r.id}[/b] [{style}]{escape(r.state_label)}[/{style}]\n"
                    f"{escape(r.title[:22])}"
                )
                item = ListItem(Label(card))
                item.run_id = r.id  # type: ignore[attr-defined]
                lv.append(item)

    @on(DataTable.RowSelected, "#runs")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        # Scoped to the run table: unscoped, Enter on the workflow screen's list bubbled
        # here and crashed the board on int("decision:…").
        if event.row_key.value is None:
            return
        self.selected_id = int(event.row_key.value)
        run = self._runs.get(self.selected_id)
        if run:
            self._show_detail(run)

    def _show_detail(self, run: BoardRun) -> None:
        state_style = _STATE_STYLE.get(run.state_label) or "white"
        self.query_one("#qtitle", Static).update(
            f"[b]#{run.id} {escape(run.story_id)}[/b] — {escape(run.title)}  "
            f"[{state_style}]{escape(run.state_label)}[/]"
        )
        flow = render_flow(run_stages(run.id, db_path=self.db_path))
        self.query_one("#pipeline", Static).update(flow)

        lines: list[str] = []
        if run.request:
            lines.append(f"[b]Original request[/b]\n{escape(run.request)}")
        if run.needs_input:
            lines.append(f"\n[b]You are signing off {escape(run.decision)}.[/b]")
            if run.questions:
                lines.append("The factory flagged:")
                lines.extend(f"[yellow]?[/yellow] {escape(q)}" for q in run.questions)
            if run.release_summary:
                lines.append(f"\n[b]What ships[/b]\n{escape(run.release_summary)}")
            if run.how_to_verify:
                lines.append("\n[b]How to verify[/b]")
                lines.extend(f"• {escape(step)}" for step in run.how_to_verify)
            if run.architecture:
                lines.append(f"\n[b]Proposed design[/b]\n{escape(run.architecture)}")
            if run.modules:
                lines.append(f"\n[b]Modules affected[/b]\n{escape(', '.join(run.modules))}")
            lines.append("\n[dim]Approve to proceed · Reject + feedback to redo.[/dim]")
        elif run.error:
            lines.append(f"\n[red]{escape(run.status.upper())}: {escape(run.error)}[/red]")
            lines.append("[dim]Nothing to approve — use `factory review`/`replay` to act.[/dim]")
        else:
            lines.append(
                f"\n[dim]Stage: {escape(run.stage)} — running. Nothing to approve yet.[/dim]"
            )
        self._detail_text = "\n".join(lines)
        self.query_one("#questions", Static).update(self._detail_text)

        # Only a parked run can be approved/rejected; anything else can be dismissed.
        self.query_one("#approve", Button).disabled = not run.needs_input
        self.query_one("#reject", Button).disabled = not run.needs_input
        self.query_one("#dismiss", Button).disabled = run.needs_input
        # Feedback only applies to a parked run — disable it otherwise so the
        # box doesn't sit there inviting input that goes nowhere.
        self.query_one("#feedback", TextArea).disabled = not run.needs_input

    # ── actions ───────────────────────────────────────────────────
    def action_refresh(self) -> None:
        self.reload()

    def action_toggle_view(self) -> None:
        self._view = "kanban" if self._view == "table" else "table"
        self.query_one("#runs").display = self._view == "table"
        self.query_one("#kanban").display = self._view == "kanban"
        self.sub_title = f"Project: {self._project_filter or 'all'}  ·  {self._view}"
        self._last_sig = None  # force repopulate of the now-visible view
        self.reload()

    def action_focus_left(self) -> None:
        self._focus_adjacent_column(-1)

    def action_focus_right(self) -> None:
        self._focus_adjacent_column(1)

    def _focus_adjacent_column(self, delta: int) -> None:
        """Move focus between kanban columns (kanban view only)."""
        if self._view != "kanban":
            return
        focused = self.focused
        cur = 0
        for i, col in enumerate(KANBAN_COLUMNS):
            if focused is not None and focused.id == f"kcol-{_col_slug(col)}":
                cur = i
                break
        target = (cur + delta) % len(KANBAN_COLUMNS)
        lv = self.query_one(f"#kcol-{_col_slug(KANBAN_COLUMNS[target])}", ListView)
        lv.focus()
        if len(lv.children):  # highlight first card -> updates the detail pane
            lv.index = 0

    @on(ListView.Highlighted)
    @on(ListView.Selected)
    def _kanban_card_selected(self, event) -> None:
        item = getattr(event, "item", None)
        run_id = getattr(item, "run_id", None) if item is not None else None
        if run_id is not None:
            self.selected_id = run_id
            run = self._runs.get(run_id)
            if run:
                self._show_detail(run)

    def action_cycle_project(self) -> None:
        # Registered projects too: a new one has no runs yet, and that is exactly
        # when it needs its interview.
        registered = [p["slug"] for p in list_projects(self.db_path)]
        options: list[str | None] = [
            None, *self._all_projects, *(s for s in registered if s not in self._all_projects)
        ]
        try:
            idx = options.index(self._project_filter)
        except ValueError:
            idx = 0
        self._project_filter = options[(idx + 1) % len(options)]
        self.sub_title = f"Project: {self._project_filter or 'all'}  ·  {self._view}"
        self._last_sig = None  # force rebuild with the new filter
        self.reload()

    def action_approve(self) -> None:
        self._resume("approve")

    def action_reject(self) -> None:
        self._resume("reject")

    def action_dismiss(self) -> None:
        self._dismiss()

    @on(Button.Pressed, "#dismiss")
    def _on_dismiss(self) -> None:
        self._dismiss()

    def _dismiss(self) -> None:
        run = self._runs.get(self.selected_id) if self.selected_id else None
        if not run:
            self.notify("Select a run first.", severity="warning")
            return
        # One dismiss policy for every surface: `runs.dismiss_run` refuses a run
        # awaiting a decision or still running, exactly as `factory dismiss` does.
        try:
            dismiss_run(run.id, db_path=self.db_path)
        except RunError as exc:
            self.notify(str(exc), severity="warning")
            return
        self.notify(f"Run #{run.id} dismissed.")
        self.selected_id = None
        self._last_sig = None
        self.reload()

    @on(Button.Pressed, "#approve")
    def _on_approve(self) -> None:
        self._resume("approve")

    @on(Button.Pressed, "#reject")
    def _on_reject(self) -> None:
        self._resume("reject")

    def _resume(self, action: str) -> None:
        if self._busy:
            self.notify("Already working on a run — wait for it to finish.", severity="warning")
            return
        run = self._runs.get(self.selected_id) if self.selected_id else None
        if not run:
            self.notify("Select a run first.", severity="warning")
            return
        if not run.needs_input:
            self.notify(f"Run #{run.id} is not awaiting input ({run.status}).", severity="warning")
            return
        feedback = self.query_one("#feedback", TextArea).text.strip()
        if action == "reject" and not feedback:
            self.notify("Rejection needs feedback — type it above.", severity="error")
            return
        try:
            queue_resume(run.id, action, feedback or None, db_path=self.db_path)
            start_worker(db_path=self.db_path)
            self.query_one("#result", Static).update(
                f"Run #{run.id}: decision queued. Resulting state will appear in Activity.")
            self.query_one("#feedback", TextArea).clear()
        except Exception as exc:
            self.query_one("#result", Static).update(str(exc))
            self.notify(str(exc), severity="error")

    @work(thread=True, exclusive=True)
    def _do_resume(self, run_id: int, action: str, feedback: str | None) -> None:
        try:
            resume_run(run_id, action, reason=feedback, db_path=self.db_path)
        except Exception as exc:  # a refusal (RunError) or a crash: surface, don't crash the TUI
            self.call_from_thread(self._after_resume, run_id, action, str(exc))
            return
        self.call_from_thread(self._after_resume, run_id, action, None)

    def _after_resume(self, run_id: int, action: str, error: str | None) -> None:
        self._busy = False
        self.query_one("#feedback", TextArea).clear()
        if error:
            self.query_one("#result", Static).update(f"[red]Run #{run_id} {action} errored: {error}[/red]")
        else:
            self.query_one("#result", Static).update(f"Run #{run_id}: {action} processed. Read the refreshed state for the outcome.")
        self.reload()

    # ── intake interview ──────────────────────────────────────────
    def _selected_project(self) -> str | None:
        if self._project_filter in (None, "—"):
            self.notify("Pick a project first: press p to cycle projects.", severity="warning")
            return None
        return self._project_filter

    def action_brief(self) -> None:
        if not (ref := self._selected_project()):
            return
        try:
            repo = Path(get_project(self.db_path, ref)["repo_path"])
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        backlog = repo / BACKLOG_RELPATH
        body = (load_brief(repo) or "(no approved brief yet: press i to interview)") + "\n\n" + (
            backlog.read_text(encoding="utf-8") if backlog.is_file() else "(no backlog yet)"
        )
        self.push_screen(ReviewScreen(f"Project {ref}", body, review=False))

    def action_interview(self) -> None:
        # The workflow screen's project bar knows whether this is a first Interview,
        # completing the agreement, or an amendment; starting one blind from here
        # spent a paid technical interview on an already approved brief.
        if self._selected_project():
            self.action_project_overview()

    @work(thread=True, exclusive=True, group="interview")
    def _do_interview(self, ref: str) -> None:
        try:
            if has_brief(ref, db_path=self.db_path):
                self.call_from_thread(
                    self.notify, f"{ref}: the brief is already approved (B shows it). "
                    f"To change it: factory interview {ref} --amend \"what changed\""
                )
                return
            outcome = run_interview(ref, db_path=self.db_path, ask=self._ask,
                                    approve=self._approve, confirm_stack=self._confirm_stack)
        except Exception as exc:  # RunError, unknown project, or a crash: never kill the board
            self.call_from_thread(self.notify, str(exc), severity="error")
            return
        if outcome.approved:
            self.call_from_thread(self.notify, f"{ref}: brief approved ({outcome.brief_path}).")
        else:
            self.call_from_thread(
                self.notify, f"{ref}: interview paused, {outcome.answers} answer(s) saved."
            )

    def _modal(self, screen: Screen):
        """Show `screen` and block this worker thread until it is dismissed."""
        answer: queue.Queue = queue.Queue()
        self.call_from_thread(self.push_screen, screen, answer.put)
        worker = get_current_worker()
        while True:
            try:
                return answer.get(timeout=0.1)
            except queue.Empty:
                if worker.is_cancelled:  # the board is closing: unblock the thread
                    raise RunError("Interview closed; the answers so far are saved.") from None

    def _ask(self, question: InterviewQuestion, missing: list[str]) -> str | None:
        return self._modal(QuestionScreen(question, missing))

    def _approve(self, brief: str) -> bool | str:
        return self._modal(ReviewScreen("Approve this product brief?", brief, review=True))

    def _confirm_stack(self, spec: ProjectSpec) -> bool | str:
        return self._modal(ReviewScreen("Proposed tech stack (project-spec.json)",
                                        spec.model_dump_json(indent=2), review=True))

    # ── menu (m / ctrl+p): every major `factory` verb ─────────────
    def get_system_commands(self, screen: Screen):
        yield from super().get_system_commands(screen)
        commands = [
            ("Project status", "factory status: where each project stands + next command",
             self.action_status),
            ("Project overview", "visual lifecycle and the next action for this project",
             self.action_project_overview),
            ("New project", "factory project create <slug>", self._menu_new_project),
            ("Interview: product brief", "factory interview <project>", self.action_interview),
            ("Interview: amend brief", "factory interview <project> --amend", self._menu_amend),
            ("Brief & backlog: show", "the approved brief and story list", self.action_brief),
            ("Backlog: propose", "factory backlog <project>", self._menu_backlog),
            ("Story: refine next from backlog", "Prepare the next story without starting a build", self._menu_next),
            ("Story: run next from backlog", "factory next <project>: one story, straight into the pipeline",
             self._menu_run_next),
            ("Story: run a new request", "factory run --project <project>", self._menu_story),
            ("Run: retry selected", "factory retry <run>", lambda: self._menu_run("retry")),
            ("Run: replay selected", "factory replay <run> (zero tokens)",
             lambda: self._menu_run("replay")),
            ("Runs: reconcile stale", "factory reconcile", self._menu_reconcile),
            ("Doctor (offline)", "factory doctor --offline",
             lambda: self._job("Doctor", self._doctor_text, True)),
            ("Doctor (probe models)", "factory doctor (one paid probe per model)",
             lambda: self._job("Doctor", self._doctor_text, False)),
            ("Evals", "factory evals (offline)", lambda: self._job("Evals", self._evals_text)),
            ("Simulate", "factory simulate (offline)",
             lambda: self._job("Simulate", self._simulate_text)),
            ("Metrics", "factory metrics", lambda: self._job("Metrics", self._metrics_text)),
            ("Model tiers", "factory tiers", lambda: self._job("Model tiers", self._tiers_text)),
        ]
        for title, help_text, callback in commands:
            yield SystemCommand(title, help_text, callback)

    def action_status(self) -> None:
        ref = None if self._project_filter in (None, "—") else self._project_filter
        self._job("Status", self._status_text, ref)

    def action_project_overview(self) -> None:
        if isinstance(self.screen, WorkflowScreen):
            return
        def selected(run_id):
            if run_id is not None:
                self.selected_id = run_id
                self._last_sig = None
                self.reload()
                if run := self._runs.get(run_id):
                    self._show_detail(run)
        self.push_screen(WorkflowScreen(self.db_path, self._project_filter), selected)

    def _status_text(self, ref: str | None) -> str:
        statuses = ([project_status(ref, db_path=self.db_path)] if ref
                    else all_project_status(db_path=self.db_path))
        if not statuses:
            return "No projects yet: menu (m) → New project."
        return "\n\n".join("\n".join(status_lines(s)) for s in statuses)

    @work(thread=True, group="menu")
    def _job(self, title: str, fn, *args) -> None:
        """Run one menu command off the UI thread; show its text, or notify its error."""
        try:
            text = fn(*args)
        except Exception as exc:  # a refusal or a crash: surface, never kill the board
            self.call_from_thread(self.notify, f"{title}: {exc}", severity="error")
            return
        if text:
            self.call_from_thread(self.push_screen, ReviewScreen(title, text, review=False))

    def _prompt(self, title: str, placeholder: str, then) -> None:
        self.push_screen(PromptScreen(title, placeholder),
                         lambda value: value and then(value))

    def _menu_new_project(self) -> None:
        def create(slug: str) -> None:
            try:
                project = create_project(self.db_path, slug=slug)
            except (ValueError, OSError) as exc:
                self.notify(str(exc), severity="error")
                return
            self._project_filter = project["slug"]
            self.sub_title = f"Project: {self._project_filter}  ·  {self._view}"
            self._last_sig = None
            self.notify(f"{project['id']} {project['slug']} created — press i to interview.")
        self._prompt("New project", "slug, e.g. habits", create)

    def _menu_amend(self) -> None:
        if ref := self._selected_project():
            self._prompt(f"Amend the {ref} brief", "what changed",
                         lambda change: self._job("Amend", self._interview_text, ref, change))

    def _interview_text(self, ref: str, amend: str) -> str:
        outcome = run_interview(ref, db_path=self.db_path, ask=self._ask, approve=self._approve,
                                confirm_stack=self._confirm_stack, amend=amend)
        return (f"Brief updated: {outcome.brief_path}\nRe-propose the backlog next."
                if outcome.approved else "Amendment paused; the answers so far are saved.")

    def _menu_backlog(self) -> None:
        if ref := self._selected_project():
            try:
                start_backlog(ref, db_path=self.db_path)
                start_worker(db_path=self.db_path)
                self.action_project_overview()
            except Exception as exc:
                self.notify(str(exc), severity="error")

    def _backlog_text(self, ref: str) -> str:
        def review(stories) -> bool | str:
            body = "\n\n".join(f"{i}. {s.title}\n   {s.request}" for i, s in enumerate(stories, 1))
            return self._modal(ReviewScreen("Approve this backlog?", body, review=True))
        outcome = propose_backlog(ref, db_path=self.db_path, review=review)
        return (f"Backlog approved: {outcome.stories} stories. Menu → Story: run next."
                if outcome.approved else "Backlog not approved.")

    def _menu_next(self) -> None:
        if ref := self._selected_project():
            row = next_story(ref, db_path=self.db_path)
            if row:
                start_refinement(ref, row["id"], db_path=self.db_path)
                start_worker(db_path=self.db_path)
                self.action_project_overview()
            else:
                self.notify("No approved story to refine; propose a backlog first.")

    def _menu_run_next(self) -> None:
        # The direct one-story path, next to refine → batch (operator decision, 2026-10-03).
        if ref := self._selected_project():
            self._job("Next story", self._next_text, ref)

    def _next_text(self, ref: str) -> str:
        row = next_story(ref, db_path=self.db_path)
        if row is None:
            return f"No approved story left in the {ref} backlog."
        # Marked started the moment its run exists, and run from its base_commit.
        outcome = run_backlog_story(ref, row, self._clarified(ref, row["request"]),
                                    db_path=self.db_path)
        return f"Story {row['title']!r}: run #{outcome.run_id} {outcome.status}."

    def _menu_story(self) -> None:
        if ref := self._selected_project():
            self._prompt(f"New story for {ref}", "what should be built",
                         lambda request: self._job("Story", self._story_text, ref, request))

    def _story_text(self, ref: str, request: str) -> str:
        outcome = self._start_story(ref, request)
        return f"Run #{outcome.run_id} {outcome.status}."

    def _start_story(self, ref: str, request: str):
        return run_project_pipeline(ref, self._clarified(ref, request), db_path=self.db_path)

    def _clarified(self, ref: str, request: str) -> str:
        # Same order as `factory next`: the story-level interview first, when a brief exists.
        if has_brief(ref, db_path=self.db_path):
            request = run_story_interview(ref, request, db_path=self.db_path, ask=self._ask)
        return request

    def _menu_run(self, verb: str) -> None:
        if not self.selected_id:
            self.notify("Select a run first.", severity="warning")
            return
        fn = retry_run if verb == "retry" else replay_run
        self._job(verb.title(), lambda rid: (
            f"Run #{(o := fn(rid, db_path=self.db_path)).run_id} {o.status}."), self.selected_id)

    def _menu_reconcile(self) -> None:
        self._job("Reconcile", lambda: (
            f"Marked failed: {ids}" if (ids := reconcile_stale(db_path=self.db_path))
            else "No stale runs."))

    @staticmethod
    def _doctor_text(offline: bool) -> str:
        report = run_doctor(offline=offline)
        icon = {"ok": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭"}
        lines = [f"{icon.get(c.status, c.status)} {c.name}  {c.detail}" for c in report.checks]
        lines.append("Ready." if report.passed else "A blocking check failed.")
        return "\n".join(lines)

    @staticmethod
    def _evals_text() -> str:
        from factory.selftest import evals
        return evals.render_markdown(evals.run_all())

    @staticmethod
    def _simulate_text() -> str:
        from factory.selftest.simulate import render_markdown, simulate_all
        return render_markdown(simulate_all())

    def _metrics_text(self) -> str:
        from factory.evidence.metrics import compute, render_markdown
        return render_markdown(compute(self.db_path))

    @staticmethod
    def _tiers_text() -> str:
        from factory.agent_config import tiers
        from factory.agent_config.settings import settings
        lines = [f"Runner: {settings().runner.agents}"]
        for agent, tier in sorted(tiers.AGENT_TIERS.items()):
            lines.append(f"{agent:16} {tier:9} {tiers.model_for_tier(tier)}")
        return "\n".join(lines)


def run_board_tui(db_path: Path) -> None:
    FactoryBoard(db_path).run()
