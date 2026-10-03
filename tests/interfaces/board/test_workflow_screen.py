"""The workflow screen's buttons: an action never takes the board down.

Live (2026-10-03): every action button (Propose backlog, Refine story, Interview…)
crashed the board — the action thread reported back through a method a Screen
does not have. The conftest stubs the detached worker; no model is called here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import Select, Static

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.tui import FactoryBoard
from factory.interfaces.board.workflow_screen import WorkflowScreen
from factory.state import db
from factory.state import workflow as store
from factory.workspace.projects import create_project


class WorkflowScreenActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        self.db_path = home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=home, slug="shop")
        brief = Path(self.project["repo_path"]) / BRIEF_RELPATH
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text("# Product brief — shop\n\nSells socks.\n")

    async def _settle(self, screen: WorkflowScreen, pilot) -> None:
        for _ in range(50):
            await pilot.pause(0.05)
            if not screen._acting:
                return
        self.fail("the action never finished")

    async def test_propose_backlog_queues_the_job_and_the_board_survives(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen = app.screen
            self.assertIsInstance(screen, WorkflowScreen)
            screen.query_one("#wf-project", Select).value = "shop"
            await pilot.pause()
            await pilot.click("#wf-backlog")
            await self._settle(screen, pilot)
            self.assertIs(app.screen, screen)
            note = str(screen.query_one("#wf-note", Static).render())
            self.assertIn("saved", note.lower())
        with db.get_db(self.db_path) as conn:
            jobs = store.list_jobs(conn, self.project["id"])
        self.assertEqual([j["kind"] for j in jobs], ["backlog"])


if __name__ == "__main__":
    unittest.main()
