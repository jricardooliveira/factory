"""The brief/backlog view and the old run board's keys, driven from the board.

The durable intake interview on the workflow screen: test_workflow_intake.py.

Offline: `factory.runs.interview.run_agent` is patched with frozen JSON and the
engine is pinned to opencode, so no test can reach a model.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Button

from factory.adapters.opencode import AgentResult
from factory.evidence.backlog import BACKLOG_RELPATH
from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.interview_screen import QuestionScreen, ReviewScreen
from factory.interfaces.board.tui import FactoryBoard
from factory.state import db
from factory.workspace.projects import create_project



class BoardInterviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        self.db_path = home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=home, slug="shop")
        self.repo = Path(self.project["repo_path"])
        env = patch.dict("os.environ", {"FACTORY_INTERVIEW_ENGINE": "opencode"})
        env.start()
        self.addCleanup(env.stop)

    def _agent(self, *results: AgentResult):
        mock = patch("factory.runs.interview.run_agent", side_effect=list(results))
        started = mock.start()
        self.addCleanup(mock.stop)
        return started

    def _notes(self, app) -> str:
        return "\n".join(str(n.message) for n in app._notifications)

    async def _wait_for(self, app, pilot, screen_type):
        for _ in range(200):
            if isinstance(app.screen, screen_type):
                return app.screen
            await pilot.pause(0.02)
        self.fail(f"{screen_type.__name__} never opened (screen: {app.screen!r})")

    async def test_without_a_project_selected_it_only_shows_a_notice(self) -> None:
        agent = self._agent()
        app = FactoryBoard(self.db_path, start_overview=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            self.assertIn("project", self._notes(app).lower())
            self.assertNotIsInstance(app.screen, QuestionScreen)
        agent.assert_not_called()

    async def test_a_project_without_runs_can_be_selected(self) -> None:
        app = FactoryBoard(self.db_path, start_overview=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_cycle_project()
            self.assertEqual(app._project_filter, "shop")

    async def test_b_shows_the_brief_and_backlog_read_only(self) -> None:
        (self.repo / BRIEF_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / BRIEF_RELPATH).write_text("# Brief\nSells shoes\n", encoding="utf-8")
        (self.repo / BACKLOG_RELPATH).write_text("# Backlog\n1. Cart\n", encoding="utf-8")
        app = FactoryBoard(self.db_path, start_overview=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_cycle_project()
            await pilot.press("B")
            screen = await self._wait_for(app, pilot, ReviewScreen)
            self.assertIn("Sells shoes", screen.text)
            self.assertIn("1. Cart", screen.text)
            self.assertEqual(len(screen.query("#approve")), 0)  # read-only
            await pilot.click("#close")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, ReviewScreen)

    async def test_board_keys_do_nothing_under_an_interview_modal(self) -> None:
        # A button has focus, not the Input: "a" must not approve the run selected
        # behind the modal (e.g. a release checkpoint), nor "q" drop the interview.
        app = FactoryBoard(self.db_path, start_overview=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ReviewScreen("Approve this product brief?", "brief", review=True))
            await pilot.pause()
            app.screen.query_one("#approve", Button).focus()
            await pilot.pause()
            self.assertIsInstance(app.focused, Button)
            with patch.object(app, "_resume") as resume, patch.object(app, "exit") as exit_:
                for key in ("a", "x", "d", "i", "B", "q"):
                    await pilot.press(key)
                await pilot.pause()
            resume.assert_not_called()
            exit_.assert_not_called()
            self.assertIsInstance(app.screen, ReviewScreen)


if __name__ == "__main__":
    unittest.main()
