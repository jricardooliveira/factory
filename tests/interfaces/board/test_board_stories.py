"""Stories (design handoff 1a): grouped list, detail per state, and the board view (`v`).
Simulated factory; a parked run is seeded as a real one leaves it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import OptionList, Static

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.board_app import BoardApp, BoardScreen
from factory.interfaces.board.stories import StoriesView
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, park_run, simulate


class StoriesTests(unittest.IsolatedAsyncioTestCase):
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
        self.agents = ScriptedAgents(stories=("Catalogue", "Basket", "Checkout", "Wishlist", "Gifts"))
        self.enterContext(simulate(self.agents))
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        answer(self._pending()[0]["id"], "approve", db_path=self.db_path)
        start_refinement("shop", 2, db_path=self.db_path)
        drain(self.db_path)
        for q in self._pending():
            answer(q["id"], "1", db_path=self.db_path)
        drain(self.db_path)  # #2 ready
        start_refinement("shop", 1, db_path=self.db_path)
        drain(self.db_path)  # #1 needs answers
        self.agents.fail_next("story")
        start_refinement("shop", 3, db_path=self.db_path)
        drain(self.db_path)  # #3 refinement failed
        park_run(self.db_path, self.project, title="Wishlist", stage="gate-2-architect", backlog_id=4,
                 logs={"spec-agent": {}, "architect-agent": {"architecture_notes": "n"}})  # #4

    def _pending(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"]) if d["status"] == "pending"]

    async def _stories(self, pilot) -> tuple[BoardScreen, StoriesView]:
        for _ in range(60):
            await pilot.pause(0.05)
            screen = pilot.app.screen
            if isinstance(screen, BoardScreen) and screen.model is not None:
                break
        await pilot.press("t")
        await pilot.pause(0.2)
        return screen, screen.query_one(StoriesView)

    async def _open(self, view: StoriesView, pilot, n: int) -> None:
        listing = view.query_one("#st-list", OptionList)
        listing.focus()
        listing.highlighted = view.ids.index(n)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.2)

    async def _settle(self, screen, pilot) -> None:
        for _ in range(100):
            await pilot.pause(0.03)
            if not screen.acting:
                await pilot.pause(0.2)
                return
        self.fail("the action never finished")

    def _text(self, view, selector: str) -> str:
        return str(view.query_one(selector, Static).render())

    async def test_the_list_is_grouped_in_the_designs_order_with_meta(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._stories(pilot)
            listing = view.query_one("#st-list", OptionList)
            rows = [str(listing.get_option_at_index(i).prompt) for i in range(listing.option_count)]
            title = view.query_one("#st-list-panel").border_title
        text = "\n".join(rows)
        order = [text.index(h) for h in ("NEEDS ANSWERS · 1", "NOT READY · 1", "READY · 1",
                                         "WORKING · 1", "DRAFTS · 1")]
        self.assertEqual(order, sorted(order))
        self.assertTrue(any("#1 Catalogue" in r and "2 questions" in r for r in rows))
        self.assertTrue(any("#3 Checkout" in r and "refinement failed" in r for r in rows))
        self.assertEqual(title, "Stories · 5")

    async def test_a_ready_story_shows_what_it_changes_and_offers_a_batch(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._stories(pilot)
            await self._open(view, pilot, 2)
            top, body = self._text(view, "#st-top"), self._text(view, "#st-body")
            buttons = [str(b.label) for b in view.query("#st-actions Button")]
        self.assertIn("○ Ready", top)
        self.assertIn("not started", top)
        for expected in ("Request", "Build basket.", "Requirements", "It renders",
                         "What it changes", "src/build_basket.ts"):
            self.assertIn(expected, body)
        self.assertEqual(buttons, ["Propose batch"])

    async def test_a_failed_refinement_says_why_and_r_retries(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._stories(pilot)
            await self._open(view, pilot, 3)
            self.assertIn("! Not ready", self._text(view, "#st-top"))
            self.assertIn("Why it isn’t ready", self._text(view, "#st-body"))
            self.assertIn("✗ Refinement failed", self._text(view, "#st-body"))
            await pilot.press("r")
            await self._settle(screen, pilot)
            self.assertIn("Retrying refinement for Story #3 Checkout", screen.query_one("#feedback").plain)

    async def test_a_draft_is_refined_with_r(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._stories(pilot)
            await self._open(view, pilot, 5)
            await pilot.press("r")
            await self._settle(screen, pilot)
            self.assertIn("Refining Story #5 Gifts", screen.query_one("#feedback").plain)
        with db.get_db(self.db_path) as conn:
            self.assertTrue(any(s["backlog_id"] == 5 for s in store.list_sessions(conn, self.project["id"])))

    async def test_enter_on_a_story_waiting_for_you_opens_its_decision(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._stories(pilot)
            await self._open(view, pilot, 1)
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertEqual(screen.view, "needs")
            self.assertEqual(screen.views["needs"].current().story, 1)

    async def test_v_shows_the_board_and_enter_opens_a_card(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._stories(pilot)
            await pilot.press("v")
            await pilot.pause(0.2)
            board = str(view.query_one("#st-board").render())
            for column in ("TO REFINE", "READY", "SPEC", "DESIGN", "CODE", "TEST", "DONE"):
                self.assertIn(column, board)
            self.assertIn("? answers", board)
            self.assertIn("◆ you", board)
            await pilot.press("right")  # READY
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertEqual(view.selected, 2)
            self.assertTrue(view.open)


if __name__ == "__main__":
    unittest.main()
