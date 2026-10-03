"""The product interview on the board, durable: questions are records, nobody blocks.

Simulated factory (tests/simulated.py): scripted agents at run_agent, jobs drained
in-process, the operator played through the Textual pilot. Never the coding stage.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import Button, DataTable, Input, Select, Static

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.interview_screen import PromptScreen
from factory.interfaces.board.tui import FactoryBoard
from factory.interfaces.board.workflow_screen import WorkflowScreen
from factory.state import db
from factory.state.interviews import add_answer, list_answers
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, simulate


class WorkflowIntakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        self.db_path = home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=home, slug="shop")
        self.repo = Path(self.project["repo_path"])
        self.agents = ScriptedAgents()
        self.enterContext(simulate(self.agents))

    def _approve_old_brief(self, *, agreement: bool) -> None:
        brief = self.repo / BRIEF_RELPATH
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text("# Product brief — shop\n\nSells socks.\n")
        git_commit_paths(self.repo, [brief], "factory: approved brief")
        if agreement:
            with db.get_db(self.db_path) as conn:
                add_answer(conn, self.project["id"], topic="execution", question="May it?",
                           options=["Yes", "Pause"], answer="Yes", assumed=False)

    async def _home(self, app, pilot) -> WorkflowScreen:
        await pilot.pause()
        screen = app.screen
        self.assertIsInstance(screen, WorkflowScreen)
        screen.query_one("#wf-project", Select).value = "shop"
        await pilot.pause()
        return screen

    async def _settle(self, screen: WorkflowScreen, pilot) -> None:
        for _ in range(200):
            await pilot.pause(0.02)
            if not screen._acting:
                return
        self.fail("the action never finished")

    def _first(self, screen) -> tuple[str, dict]:
        table = screen.query_one("#wf-list", DataTable)
        if not table.row_count:
            return "", {}
        return screen.items[str(table.coordinate_to_cell_key((0, 0)).row_key.value)]

    def _label(self, screen, button: str) -> str:
        return str(screen.query_one(f"#{button}", Button).label)

    async def test_interview_on_the_board_writes_the_brief(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 50)) as pilot:
            screen = await self._home(app, pilot)
            self.assertEqual(self._label(screen, "wf-intake"), "Interview")
            await pilot.click("#wf-intake")
            await self._settle(screen, pilot)
            screen.query_one("#wf-tabs").active = "inbox"
            await pilot.pause()
            for answered in range(30):
                kind, row = self._first(screen)
                if kind != "decision" or row["kind"] != "question":
                    break
                screen.query_one("#wf-list", DataTable).focus()
                await pilot.press("enter")
                # The first one is handed back ("You decide"); the rest take the recommendation.
                await pilot.press(*(("3", "enter") if answered == 0 else ("enter",)))
                await self._settle(screen, pilot)
            kind, row = self._first(screen)
            self.assertEqual((kind, row.get("kind")), ("decision", "brief"))
            text = str(screen.query_one("#wf-text", Static).render())
            self.assertIn("Technical choices", text)
            self.assertIn("React 19", text)
            self.assertEqual(self._label(screen, "wf-primary"), "Approve brief")
            await pilot.click("#wf-primary")
            await self._settle(screen, pilot)
            self.assertEqual(self._label(screen, "wf-intake"), "Amend brief…")
        self.assertTrue((self.repo / BRIEF_RELPATH).is_file())
        with db.get_db(self.db_path) as conn:
            answers = list_answers(conn, self.project["id"])
        self.assertTrue(answers[0]["assumed"])  # "You decide" is an assumption, said as one
        self.assertIn("execution", {a["topic"] for a in answers})

    async def test_a_failed_interview_call_is_shown_with_a_retry(self) -> None:
        self.agents.fail_next("product")
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 50)) as pilot:
            screen = await self._home(app, pilot)
            await pilot.click("#wf-intake")
            await self._settle(screen, pilot)
            screen.query_one("#wf-tabs").active = "inbox"
            await pilot.pause()
            kind, row = self._first(screen)
            self.assertEqual(kind, "job")
            table = screen.query_one("#wf-list", DataTable)
            listed = " ".join(map(str, table.get_row_at(0)))
            self.assertIn("Failed", listed)
            self.assertIn("Product interview", listed)
            self.assertIn("interview-agent call failed", str(screen.query_one("#wf-text", Static).render()))
            self.assertEqual(self._label(screen, "wf-primary"), "Retry")
            await pilot.click("#wf-primary")
            await self._settle(screen, pilot)
            kind, row = self._first(screen)
            self.assertEqual((kind, row.get("kind")), ("decision", "question"))

    async def test_an_old_brief_without_the_agreement_offers_to_complete_it(self) -> None:
        self._approve_old_brief(agreement=False)
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 50)) as pilot:
            screen = await self._home(app, pilot)
            self.assertEqual(self._label(screen, "wf-intake"), "Complete agreement")
            await pilot.click("#wf-intake")
            await self._settle(screen, pilot)
        self.assertEqual([mode for _a, mode in self.agents.calls], ["technical"])

    async def test_a_complete_brief_offers_amend_and_asks_what_changed_first(self) -> None:
        self._approve_old_brief(agreement=True)
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 50)) as pilot:
            screen = await self._home(app, pilot)
            self.assertEqual(self._label(screen, "wf-intake"), "Amend brief…")
            await pilot.click("#wf-intake")
            await pilot.pause()
            self.assertIsInstance(app.screen, PromptScreen)
            self.assertEqual(self.agents.calls, [])  # nothing paid before you say what changed
            app.screen.query_one(Input).value = "add gift wrapping"
            await pilot.press("enter")
            await self._settle(screen, pilot)
        self.assertIn("add gift wrapping", self.agents.prompts[0])
        self.assertIn("## Amendment", self.agents.prompts[0])

    async def test_propose_backlog_is_not_offered_twice(self) -> None:
        self._approve_old_brief(agreement=True)
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 50)) as pilot:
            screen = await self._home(app, pilot)
            self.assertFalse(screen.query_one("#wf-backlog", Button).disabled)
            await pilot.click("#wf-backlog")
            await self._settle(screen, pilot)
            button = screen.query_one("#wf-backlog", Button)
            self.assertTrue(button.disabled)
            self.assertIn("Needs you", str(button.tooltip))


if __name__ == "__main__":
    unittest.main()
