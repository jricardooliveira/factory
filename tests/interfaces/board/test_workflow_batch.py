"""Launching a batch: the proposal as stories to tick, not "edit the backlog IDs below".

Live (2026-10-03): a proposed batch was a JSON dump and a text box of comma-separated
backlog IDs. Simulated factory; the launch only QUEUES builds — the in-process worker
never runs them (tests/simulated.py drains refinement and backlog work only).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import Button, DataTable, Select, SelectionList, Static

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.tui import FactoryBoard
from factory.interfaces.board.workflow_screen import WorkflowScreen
from factory.runs.batches import propose_batch
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, simulate


class BatchLaunchTests(unittest.IsolatedAsyncioTestCase):
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
        [proposal] = self._pending()
        answer(proposal["id"], "approve", db_path=self.db_path)
        for story in (1, 2):
            start_refinement("shop", story, db_path=self.db_path)
        drain(self.db_path)
        for question in self._pending():
            answer(question["id"], "1", db_path=self.db_path)
        drain(self.db_path)
        self.proposal = propose_batch("shop", db_path=self.db_path)

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

    async def test_the_batch_reads_as_stories_to_tick(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            row = " ".join(map(str, screen.query_one("#wf-list", DataTable).get_row_at(0)))
            text = str(screen.query_one("#wf-text", Static).render())
            ticks = screen.query_one(SelectionList)
            shown, selected = ticks.display, sorted(ticks.selected)
            label = str(screen.query_one("#wf-primary", Button).label)
        self.assertIn("Batch · 2 stories", row)
        for expected in ("Budget $20.00", "2 at once", "release still needs you"):
            self.assertIn(expected, text)
        self.assertNotIn("{", text)
        self.assertTrue(shown)
        self.assertEqual(selected, [1, 2])
        self.assertEqual(label, "Launch 2 stories")

    async def test_untick_then_launch_queues_only_the_ticked_story(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            ticks = screen.query_one(SelectionList)
            ticks.deselect(2)
            await pilot.pause()
            self.assertEqual(str(screen.query_one("#wf-primary", Button).label), "Launch 1 story")
            await pilot.click("#wf-primary")
            await self._settle(screen, pilot)
        with db.get_db(self.db_path) as conn:
            builds = [j for j in store.list_jobs(conn, self.project["id"]) if j["kind"] == "build"]
            launched = store.get_proposal(conn, self.proposal["id"])
            plans = {p.id: p.backlog_id for p in store.list_plans(conn, self.project["id"])}
        self.assertEqual(launched["status"], "launched")
        self.assertEqual([plans[j["payload"]["plan_id"]] for j in builds], [1])
        self.assertEqual({j["status"] for j in builds}, {"queued"})  # never built here

    async def test_nothing_ticked_launches_nothing(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            screen.query_one(SelectionList).deselect_all()
            await pilot.pause()
            self.assertTrue(screen.query_one("#wf-primary", Button).disabled)

    async def test_a_waiting_batch_is_not_proposed_twice(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            button = screen.query_one("#wf-propose", Button)
            self.assertTrue(button.disabled)
            self.assertIn("Needs you", str(button.tooltip))
            self.assertEqual(str(screen.query_one("#wf-backlog", Button).label), "Re-propose backlog")

    async def test_a_ready_story_offers_refine_again_and_no_stop(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._open(app, pilot)
            screen.query_one("#wf-tabs").active = "stories"
            await pilot.pause()
            label = str(screen.query_one("#wf-primary", Button).label)
            stop_shown = screen.query_one("#wf-stop", Button).display
        self.assertEqual(label, "Refine again")
        self.assertFalse(stop_shown)


if __name__ == "__main__":
    unittest.main()
