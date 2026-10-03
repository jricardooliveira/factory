"""Home for someone returning after an absence: what needs me, what is working, what's next.

Live (2026-10-03): Overview repeated Needs you; "Needs input: 1" while four
decisions waited; Activity showed UTC next to a local clock; a story's plan was a
JSON dump; "Action saved." stayed on screen in warning yellow; at 90 columns the
question scrolled out of view above its options. Simulated factory, no model.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from textual.widgets import DataTable, Select, Static, Tab

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


class OverviewTests(unittest.IsolatedAsyncioTestCase):
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
        self.enterContext(simulate(ScriptedAgents(stories=("Catalogue", "Basket"))))
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        [proposal] = self._pending()
        answer(proposal["id"], "approve", db_path=self.db_path)
        start_refinement("shop", 1, db_path=self.db_path)  # Catalogue: two questions wait
        drain(self.db_path)

    def _pending(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"])
                    if d["status"] == "pending"]

    async def _home(self, app, pilot, tab: str = "overview") -> WorkflowScreen:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#wf-project", Select).value = "shop"
        await pilot.pause()
        screen.query_one("#wf-tabs").active = tab
        await pilot.pause()
        return screen

    async def _settle(self, screen: WorkflowScreen, pilot) -> None:
        for _ in range(100):
            await pilot.pause(0.02)
            if not screen._acting:
                return
        self.fail("the action never finished")

    def _rows(self, screen) -> list[tuple[str, str]]:
        table = screen.query_one("#wf-list", DataTable)
        return [(str(table.get_row_at(i)[1]), str(table.get_row_at(i)[2]))
                for i in range(table.row_count)]

    def _select(self, screen, state: str) -> None:
        table = screen.query_one("#wf-list", DataTable)
        table.move_cursor(row=[s for s, _ in self._rows(screen)].index(state))

    async def test_overview_puts_what_needs_you_first_then_the_one_next_thing(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._home(app, pilot)
            rows = self._rows(screen)
        states = [s for s, _ in rows]
        self.assertEqual(states[:2], ["Question", "Question"])
        self.assertEqual(states.count("Next"), 1)
        self.assertIn("Refine story #2 Basket", dict(rows)["Next"])

    async def test_the_next_row_does_the_next_thing_and_says_so_in_a_toast(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._home(app, pilot)
            self._select(screen, "Next")
            await pilot.pause()
            self.assertEqual(str(screen.query_one("#wf-primary").label), "Refine story")
            await pilot.click("#wf-primary")
            await self._settle(screen, pilot)
            note = str(screen.query_one("#wf-note", Static).render())
            toasts = [str(n.message) for n in app._notifications]
        self.assertEqual(len(self._pending()), 4)  # Basket's two questions joined Catalogue's
        self.assertEqual(note, "")  # success is a toast, not a sticky warning
        self.assertTrue(any("Basket" in t for t in toasts), toasts)

    async def test_tabs_and_summary_count_what_actually_waits(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._home(app, pilot)
            inbox = str(screen.query_one("#inbox", Tab).label)
            summary = str(screen.query_one("#wf-summary", Static).render())
        self.assertEqual(inbox, "Needs you (2)")
        self.assertIn("2 need you", summary)

    async def test_activity_shows_local_time(self) -> None:
        with db.get_db(self.db_path) as conn:
            newest = store.list_events(conn, self.project["id"])[-1]
        local = datetime.fromisoformat(newest["created_at"]).astimezone().strftime("%H:%M:%S")
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._home(app, pilot, "activity")
            rows = self._rows(screen)
        self.assertIn(local, [s for s, _ in rows])

    async def test_a_ready_story_reads_as_text_not_json(self) -> None:
        for question in self._pending():
            answer(question["id"], "1", db_path=self.db_path)
        drain(self.db_path)
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._home(app, pilot, "stories")
            self._select(screen, "Ready")
            await pilot.pause()
            text = str(screen.query_one("#wf-text", Static).render())
        for expected in ("Acceptance criteria", "It renders", "src/build_catalogue.ts", "Changes"):
            self.assertIn(expected, text)
        self.assertNotIn("{", text)

    async def test_the_question_stays_in_view_at_90_columns(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(90, 34)) as pilot:
            screen = await self._home(app, pilot, "inbox")
            screen.query_one("#wf-list", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            head = screen.query_one("#wf-head", Static)
            self.assertIn("Story question 1?", str(head.render()))
            visible = screen.region
            self.assertTrue(visible.contains_region(head.region), (head.region, visible))
            self.assertTrue(screen.query_one(AnswerPicker).display)
            primary = screen.query_one("#wf-primary")
            self.assertTrue(visible.contains_region(primary.region))

    async def test_activity_says_what_happened_in_words(self) -> None:
        [question, _] = self._pending()
        answer(question["id"], "2", db_path=self.db_path)
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._home(app, pilot, "activity")
            said = [what for _state, what in self._rows(screen)]
        self.assertIn("Refinement has questions for you", said)
        self.assertIn("Backlog proposal ready for your review", said)
        self.assertTrue(any(w.startswith("Answered: Story question 1?") for w in said), said)
        self.assertFalse(any(": needs_input" in w for w in said), said)

    async def test_home_does_not_offer_home(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test(size=(140, 45)) as pilot:
            screen = await self._home(app, pilot)
            shown = {b.binding.action for b in screen.active_bindings.values() if b.enabled}
            summary = str(screen.query_one("#wf-summary", Static).render())
        self.assertNotIn("project_overview", shown)
        self.assertNotIn("shop", summary)  # the header and the selector already name it


if __name__ == "__main__":
    unittest.main()
