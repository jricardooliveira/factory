"""Answering a question on the board: a pick list, like Claude Code's AskUserQuestion.

Live (2026-10-03): every refinement question carried options, and the board asked the
operator to TYPE "an option number, your answer, or “you decide”" into a text box.
Simulated factory (tests/simulated.py): scripted agents, jobs drained in-process.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import DataTable, Input, OptionList, Select, TextArea

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.answer import AnswerPicker
from factory.interfaces.board.tui import FactoryBoard
from factory.interfaces.board.workflow_screen import WorkflowScreen
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, simulate


class AnswerPickerTests(unittest.IsolatedAsyncioTestCase):
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
        self.enterContext(simulate(ScriptedAgents(stories=("Catalogue",), story_questions=2)))
        # One story waiting on two questions: what the operator found on the board.
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        [proposal] = self._decisions("backlog")
        answer(proposal["id"], "approve", db_path=self.db_path)
        start_refinement("shop", 1, db_path=self.db_path)
        drain(self.db_path)

    def _decisions(self, kind: str, status: str = "pending") -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"])
                    if d["kind"] == kind and d["status"] == status]

    async def _open(self, app, pilot) -> WorkflowScreen:
        await pilot.pause()
        screen = app.screen
        self.assertIsInstance(screen, WorkflowScreen)
        screen.query_one("#wf-project", Select).value = "shop"
        await pilot.pause()
        screen.query_one("#wf-tabs").active = "inbox"
        await pilot.pause()
        screen.query_one("#wf-list", DataTable).focus()
        await pilot.press("enter")  # into the selected question's answers
        await pilot.pause()
        return screen

    async def _settle(self, screen: WorkflowScreen, pilot) -> None:
        for _ in range(100):
            await pilot.pause(0.02)
            if not screen._acting:
                return
        self.fail("the answer never finished")

    def _prompts(self, screen) -> list[str]:
        options = screen.query_one(AnswerPicker).query_one(OptionList)
        return [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]

    async def test_a_question_with_options_is_a_pick_list_recommended_first(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            prompts = self._prompts(screen)
            self.assertIn("Simple screens", prompts[0])
            self.assertIn("recommended", prompts[0])
            self.assertIn("Rich screens", prompts[1])
            self.assertIn("You decide", prompts[2])
            self.assertIn("Other", prompts[3])
            self.assertFalse(screen.query_one(TextArea).display)  # no free-text box
            self.assertFalse(screen.query_one(AnswerPicker).query_one(Input).display)
            self.assertIs(app.focused, screen.query_one(AnswerPicker).query_one(OptionList))

    async def test_enter_answers_with_the_highlighted_option(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            await pilot.press("down", "enter")
            await self._settle(screen, pilot)
        [first] = [d for d in self._decisions("question", "answered")]
        self.assertEqual(first["answer"], "2")
        with db.get_db(self.db_path) as conn:
            [session] = store.list_sessions(conn, self.project["id"])
        self.assertEqual(session["draft"]["answers"][0]["answer"],
                         "Rich screens — More screens, more work")

    async def test_a_digit_picks_and_you_decide_is_an_assumption(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            await pilot.press("3", "enter")
            await self._settle(screen, pilot)
        with db.get_db(self.db_path) as conn:
            [session] = store.list_sessions(conn, self.project["id"])
        saved = session["draft"]["answers"][0]
        self.assertEqual(saved["answer"], "Simple screens — The smallest thing that settles screens")
        self.assertTrue(saved["assumed"])

    async def test_other_opens_a_text_field_and_enter_answers_with_it(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            await pilot.press("4", "enter")
            field = screen.query_one(AnswerPicker).query_one(Input)
            self.assertTrue(field.display)
            self.assertIs(app.focused, field)
            await pilot.press(*"only the basket", "enter")
            await self._settle(screen, pilot)
        [first] = self._decisions("question", "answered")
        self.assertEqual(first["answer"], "only the basket")

    async def test_the_highlighted_choice_survives_leaving_the_question(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            await pilot.press("2")
            await pilot.pause()
            screen.query_one("#wf-tabs").active = "stories"
            await pilot.pause()
            screen.query_one("#wf-tabs").active = "inbox"
            await pilot.pause()
            options = screen.query_one(AnswerPicker).query_one(OptionList)
            self.assertEqual(options.highlighted, 1)
        self.assertEqual(self._decisions("question")[0]["draft_text"], "2")


if __name__ == "__main__":
    unittest.main()
