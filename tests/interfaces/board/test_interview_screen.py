"""The intake interview and the brief/backlog view, driven from the board.

Offline: `factory.runs.interview.run_agent` is patched with frozen JSON and the
engine is pinned to opencode, so no test can reach a model.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Button, Input

from factory.adapters.opencode import AgentResult
from factory.domain.interview import REQUIRED_TOPICS
from factory.evidence.backlog import BACKLOG_RELPATH
from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.interview_screen import QuestionScreen, ReviewScreen
from factory.interfaces.board.tui import FactoryBoard
from factory.state import db
from factory.state.interviews import list_answers
from factory.workspace.projects import create_project


def _result(payload: dict, returncode: int = 0) -> AgentResult:
    return AgentResult(agent="interview-agent", output=json.dumps(payload),
                       duration_secs=0.1, returncode=returncode)


DONE = _result({"questions": [], "done": True})
GOAL_WITH_OPTIONS = _result({"questions": [{
    "topic": "goal", "question": "What is it for?",
    "options": [{"label": "Sell shoes"}, {"label": "Sell hats"}],
}]})
SPEC = _result({"name": "Shop", "description": "d", "language": "Python 3.12",
                "framework": "FastAPI", "database": "SQLite"})


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

    async def _type(self, app, pilot, text: str) -> None:
        box = app.screen.query_one(Input)
        box.focus()
        box.value = text
        await pilot.press("enter")
        await pilot.pause()

    async def test_without_a_project_selected_it_only_shows_a_notice(self) -> None:
        agent = self._agent()
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            self.assertIn("project", self._notes(app).lower())
            self.assertNotIsInstance(app.screen, QuestionScreen)
        agent.assert_not_called()

    async def test_a_project_without_runs_can_be_selected(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_cycle_project()
            self.assertEqual(app._project_filter, "shop")

    async def test_interview_on_the_board_writes_the_brief(self) -> None:
        self._agent(GOAL_WITH_OPTIONS, DONE, DONE, SPEC)
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_cycle_project()
            await pilot.press("i")
            # 1) the agent's question: pick the first option by its button
            screen = await self._wait_for(app, pilot, QuestionScreen)
            self.assertIn("What is it for?", screen.text)
            await pilot.click("#opt-1")
            # 2) the 8 remaining required topics, asked by Python: delegate one
            await self._wait_for(app, pilot, QuestionScreen)
            await pilot.click("#decide")
            for _ in REQUIRED_TOPICS[2:]:
                await self._wait_for(app, pilot, QuestionScreen)
                await self._type(app, pilot, "an answer")
            # 3) the brief: approve; 4) the stack proposal: keep the current spec
            review = await self._wait_for(app, pilot, ReviewScreen)
            self.assertIn("Sell shoes", review.text)
            await pilot.click("#approve")
            review = await self._wait_for(app, pilot, ReviewScreen)
            self.assertIn("FastAPI", review.text)
            await pilot.click("#stop")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn("approved", self._notes(app))
        self.assertTrue((self.repo / BRIEF_RELPATH).is_file())
        with db.get_db(self.db_path) as conn:
            answers = list_answers(conn, self.project["id"])
        self.assertEqual(answers[0]["answer"], "Sell shoes")
        self.assertTrue(answers[1]["assumed"])

    async def test_a_failed_agent_call_is_a_notification_not_a_crash(self) -> None:
        self._agent(_result({}, returncode=1))
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_cycle_project()
            await pilot.press("i")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn("interview-agent call failed", self._notes(app))
            self.assertIs(app.screen, app.screen_stack[0])

    async def test_b_shows_the_brief_and_backlog_read_only(self) -> None:
        (self.repo / BRIEF_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / BRIEF_RELPATH).write_text("# Brief\nSells shoes\n", encoding="utf-8")
        (self.repo / BACKLOG_RELPATH).write_text("# Backlog\n1. Cart\n", encoding="utf-8")
        app = FactoryBoard(self.db_path)
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

    async def test_an_approved_brief_is_not_reopened(self) -> None:
        (self.repo / BRIEF_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / BRIEF_RELPATH).write_text("# Brief\n", encoding="utf-8")
        agent = self._agent()
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_cycle_project()
            await pilot.press("i")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn("--amend", self._notes(app))
        agent.assert_not_called()


    async def test_board_keys_do_nothing_under_an_interview_modal(self) -> None:
        # A button has focus, not the Input: "a" must not approve the run selected
        # behind the modal (e.g. a release checkpoint), nor "q" drop the interview.
        app = FactoryBoard(self.db_path)
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
