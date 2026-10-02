"""Interactive Textual board: see status live, give input in place.

Select a parked run, read what it's asking, type feedback, and approve/reject
without leaving the screen. Resume work runs in a worker thread (the pipeline can
take minutes) through `factory.runs` with no event callback: the service never
prints, so nothing can corrupt the screen, and the board's own refresh tick picks
up the progress from the DB.
"""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
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

from factory.evidence.progress import render_flow, run_pipeline_progress
from factory.interfaces.board.data import (
    KANBAN_COLUMNS,
    BoardRun,
    group_by_column,
    list_projects_on_board,
    load_board_runs,
)
from factory.runs import resume_run
from factory.state.db import archive_run, get_db


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
    ]

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
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

    def on_mount(self) -> None:
        table = self.query_one("#runs", DataTable)
        table.add_columns("#", "Project", "Story", "State", "Stage / waiting on", "Cost")
        self.query_one("#kanban").display = False  # table is the default view
        self.sub_title = "Project: all  ·  table"
        self.reload()
        self.set_interval(2.0, self.reload)

    # ── data ──────────────────────────────────────────────────────
    def reload(self) -> None:
        if self._busy:
            return
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
            detail = r.questions[0][:40] if r.questions else (r.error or r.stage or "")[:40]
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

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
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
        flow = render_flow(run_pipeline_progress(self.db_path, run.id))
        self.query_one("#pipeline", Static).update(flow)

        lines: list[str] = []
        if run.request:
            lines.append(f"[b]Original request[/b]\n{escape(run.request)}")
        if run.needs_input:
            lines.append(f"\n[b]You are signing off {escape(run.decision)}.[/b]")
            if run.questions:
                lines.append("The factory flagged:")
                lines.extend(f"[yellow]?[/yellow] {escape(q)}" for q in run.questions)
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
        options: list[str | None] = [None, *self._all_projects]
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
        if run.needs_input:
            self.notify("This run is awaiting your decision — approve or reject it.", severity="warning")
            return
        with get_db(self.db_path) as conn:
            archive_run(conn, run.id)
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
        self._busy = True
        self.query_one("#result", Static).update(
            f"[dim]Run #{run.id}: {action} in progress (may take a minute)…[/dim]"
        )
        self._do_resume(run.id, action, feedback or None)

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
            self.query_one("#result", Static).update(f"[green]Run #{run_id} {action} done.[/green]")
        self.reload()


def run_board_tui(db_path: Path) -> None:
    FactoryBoard(db_path).run()
