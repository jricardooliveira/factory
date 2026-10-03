"""The batch proposal (design handoff, overlay `b`): ready stories pre-ticked by a greedy
non-overlapping pick, refusals inline, live budget, Launch N. Launching only QUEUES the
builds — the simulated worker never runs them."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import Static

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.batch import BatchView
from factory.interfaces.board.board_app import BoardApp, BoardScreen
from factory.runs.dashboard import pause_project
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, simulate


class BatchTests(unittest.IsolatedAsyncioTestCase):
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
        self.enterContext(simulate(ScriptedAgents(stories=("Catalogue", "Basket", "Checkout", "Wishlist"))))
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        answer(self._pending()[0]["id"], "approve", db_path=self.db_path)
        for n in (2, 3, 4):
            start_refinement("shop", n, db_path=self.db_path)
        drain(self.db_path)
        for q in self._pending():
            answer(q["id"], "1", db_path=self.db_path)
        drain(self.db_path)  # #2 #3 #4 ready, #1 draft

    def _pending(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"]) if d["status"] == "pending"]

    def _builds(self) -> list[int]:
        with db.get_db(self.db_path) as conn:
            plans = {p.id: p.backlog_id for p in store.list_plans(conn, self.project["id"])}
            return sorted(plans[j["payload"]["plan_id"]] for j in store.list_jobs(conn, self.project["id"])
                          if j["kind"] == "build")

    async def _batch(self, pilot) -> tuple[BoardScreen, BatchView]:
        for _ in range(60):
            await pilot.pause(0.05)
            screen = pilot.app.screen
            if isinstance(screen, BoardScreen) and screen.model is not None:
                break
        await pilot.press("b")
        await pilot.pause(0.3)
        return screen, screen.query_one(BatchView)

    def _body(self, view) -> str:
        return "\n".join(str(w.render()) for w in view.query(Static))

    async def _settle(self, screen, pilot) -> None:
        for _ in range(100):
            await pilot.pause(0.03)
            if not screen.acting:
                await pilot.pause(0.2)
                return
        self.fail("the action never finished")

    async def test_b_opens_the_proposal_pre_ticked_with_the_budget(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._batch(pilot)
            self.assertEqual(screen.view, "batch")
            text = self._body(view)
            panel = view.query_one(".panel")
            self.assertEqual(panel.border_title, "Batch proposal")
            self.assertEqual(panel.border_subtitle, "3 ready")
        self.assertIn("Runs up to 2 stories at the same time. Picked so they don’t change the same files.", text)
        self.assertIn("[x] #2 Basket", text)
        self.assertIn("[x] #3 Checkout", text)
        self.assertIn("[ ] #4 Wishlist", text)
        self.assertIn("up to $10", text)
        self.assertIn("Not in this batch", text)
        self.assertIn("#1 Catalogue", text)
        self.assertIn("draft · not refined yet", text)
        self.assertIn("2 stories × $10 = up to $20", text)

    async def test_a_third_tick_is_refused_inline_and_unticking_frees_it(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._batch(pilot)
            await pilot.press("down", "down", "space")
            await pilot.pause()
            self.assertIn("! 2 run at a time. Untick one first.", self._body(view))
            await pilot.press("up", "space", "down", "space")
            await pilot.pause()
            text = self._body(view)
        self.assertIn("[ ] #3 Checkout", text)
        self.assertIn("[x] #4 Wishlist", text)

    async def test_enter_launches_the_ticked_stories(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._batch(pilot)
            await pilot.press("enter")
            await self._settle(screen, pilot)
            self.assertIn("Launched #2 and #3. Spend so far shows in Working now.",
                          screen.query_one("#feedback").plain)
            self.assertEqual(screen.view, "overview")
        self.assertEqual(self._builds(), [2, 3])

    async def test_launching_while_paused_is_refused(self) -> None:
        pause_project("shop", True, db_path=self.db_path)
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._batch(pilot)
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertIn("New starts are paused. Press P to resume, then launch.",
                          screen.query_one("#feedback").plain)
        self.assertEqual(self._builds(), [])


if __name__ == "__main__":
    unittest.main()
