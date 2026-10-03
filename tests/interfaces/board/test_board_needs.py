"""Needs you (design handoff 1a): the grouped inbox and every kind of detail, driven by keys.

Simulated factory: scripted agents, an in-process worker that never builds; a parked run
is seeded as a real one leaves it. Decisions on runs are only QUEUED here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import OptionList, Static, TextArea

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.board_app import BoardApp, BoardScreen
from factory.interfaces.board.needs import NeedsView
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, park_run, simulate


class NeedsYouTests(unittest.IsolatedAsyncioTestCase):
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
        self.agents = ScriptedAgents(stories=("Catalogue", "Basket", "Checkout"), story_questions=3)
        self.enterContext(simulate(self.agents))
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        answer(self._pending("backlog")[0]["id"], "approve", db_path=self.db_path)
        start_refinement("shop", 1, db_path=self.db_path)  # three questions
        drain(self.db_path)
        self.agents.fail_next("story")
        start_refinement("shop", 2, db_path=self.db_path)  # refinement fails
        drain(self.db_path)
        self.run_id = park_run(self.db_path, self.project, title="Checkout", stage="gate-2-architect",
                               backlog_id=3, questions="A new table: approve?",
                               logs={"spec-agent": {}, "architect-agent": {
                                   "architecture_notes": "One table for orders.",
                                   "modules_affected": ["orders.py"],
                                   "implementation_constraints": ["Totals are stored, not computed"]}})

    def _pending(self, kind: str) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"])
                    if d["kind"] == kind and d["status"] == "pending"]

    def _jobs(self, kind: str) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [j for j in store.list_jobs(conn, self.project["id"]) if j["kind"] == kind]

    async def _needs(self, pilot) -> tuple[BoardScreen, NeedsView]:
        for _ in range(60):
            await pilot.pause(0.05)
            screen = pilot.app.screen
            if isinstance(screen, BoardScreen) and screen.model is not None:
                break
        await pilot.press("n")
        await pilot.pause(0.2)
        return screen, screen.query_one(NeedsView)

    async def _settle(self, screen: BoardScreen, pilot) -> None:
        for _ in range(100):
            await pilot.pause(0.03)
            if not screen.acting:
                await pilot.pause(0.2)
                return
        self.fail("the action never finished")

    async def _select(self, view: NeedsView, pilot, kind: str) -> None:
        d = next(x for x in view.decisions if x.kind == kind)
        listing = view.query_one("#nd-list", OptionList)
        listing.focus()
        listing.highlighted = view.ids.index(d.id)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.2)

    def _text(self, view, selector: str) -> str:
        return str(view.query_one(selector, Static).render())

    def _feedback(self, screen) -> str:
        return screen.query_one("#feedback").plain

    async def test_the_inbox_is_grouped_and_one_storys_questions_are_one_row(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._needs(pilot)
            listing = view.query_one("#nd-list", OptionList)
            rows = [str(listing.get_option_at_index(i).prompt) for i in range(listing.option_count)]
            title = view.query_one("#nd-list-panel").border_title
        text = "\n".join(rows)
        for header in ("FAILED · 1", "APPROVE · 1", "ANSWER · 3"):
            self.assertIn(header, text)
        self.assertLess(text.index("FAILED"), text.index("APPROVE"))
        self.assertLess(text.index("APPROVE"), text.index("ANSWER"))
        self.assertEqual(sum("Story #1 Catalogue" in r for r in rows), 1)
        self.assertIn("3 questions", text)
        self.assertEqual(title, "Needs you · 5")

    async def test_answering_loads_the_next_question_in_place_then_returns(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._needs(pilot)
            await self._select(view, pilot, "questions")
            self.assertIn("●○○", self._text(view, "#nd-top").replace("[", ""))
            self.assertIn("Story question 1?", self._text(view, "#nd-top"))
            await pilot.press("enter")
            await self._settle(screen, pilot)
            self.assertIn("Answered. Question 2 of 3 is next.", self._feedback(screen))
            self.assertIn("Story question 2?", self._text(view, "#nd-top"))
            self.assertIs(app.focused, view.query_one("#nd-picker").query_one(OptionList))
            await pilot.press("3", "enter")  # You decide
            await self._settle(screen, pilot)
            self.assertIn("Left to the factory, recorded as an assumption.", self._feedback(screen))
            await pilot.press("enter")
            await self._settle(screen, pilot)
            self.assertIn("Story #1 Catalogue: all 3 answered. Refinement continues.",
                          self._feedback(screen))
            self.assertIs(app.focused, view.query_one("#nd-list", OptionList))
        self.assertEqual(self._pending("question"), [])

    async def test_skip_keeps_the_question_and_moves_on(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._needs(pilot)
            await self._select(view, pilot, "questions")
            before = view.selected
            await pilot.press("s")
            await pilot.pause(0.2)
            self.assertIn("Skipped for now. It stays in Needs you.", self._feedback(screen))
        self.assertEqual(len(self._pending("question")), 3)

    async def test_a_checkpoint_reads_as_its_design_then_approve_queues_it(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._needs(pilot)
            await self._select(view, pilot, "ckpt")
            body = self._text(view, "#nd-body")
            panel = view.query_one("#nd-detail")
            self.assertEqual((panel.border_title, panel.border_subtitle),
                             ("Story #3 Checkout", "checkpoint 2 of 3"))
            for expected in ("Design to approve", "One table for orders.", "New", "orders.py",
                             "Totals are stored, not computed", "A new table: approve?"):
                self.assertIn(expected, body)
            await pilot.press("a")
            await self._settle(screen, pilot)
            self.assertIn("Design approved. Story #3 Checkout continues.", self._feedback(screen))
        [job] = self._jobs("resume")
        self.assertEqual(job["payload"]["action"], "approve")

    async def test_reject_opens_a_text_box_and_sends_the_words(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._needs(pilot)
            await self._select(view, pilot, "ckpt")
            await pilot.press("r")
            await pilot.pause()
            self.assertIs(app.focused, view.query_one("#nd-text", TextArea))
            await pilot.press(*"compute totals")
            await pilot.press("escape")  # keeps the draft
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            self.assertEqual(view.query_one("#nd-text", TextArea).text, "compute totals")
            await pilot.click("#nd-send")
            await self._settle(screen, pilot)
            self.assertIn("Feedback sent. The design runs again.", self._feedback(screen))
        [job] = self._jobs("resume")
        self.assertEqual(job["payload"], {"action": "reject", "reason": "compute totals"})

    async def test_a_failure_offers_retry_with_a_note_and_dismiss(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._needs(pilot)
            await self._select(view, pilot, "fail")
            self.assertIn("✗ Failed", self._text(view, "#nd-top"))
            await pilot.press("m")
            await pilot.pause()
            await pilot.press(*"keep it small")
            await pilot.click("#nd-send")
            await self._settle(screen, pilot)
            self.assertIn("with your note", self._feedback(screen))
        self.assertIn("keep it small", self.agents.prompts[-1])

    async def test_backlog_update_approve_and_request_changes(self) -> None:
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(160, 50)) as pilot:
            screen, view = await self._needs(pilot)
            await self._select(view, pilot, "backlog")
            self.assertIn("Adds 3 stories (backlog 3 → 6).", self._text(view, "#nd-top"))
            await pilot.press("c")
            await pilot.pause()
            await pilot.press(*"drop checkout")
            await pilot.click("#nd-send")
            await self._settle(screen, pilot)
            self.assertIn("Changes sent. A new proposal is being written.", self._feedback(screen))
        self.assertIn("drop checkout", self.agents.prompts[-1])

    async def test_narrow_opens_the_detail_as_its_own_view_and_esc_returns(self) -> None:
        app = BoardApp(self.db_path, "shop")
        async with app.run_test(size=(90, 40)) as pilot:
            screen, view = await self._needs(pilot)
            self.assertFalse(view.query_one("#nd-detail").display)
            await self._select(view, pilot, "ckpt")
            self.assertTrue(view.query_one("#nd-detail").display)
            self.assertFalse(view.query_one("#nd-list-panel").display)
            chosen = view.selected
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(view.query_one("#nd-list-panel").display)
            self.assertEqual(view.selected, chosen)


if __name__ == "__main__":
    unittest.main()
