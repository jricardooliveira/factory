"""Overview (design handoff 1a): what needs me, the ONE next start, what is working, since
I left. Simulated factory; a parked run is seeded as a real one leaves it."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual.widgets import OptionList, Static

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.board_app import BoardApp, BoardScreen
from factory.interfaces.board.overview import OverviewView
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, park_run, simulate


class OverviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.agents = ScriptedAgents(stories=("Catalogue", "Basket", "Checkout", "Wishlist"))
        self.enterContext(simulate(self.agents))

    def _project(self, slug: str = "shop", *, brief: bool = True) -> dict:
        project = create_project(self.db_path, home=self.home, slug=slug)
        if brief:
            repo = Path(project["repo_path"])
            path = repo / BRIEF_RELPATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# Product brief — {slug}\n\nSells socks.\n")
            git_commit_paths(repo, [path], "factory: approved brief")
        return project

    def _busy_shop(self) -> dict:
        project = self._project()
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        answer(self._pending(project)[0]["id"], "approve", db_path=self.db_path)
        for n in (2, 3):
            start_refinement("shop", n, db_path=self.db_path)
        drain(self.db_path)
        for q in self._pending(project):
            answer(q["id"], "1", db_path=self.db_path)
        drain(self.db_path)  # #2 and #3 ready
        start_refinement("shop", 1, db_path=self.db_path)
        drain(self.db_path)  # #1 waits on two questions
        park_run(self.db_path, project, title="Wishlist", stage="gate-2-architect", backlog_id=4,
                 logs={"spec-agent": {}, "architect-agent": {"architecture_notes": "n"}})
        return project

    def _pending(self, project: dict) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, project["id"]) if d["status"] == "pending"]

    async def _overview(self, pilot) -> tuple[BoardScreen, OverviewView]:
        for _ in range(60):
            await pilot.pause(0.05)
            screen = pilot.app.screen
            if isinstance(screen, BoardScreen) and screen.model is not None:
                break
        await pilot.pause(0.2)
        return screen, screen.query_one(OverviewView)

    def _panel(self, view, panel_id: str) -> str:
        return "\n".join(str(w.render()) for w in view.query_one(panel_id).query(Static))

    async def test_wide_overview_is_two_columns_of_the_designs_boxes(self) -> None:
        self._busy_shop()
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._overview(pilot)
            titles = [p.border_title for p in view.query(".panel")]
            left, right = view.query_one("#ov-left").region, view.query_one("#ov-right").region
            needs = view.query_one("#ov-needs-list", OptionList)
            rows = [str(needs.get_option_at_index(i).prompt) for i in range(needs.option_count)]
            nxt = self._panel(view, "#ov-next")
            working = self._panel(view, "#ov-working")
            stories = self._panel(view, "#ov-stories")
        self.assertEqual(titles[:3], ["Needs you · 3", "Next to start", "Stories"])
        self.assertEqual(titles[3], "Working now")
        self.assertTrue(titles[4].startswith("Since you left"))
        self.assertEqual(left.width, 80)
        self.assertEqual(left.y, right.y)
        self.assertTrue(rows[0].startswith("◆ Approve"))
        self.assertTrue(any(r.startswith("? Answer") and "Story #1 Catalogue" in r for r in rows))
        self.assertIn("▶ Propose a batch · 2 ready · #2 and #3 can run together", nxt)
        self.assertIn("● Story #4 Wishlist", working)
        self.assertIn("◆ Waiting for you · checkpoint 2 of 3 (design)", working)
        self.assertIn("2 ready", stories)
        self.assertIn("1 working", stories)

    async def test_enter_opens_the_decision_in_needs_you(self) -> None:
        self._busy_shop()
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._overview(pilot)
            view.query_one("#ov-needs-list", OptionList).focus()
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertEqual(screen.view, "needs")
            needs = screen.views["needs"]
            self.assertTrue(needs.open)
            self.assertEqual(needs.current().kind, "ckpt")

    async def test_welcome_back_counts_what_arrived_since_the_last_visit(self) -> None:
        self._busy_shop()
        then = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        (self.home / "board-state.json").write_text(json.dumps({"project": "shop",
                                                                 "seen": {"shop": then}}))
        app = BoardApp(self.db_path)
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._overview(pilot)
            feedback = screen.query_one("#feedback").plain
            since = view.query_one("#ov-since").border_title
        local = datetime.fromisoformat(then).astimezone().strftime("%H:%M")
        self.assertIn(f"Welcome back. 3 new decisions since {local}.", feedback)
        self.assertEqual(since, f"Since you left · {local}")

    async def test_a_new_project_starts_with_the_interview(self) -> None:
        self._project("tetris", brief=False)
        app = BoardApp(self.db_path, "tetris")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._overview(pilot)
            self.assertIn("▶ Start the interview", self._panel(view, "#ov-next"))
            self.assertIn("✓ Nothing needs you.", self._panel(view, "#ov-needs"))
            await pilot.press("i")
            for _ in range(60):
                await pilot.pause(0.05)
                if not screen.acting:
                    break
            await pilot.pause(0.3)
            self.assertIn("Interview started. Questions arrive in Needs you.",
                          screen.query_one("#feedback").plain)

    async def test_P_pauses_new_starts_and_says_so_everywhere(self) -> None:
        self._busy_shop()
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._overview(pilot)
            await pilot.press("P")
            for _ in range(60):
                await pilot.pause(0.05)
                if not screen.acting:
                    break
            await pilot.pause(0.4)
            self.assertIn("⏸ new starts paused", str(screen.query_one("#header").render()))
            self.assertIn("New starts are paused.", self._panel(view, "#ov-next"))

    async def test_narrow_overview_stacks_the_boxes(self) -> None:
        self._busy_shop()
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(90, 40)) as pilot:
            screen, view = await self._overview(pilot)
            needs, working = view.query_one("#ov-needs").region, view.query_one("#ov-working").region
            self.assertEqual(needs.x, working.x)
            self.assertLess(needs.y, working.y)


if __name__ == "__main__":
    unittest.main()
