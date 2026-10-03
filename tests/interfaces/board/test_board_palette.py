"""ctrl+p: the operator's other verbs (doctor, evals, metrics, new project, the direct
story runs…), never in the footer; and the story interview's modal pick list."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from textual.widgets import Input

from factory.domain.interview import InterviewOption, InterviewQuestion
from factory.evidence.backlog import BACKLOG_RELPATH
from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.answer import AnswerPicker
from factory.interfaces.board.board_app import BoardScreen
from factory.interfaces.board.interview_screen import PromptScreen, QuestionScreen, ReviewScreen
from factory.interfaces.board.tui import FactoryBoard
from factory.state import db
from factory.workspace.projects import create_project

TUI = "factory.interfaces.board.tui"


class PaletteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="habits")

    async def _board(self, app, pilot) -> BoardScreen:
        for _ in range(60):
            await pilot.pause(0.05)
            if isinstance(app.screen, BoardScreen) and app.screen.model is not None:
                return app.screen
        self.fail("the board never loaded")

    def _command(self, app, title: str):
        return next(c for c in app.get_system_commands(app.screen) if c.title == title).callback

    async def _wait_for(self, app, pilot, kind):
        for _ in range(100):
            await pilot.pause(0.03)
            if isinstance(app.screen, kind):
                return app.screen
        self.fail(f"{kind.__name__} never opened")

    async def test_ctrl_p_lists_every_operator_verb_and_none_is_in_the_footer(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(app, pilot)
            titles = {c.title for c in app.get_system_commands(app.screen)}
            footer = str(screen.query_one("#keys").render())
        for title in ("Project status", "New project", "Brief & backlog: show",
                      "Story: run next from backlog", "Story: run a new request", "Run: retry",
                      "Run: replay", "Runs: reconcile stale", "Doctor (offline)",
                      "Doctor (probe models)", "Evals", "Simulate", "Metrics", "Model tiers"):
            self.assertIn(title, titles)
        for word in ("doctor", "evals", "metrics"):
            self.assertNotIn(word, footer.lower())

    async def test_project_status_opens_read_only(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(160, 50)) as pilot:
            await self._board(app, pilot)
            self._command(app, "Project status")()
            review = await self._wait_for(app, pilot, ReviewScreen)
            self.assertIn("Next: factory interview habits", review.text)

    async def test_new_project_asks_for_a_slug_creates_it_and_switches_to_it(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(app, pilot)
            self._command(app, "New project")()
            prompt = await self._wait_for(app, pilot, PromptScreen)
            prompt.query_one(Input).value = "tetris"
            await pilot.press("enter")
            for _ in range(60):
                await pilot.pause(0.05)
                if screen.model and screen.model.project and screen.model.project["slug"] == "tetris":
                    break
            self.assertEqual(screen.project, "tetris")
            self.assertIn("tetris created", screen.query_one("#feedback").plain)

    async def test_brief_and_backlog_show_read_only(self) -> None:
        repo = Path(self.project["repo_path"])
        (repo / BRIEF_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (repo / BRIEF_RELPATH).write_text("# Brief\nTrack habits\n")
        (repo / BACKLOG_RELPATH).write_text("# Backlog\n1. Log\n")
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(160, 50)) as pilot:
            await self._board(app, pilot)
            self._command(app, "Brief & backlog: show")()
            review = await self._wait_for(app, pilot, ReviewScreen)
            self.assertIn("Track habits", review.text)
            self.assertIn("1. Log", review.text)
            self.assertEqual(len(review.query("#approve")), 0)

    async def test_run_next_goes_through_the_backlog_service(self) -> None:
        outcome = SimpleNamespace(run_id=7, status="waiting_human", story_id="US-0001")
        row = {"id": 3, "request": "log a habit", "title": "Log"}
        app = FactoryBoard(self.db_path)
        with patch(f"{TUI}.next_story", return_value=row), patch(f"{TUI}.has_brief", return_value=False), \
                patch(f"{TUI}.run_backlog_story", return_value=outcome) as run:
            async with app.run_test(size=(160, 50)) as pilot:
                await self._board(app, pilot)
                self._command(app, "Story: run next from backlog")()
                review = await self._wait_for(app, pilot, ReviewScreen)
                self.assertIn("run #7 waiting_human", review.text)
        run.assert_called_once_with("habits", row, "log a habit", db_path=self.db_path)

    async def test_a_failing_command_is_said_in_red_not_a_crash(self) -> None:
        app = FactoryBoard(self.db_path)
        with patch(f"{TUI}.run_doctor", side_effect=RuntimeError("boom")):
            async with app.run_test(size=(160, 50)) as pilot:
                screen = await self._board(app, pilot)
                self._command(app, "Doctor (offline)")()
                for _ in range(60):
                    await pilot.pause(0.05)
                    if "boom" in screen.query_one("#feedback").plain:
                        break
                self.assertEqual(screen.query_one("#feedback").plain, "✗ Doctor: boom")


class StoryInterviewModalTests(unittest.IsolatedAsyncioTestCase):
    """The palette's direct story run asks in a modal: the same pick list as Needs you."""

    QUESTION = InterviewQuestion(topic="errors", question="What if payment fails?", options=[
        InterviewOption(label="Refuse", description="Show an error"),
        InterviewOption(label="Retry", description="Try once more")])

    async def _ask(self, *keys: str) -> list:
        results: list = []
        home = Path(tempfile.mkdtemp())
        db.init_db(home / "f.db")
        app = FactoryBoard(home / "f.db")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(QuestionScreen(self.QUESTION, []), results.append)
            await pilot.pause()
            self.assertTrue(app.screen.query(AnswerPicker))
            await pilot.press(*keys)
            await pilot.pause()
        return results

    async def test_a_digit_and_enter_answer_with_that_option(self) -> None:
        self.assertEqual(await self._ask("2", "enter"), ["2"])

    async def test_other_answers_in_your_own_words(self) -> None:
        self.assertEqual(await self._ask("4", "enter", *"call me", "enter"), ["call me"])

    async def test_escape_is_done_for_now(self) -> None:
        self.assertEqual(await self._ask("escape"), [None])


if __name__ == "__main__":
    unittest.main()
