"""Headless smoke test for the Textual board (mounts, composes, populates)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import json

from factory.state import db
from factory.tui import FactoryBoard
from textual.widgets import Button, DataTable, TextArea


class TuiMountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Bookmark API", "Build a bookmark REST API")
            rid = db.start_run(conn, "US-0001")
            db.log_agent(
                conn, rid, "architect-agent", "p",
                json.dumps({"architecture_notes": "Layered FastAPI app",
                            "modules_affected": ["api.py", "models.py"]}),
                verdict="pass",
            )
            db.update_run_stage(conn, rid, "gate-2-architect")
            db.log_gate(conn, rid, "gate-2-architect", True, "needs human",
                        needs_human=True, human_questions="Approve breaking change?")
            db.finish_run(conn, rid, "waiting_human")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def _select_first_row(self, app, pilot) -> None:
        table = app.query_one("#runs", DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("enter")  # fires RowSelected
        await pilot.pause()

    async def test_board_mounts_and_populates(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#runs", DataTable)
            self.assertEqual(len(table.columns), 6)
            self.assertEqual(table.row_count, 1)  # one parked run
            await self._select_first_row(app, pilot)
            self.assertEqual(app.selected_id, 1)

    async def test_parked_run_enables_feedback_box(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await self._select_first_row(app, pilot)
            # Parked run -> feedback editable.
            self.assertFalse(app.query_one("#feedback", TextArea).disabled)

    async def test_detail_pane_shows_context_and_enables_actions(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await self._select_first_row(app, pilot)
            text = app._detail_text
            self.assertIn("Build a bookmark REST API", text)   # original request
            self.assertIn("signing off", text)                 # what you're deciding
            self.assertIn("Approve breaking change?", text)     # the flagged question
            self.assertIn("Layered FastAPI app", text)          # proposed design
            # Parked run -> actions enabled
            self.assertFalse(app.query_one("#approve", Button).disabled)
            self.assertFalse(app.query_one("#reject", Button).disabled)

    async def test_reject_without_feedback_is_refused(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await self._select_first_row(app, pilot)
            app.query_one("#feedback", TextArea).clear()
            app.action_reject()  # run selected, but no feedback typed
            await pilot.pause()
            # Refused -> not busy, no resume attempted.
            self.assertFalse(app._busy)


def _make_project(conn, pid: str, slug: str) -> None:
    conn.execute(
        "INSERT INTO projects (id, slug, name, repo_path, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'active', 't', 't')",
        (pid, slug, slug.title(), f"/tmp/{slug}/repo"),
    )


class DismissAndFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            _make_project(conn, "PROJ-001", "alpha")
            _make_project(conn, "PROJ-002", "beta")
            for sid, pid in [("US-0001", "PROJ-001"), ("US-0002", "PROJ-002")]:
                db.create_story(conn, sid, f"Story {sid}", "req", project_id=pid)
                rid = db.start_run(conn, sid, project_id=pid)
                db.finish_run(conn, rid, "blocked", error="boom")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def _select_first_row(self, app, pilot) -> None:
        app.query_one("#runs", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    async def test_dismiss_archives_nonparked_run(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#runs", DataTable)
            self.assertEqual(table.row_count, 2)
            await self._select_first_row(app, pilot)
            # Dismiss enabled (blocked run), approve/reject + feedback disabled.
            self.assertFalse(app.query_one("#dismiss", Button).disabled)
            self.assertTrue(app.query_one("#approve", Button).disabled)
            self.assertTrue(app.query_one("#feedback", TextArea).disabled)
            app.action_dismiss()
            await pilot.pause()
            self.assertEqual(app.query_one("#runs", DataTable).row_count, 1)

    async def test_project_filter_narrows_board(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one("#runs", DataTable).row_count, 2)
            app.action_cycle_project()  # all -> first project
            await pilot.pause()
            self.assertIsNotNone(app._project_filter)
            self.assertEqual(app.query_one("#runs", DataTable).row_count, 1)


class KanbanViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            # one blocked, one completed -> different kanban columns
            for sid, status in [("US-0001", "blocked"), ("US-0002", "completed")]:
                db.create_story(conn, sid, f"Story {sid}", "req")
                rid = db.start_run(conn, sid)
                db.update_run_stage(conn, rid, "coder-agent")
                db.finish_run(conn, rid, status, error="boom" if status == "blocked" else None)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_toggle_to_kanban_populates_columns(self) -> None:
        from textual.widgets import ListView

        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(app._view, "table")
            self.assertFalse(app.query_one("#kanban").display)
            await pilot.press("v")
            await app.workers.wait_for_complete()  # kanban populate is an async worker
            await pilot.pause()
            self.assertEqual(app._view, "kanban")
            self.assertTrue(app.query_one("#kanban").display)
            blocked = app.query_one("#kcol-blocked", ListView)
            done = app.query_one("#kcol-done", ListView)
            self.assertEqual(len(blocked.children), 1)
            self.assertEqual(len(done.children), 1)  # completed shows in kanban Done

    async def test_left_right_moves_between_columns(self) -> None:
        from textual.widgets import ListView

        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("v")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Focus the first column, then move right and confirm focus changed
            # to a different kanban column ListView.
            app.query_one("#kcol-needs-you", ListView).focus()
            await pilot.pause()
            app.action_focus_right()
            await pilot.pause()
            focused = app.focused
            self.assertIsInstance(focused, ListView)
            self.assertTrue(focused.id.startswith("kcol-"))
            self.assertNotEqual(focused.id, "kcol-needs-you")

    async def test_dismiss_in_kanban_does_not_crash(self) -> None:
        from textual.widgets import ListView

        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("v")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Select the blocked card, dismiss it, and refresh again.
            blocked = app.query_one("#kcol-blocked", ListView)
            app.selected_id = blocked.children[0].run_id
            app.action_dismiss()
            await app.workers.wait_for_complete()
            await pilot.pause()
            # No DuplicateIds crash; the dismissed card is gone.
            self.assertEqual(len(app.query_one("#kcol-blocked", ListView).children), 0)


class CursorStabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            for n in range(1, 4):
                sid = f"US-000{n}"
                db.create_story(conn, sid, f"Story {n}", "req")
                rid = db.start_run(conn, sid)
                db.finish_run(conn, rid, "failed", error=f"boom {n}")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_cursor_survives_refresh(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#runs", DataTable)
            self.assertEqual(table.row_count, 3)
            table.focus()
            await pilot.press("down", "down")  # move to row 2
            await pilot.pause()
            moved_to = table.cursor_coordinate.row
            self.assertEqual(moved_to, 2)
            # Refresh several times — data unchanged, cursor must not jump to top.
            app.reload()
            app.reload()
            await pilot.pause()
            self.assertEqual(table.cursor_coordinate.row, 2)


if __name__ == "__main__":
    unittest.main()
