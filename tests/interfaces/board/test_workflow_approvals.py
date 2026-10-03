"""Approving on the board: the document as text, Approve or Request changes.

Live (2026-10-03): the backlog proposal was a JSON dump (context_revision hash
included) and "Type approve to save these stories"; any other text was refused,
so the operator could not ask for changes. Simulated factory, no model.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import Button, DataTable, Select, Static, TextArea

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.tui import FactoryBoard
from factory.interfaces.board.workflow_screen import WorkflowScreen
from factory.runs.refinement import start_backlog
from factory.state import db
from factory.state import workflow as store
from factory.state.backlog import list_backlog
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, simulate


class BacklogApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        self.db_path = home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=home, slug="shop")
        repo = Path(self.project["repo_path"])
        brief = repo / BRIEF_RELPATH
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text("# Product brief — shop\n\nSells socks.\n")
        git_commit_paths(repo, [brief], "factory: approved brief")
        self.agents = ScriptedAgents(stories=("Catalogue", "Basket"))
        self.enterContext(simulate(self.agents))
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)

    def _pending(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"])
                    if d["status"] == "pending"]

    async def _open(self, app, pilot) -> WorkflowScreen:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#wf-project", Select).value = "shop"
        await pilot.pause()
        screen.query_one("#wf-tabs").active = "inbox"
        await pilot.pause()
        return screen

    async def _settle(self, screen: WorkflowScreen, pilot) -> None:
        for _ in range(100):
            await pilot.pause(0.02)
            if not screen._acting:
                return
        self.fail("the action never finished")

    def _text(self, screen) -> str:
        return str(screen.query_one("#wf-text", Static).render())

    async def test_the_proposal_reads_as_stories_not_json(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            text = self._text(screen)
            row = screen.query_one("#wf-list", DataTable).get_row_at(0)
            primary = screen.query_one("#wf-primary", Button)
            secondary = screen.query_one("#wf-secondary", Button)
            shown = secondary.display  # read while mounted: a torn-down widget is not displayed
        self.assertIn("2 stories", " ".join(map(str, row)))
        for expected in ("1. Catalogue", "Build catalogue.", "Catalogue comes next.", "2. Basket"):
            self.assertIn(expected, text)
        self.assertNotIn("context_revision", text)
        self.assertNotIn("Type approve", text)
        self.assertEqual(str(primary.label), "Approve backlog")
        self.assertEqual(str(secondary.label), "Request changes")
        self.assertTrue(shown)

    async def test_approve_saves_the_stories(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            await pilot.click("#wf-primary")
            await self._settle(screen, pilot)
        with db.get_db(self.db_path) as conn:
            rows = list_backlog(conn, self.project["id"])
        self.assertEqual([r["title"] for r in rows], ["Catalogue", "Basket"])
        self.assertEqual(self._pending(), [])

    async def test_request_changes_sends_the_words_and_a_new_proposal_arrives(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            screen.query_one(TextArea).focus()
            await pilot.press(*"merge them")
            await pilot.click("#wf-secondary")
            await self._settle(screen, pilot)
        self.assertIn("merge them", self.agents.prompts[-1])
        [fresh] = self._pending()
        self.assertEqual(fresh["context"]["feedback"], ["merge them"])

    async def test_request_changes_without_words_sends_nothing(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            await pilot.click("#wf-secondary")
            await pilot.pause()
            note = str(screen.query_one("#wf-note", Static).render())
        self.assertIn("what should change", note.lower())
        self.assertEqual(len(self.agents.prompts), 1)


if __name__ == "__main__":
    unittest.main()
