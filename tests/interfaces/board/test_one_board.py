"""One board: the workflow screen is home, the run table is "All runs" behind it.

Live (2026-10-03): home's footer listed the hidden run board's keys (a Approve,
x Reject, d Dismiss, v Table/Kanban…) and they acted on the run selected
underneath; the header said "Project: all · table" whatever was chosen.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Select

from factory.interfaces.board.tui import FactoryBoard
from factory.interfaces.board.workflow_screen import WorkflowScreen
from factory.state import db
from factory.workspace.projects import create_project

RUN_BOARD_ACTIONS = {"approve", "reject", "dismiss", "toggle_view", "focus_left", "focus_right",
                     "cycle_project", "brief", "interview", "status"}


class OneBoardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        self.db_path = home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=home, slug="shop")
        with db.get_db(self.db_path) as conn:  # a parked run under the home screen
            db.create_story(conn, "US-0001", "Cart", "Add a cart", project_id=self.project["id"])
            rid = db.start_run(conn, "US-0001", project_id=self.project["id"])
            db.log_agent(conn, rid, "architect-agent", "p",
                         json.dumps({"architecture_notes": "n", "modules_affected": ["a.py"]}),
                         verdict="pass")
            db.log_gate(conn, rid, "gate-2-architect", True, "needs human", needs_human=True,
                        human_questions="Approve?")
            db.finish_run(conn, rid, "waiting_human")
        self.run_id = rid

    def _footer_actions(self, app) -> set[str]:
        return {b.binding.action for b in app.screen.active_bindings.values() if b.enabled}

    async def test_home_shows_and_obeys_only_its_own_keys(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, WorkflowScreen)
            app.selected_id = self.run_id  # the parked run is selected underneath
            self.assertFalse(RUN_BOARD_ACTIONS & self._footer_actions(app))
            with patch.object(app, "_resume") as resume, patch.object(app, "_dismiss") as dismiss:
                for key in ("a", "x", "d", "v", "p"):
                    await pilot.press(key)
                await pilot.pause()
            resume.assert_not_called()
            dismiss.assert_not_called()
            self.assertIsInstance(app.screen, WorkflowScreen)

    async def test_the_header_names_the_chosen_project(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen = app.screen
            self.assertEqual(screen.sub_title, "All projects")
            screen.query_one("#wf-project", Select).value = "shop"
            await pilot.pause()
            self.assertEqual(screen.sub_title, "shop")
            self.assertNotIn("table", screen.sub_title)

    async def test_escape_shows_all_runs_and_o_comes_home(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, WorkflowScreen)
            self.assertIn("approve", self._footer_actions(app))  # the run board's own keys
            await pilot.press("o")
            await pilot.pause()
            self.assertIsInstance(app.screen, WorkflowScreen)

    async def test_a_refresh_tick_during_shutdown_is_not_a_crash(self) -> None:
        # Flaky in CI before: the run board's 2 s reload fired while the app was
        # tearing down, after "#runs" was gone (NoMatches on the default screen).
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            for table in app.screen_stack[0].query("#runs"):
                await table.remove()
            app._last_sig = None  # something changed: the tick rebuilds the table
            app.reload()  # must not raise


if __name__ == "__main__":
    unittest.main()
