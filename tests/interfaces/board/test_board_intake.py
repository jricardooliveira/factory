"""A new project from `i` to an approved brief, on the board (design handoff flow 2):
interview rounds arrive as one question group in Needs you, the brief arrives as an
approval, approving it writes BRIEF.md and the next start becomes Propose a backlog.
Simulated factory: scripted agents, an in-process worker."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import OptionList

from factory.evidence.brief import BRIEF_RELPATH
from factory.interfaces.board.board_app import BoardApp, BoardScreen
from factory.state import db
from factory.state.interviews import list_answers
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, simulate


class IntakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        self.db_path = home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=home, slug="tetris")
        self.enterContext(simulate(ScriptedAgents()))

    async def _settle(self, screen, pilot) -> None:
        for _ in range(150):
            await pilot.pause(0.03)
            if not screen.acting:
                await pilot.pause(0.3)
                return
        self.fail("the action never finished")

    async def test_from_i_to_an_approved_brief(self) -> None:
        app = BoardApp(self.db_path, "tetris")
        async with app.run_test(size=(160, 50)) as pilot:
            for _ in range(60):
                await pilot.pause(0.05)
                if isinstance(app.screen, BoardScreen) and app.screen.model is not None:
                    break
            screen = app.screen
            await pilot.press("i")
            await self._settle(screen, pilot)
            await pilot.press("n")
            needs = screen.views["needs"]
            for answered in range(40):
                await pilot.pause(0.2)
                group = next((d for d in needs.decisions if d.kind == "questions"), None)
                if group is None:
                    break
                if not needs.open or needs.current() is None or needs.current().id != group.id:
                    listing = needs.query_one("#nd-list", OptionList)
                    listing.focus()
                    listing.highlighted = needs.ids.index(group.id)
                    await pilot.pause()
                    await pilot.press("enter")
                    await pilot.pause(0.2)
                # The first one is handed back ("You decide"); the rest take the recommendation.
                await pilot.press(*(("3", "enter") if answered == 0 else ("enter",)))
                await self._settle(screen, pilot)
            brief = next(d for d in needs.decisions if d.kind == "brief")
            screen.open_decision(brief.id)
            await pilot.pause(0.3)
            self.assertIn("Technical choices", str(needs.query_one("#nd-body").render()))
            await pilot.press("a")
            await self._settle(screen, pilot)
            self.assertIn("Brief approved. Next: propose a backlog.", screen.query_one("#feedback").plain)
            for _ in range(40):
                await pilot.pause(0.1)
                if screen.model.next and screen.model.next.action == "backlog":
                    break
            self.assertEqual(screen.model.next.action, "backlog")
        self.assertTrue((Path(self.project["repo_path"]) / BRIEF_RELPATH).is_file())
        with db.get_db(self.db_path) as conn:
            answers = list_answers(conn, self.project["id"])
        self.assertTrue(answers[0]["assumed"])  # "You decide" is an assumption, said as one


if __name__ == "__main__":
    unittest.main()
