"""The board's overlays and the rest (design handoff): project menu `p`, amend in two
steps, stop `S`, help `?`, restart `W`, Activity and All runs. Simulated factory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import Input, OptionList, Static, TextArea

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.board_app import BoardApp, BoardScreen
from factory.interfaces.board.modals import AmendModal, HelpModal, ProjectMenu, StopModal
from factory.runs.batches import launch_batch, propose_batch
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.state.interviews import add_answer
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, simulate


class OverlayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="shop")
        create_project(self.db_path, home=self.home, slug="tetris")
        repo = Path(self.project["repo_path"])
        brief = repo / BRIEF_RELPATH
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text("# Product brief — shop\n\nSells socks.\n")
        git_commit_paths(repo, [brief], "factory: approved brief")
        with db.get_db(self.db_path) as conn:
            add_answer(conn, self.project["id"], topic="execution", question="May it?",
                       options=["Yes"], answer="Yes", assumed=False)
        self.agents = ScriptedAgents(stories=("Catalogue", "Basket"))
        self.enterContext(simulate(self.agents))
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        answer(self._pending()[0]["id"], "approve", db_path=self.db_path)

    def _pending(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"]) if d["status"] == "pending"]

    async def _board(self, pilot) -> BoardScreen:
        for _ in range(60):
            await pilot.pause(0.05)
            screen = pilot.app.screen
            if isinstance(screen, BoardScreen) and screen.model is not None:
                return screen
        self.fail("the board never loaded")

    async def _settle(self, screen, pilot) -> None:
        for _ in range(100):
            await pilot.pause(0.03)
            if not screen.acting:
                await pilot.pause(0.3)
                return
        self.fail("the action never finished")

    def _items(self, app) -> list[str]:
        listing = app.screen.query_one(OptionList)
        return [str(listing.get_option_at_index(i).prompt) for i in range(listing.option_count)]

    async def test_p_opens_the_project_menu_with_what_applies(self) -> None:
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)  # a proposal waits: Propose backlog is disabled with its reason
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            await pilot.press("p")
            await pilot.pause()
            self.assertIsInstance(app.screen, ProjectMenu)
            items = self._items(app)
            self.assertEqual(items[0], "Switch to tetris")
            self.assertIn("Amend brief…", items)
            self.assertIn("Propose backlog · a proposal is waiting for you", items)
            self.assertIn("Pause new starts", items)
            self.assertIn("Settings…", items)
            app.screen.query_one(OptionList).highlighted = items.index(
                "Propose backlog · a proposal is waiting for you")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Propose backlog: a proposal is waiting for you.",
                          screen.query_one("#feedback").plain)

    async def test_switching_project_from_the_menu(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            await pilot.press("p")
            await pilot.pause()
            await pilot.press("enter")  # Switch to tetris
            for _ in range(40):
                await pilot.pause(0.05)
                if screen.project == "tetris" and screen.model.project["slug"] == "tetris":
                    break
            self.assertIn("tetris ▾", str(screen.query_one("#header").render()))

    async def test_amend_shows_its_cost_before_anything_paid_runs(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            await pilot.press("p")
            await pilot.pause()
            app.screen.query_one(OptionList).highlighted = self._items(app).index("Amend brief…")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, AmendModal)
            app.screen.query_one(TextArea).load_text("add gift wrapping")
            await pilot.click("#am-continue")
            await pilot.pause()
            step2 = "\n".join(str(w.render()) for w in app.screen.query(Static))
            self.assertIn("This drafts a revised brief", step2)
            self.assertEqual(self.agents.calls[-1][1], "backlog")  # nothing paid yet
            await pilot.press("enter")
            await self._settle(screen, pilot)
            self.assertIn("Drafting a revised brief. It arrives in Needs you.",
                          screen.query_one("#feedback").plain)
        self.assertIn("add gift wrapping", self.agents.prompts[-1])

    async def test_S_stops_a_running_story_at_a_safe_point(self) -> None:
        start_refinement("shop", 2, db_path=self.db_path)
        drain(self.db_path)
        for q in self._pending():
            answer(q["id"], "1", db_path=self.db_path)
        drain(self.db_path)
        proposal = propose_batch("shop", db_path=self.db_path, only=[2])
        launch_batch(proposal["id"], db_path=self.db_path, selected_ids=[2])  # queued build
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            await pilot.press("S")
            await pilot.pause()
            self.assertIsInstance(app.screen, StopModal)
            self.assertIn("Stop Story #2 at a safe point?", app.screen.query_one(".panel").border_title)
            await pilot.press("enter")
            await self._settle(screen, pilot)
            self.assertIn("Story #2 will stop after this step.", screen.query_one("#feedback").plain)
        with db.get_db(self.db_path) as conn:
            [build] = [j for j in store.list_jobs(conn, self.project["id"]) if j["kind"] == "build"]
        self.assertTrue(build["payload"]["stop_requested"])

    async def test_help_lists_the_keys(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            await self._board(pilot)
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, HelpModal)
            text = "\n".join(str(w.render()) for w in app.screen.query(Static))
            self.assertIn("o Overview", text)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, BoardScreen)

    async def test_a_stuck_worker_is_red_in_the_header_and_W_restarts_it(self) -> None:
        start_refinement("shop", 1, db_path=self.db_path)  # queued, no worker drains it
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            self.assertIn("■ worker stopped · 1 waiting", str(screen.query_one("#header").render()))
            await pilot.press("W")
            await self._settle(screen, pilot)
            self.assertIn("Worker restarted. Waiting steps are running.",
                          screen.query_one("#feedback").plain)

    async def test_activity_and_all_runs(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen = await self._board(pilot)
            await pilot.press("l")
            await pilot.pause()
            activity = "\n".join(str(w.render()) for w in screen.views["activity"].query(Static))
            self.assertIn("Today", activity)
            self.assertIn("Backlog proposal ready for your review", activity)
            await pilot.press("o", "escape")
            await pilot.pause()
            self.assertEqual(screen.view, "allruns")
            runs = "\n".join(str(w.render()) for w in screen.views["allruns"].query(Static))
            self.assertIn("Checkpoint approvals now live in Needs you. This view is history.", runs)


if __name__ == "__main__":
    unittest.main()
