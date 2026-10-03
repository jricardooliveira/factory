"""The board's frame (design handoff, "Screen frame"): header, lifecycle, tabs with badges,
feedback line, screen-specific footer, and the < 120-column rule. Simulated factory."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.board_app import BoardApp, BoardScreen
from factory.interfaces.board.chrome import FeedbackLine
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, simulate


class ChromeTests(unittest.IsolatedAsyncioTestCase):
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
        self.enterContext(simulate(ScriptedAgents(stories=("Catalogue", "Basket", "Checkout"))))
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        with db.get_db(self.db_path) as conn:
            [proposal] = [d for d in store.list_decisions(conn, self.project["id"])
                          if d["status"] == "pending"]
        answer(proposal["id"], "approve", db_path=self.db_path)
        start_refinement("shop", 1, db_path=self.db_path)
        drain(self.db_path)  # Story #1 waits on two questions

    async def _board(self, pilot) -> BoardScreen:
        for _ in range(50):
            await pilot.pause(0.05)
            screen = pilot.app.screen
            if isinstance(screen, BoardScreen) and screen.model is not None:
                return screen
        self.fail("the board never loaded")

    def _text(self, screen, selector: str) -> str:
        return str(screen.query_one(selector).render())

    async def test_header_lifecycle_and_tabs(self) -> None:
        app = BoardApp(self.db_path)
        before = datetime.now().strftime("%H:%M")
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            header = self._text(screen, "#header")
            life = self._text(screen, "#lifecycle")
            tabs = self._text(screen, "#tabs")
        after = datetime.now().strftime("%H:%M")
        self.assertIn("factory  ›  shop ▾", header)
        self.assertIn("○ worker idle", header)
        self.assertTrue(before in header or after in header, header)  # the local clock
        self.assertIn("Define ✓ ─ Plan ✓ ─ Build ● ─ Release", life)
        self.assertIn("p project", life)
        self.assertIn(" Overview ", tabs)
        self.assertIn(" Needs you  2 ", tabs)
        self.assertIn(" Stories  3 ", tabs)

    async def test_keys_switch_tabs_and_the_footer_follows(self) -> None:
        app = BoardApp(self.db_path)
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            self.assertEqual(screen.view, "overview")
            self.assertIn("esc  all runs", self._text(screen, "#keys"))
            await pilot.press("n")
            await pilot.pause()
            self.assertEqual(screen.view, "needs")
            self.assertIn("o  overview", self._text(screen, "#keys"))
            for key, view in (("t", "stories"), ("l", "activity"), ("o", "overview")):
                await pilot.press(key)
                await pilot.pause()
                self.assertEqual(screen.view, view)
            self.assertIn("?  help", self._text(screen, "#keys"))

    async def test_under_120_columns_the_board_is_narrow(self) -> None:
        app = BoardApp(self.db_path)
        async with app.run_test(size=(90, 40)) as pilot:
            screen = await self._board(pilot)
            self.assertTrue(screen.has_class("narrow"))
        app = BoardApp(self.db_path)
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            self.assertFalse(screen.has_class("narrow"))


class FeedbackLineTests(unittest.IsolatedAsyncioTestCase):
    async def test_done_clears_after_four_and_a_half_seconds_a_failure_stays(self) -> None:
        clock = [datetime(2026, 10, 3, 14, 0)]
        line = FeedbackLine(now=lambda: clock[0])
        line.say("Backlog approved.", "ok")
        self.assertEqual(line.plain, "✓ Backlog approved.")
        clock[0] += timedelta(seconds=5)
        line.tick()
        self.assertEqual(line.plain, "")
        line.say("Couldn't launch: Story #4 changed. b proposes again", "err")
        clock[0] += timedelta(minutes=5)
        line.tick()
        self.assertEqual(line.plain, "✗ Couldn't launch: Story #4 changed. b proposes again")


if __name__ == "__main__":
    unittest.main()
