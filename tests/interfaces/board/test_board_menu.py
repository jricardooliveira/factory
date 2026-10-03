"""The board's menu (`m` / ctrl+p): every major `factory` verb, run in place.

Each command calls the same `runs` / `preflight` / `selftest` function as its CLI
verb; these tests patch it where the board looks it up — no model call, ever.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from factory.interfaces.board.interview_screen import PromptScreen, ReviewScreen
from factory.interfaces.board.tui import FactoryBoard
from factory.state import db

TUI = "factory.interfaces.board.tui"


class BoardMenuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _command(self, app: FactoryBoard, title: str):
        commands = {c.title: c for c in app.get_system_commands(app.screen)}
        self.assertIn(title, commands, sorted(commands))
        return commands[title].callback

    async def _settle(self, app, pilot) -> None:
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def test_the_menu_lists_every_major_command(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test():
            titles = {c.title for c in app.get_system_commands(app.screen)}
        for title in ("Project status", "New project", "Interview: product brief", "Interview: amend brief",
                      "Brief & backlog: show", "Backlog: propose", "Story: run next from backlog",
                      "Story: run a new request", "Run: retry selected", "Run: replay selected",
                      "Runs: reconcile stale", "Doctor (offline)", "Doctor (probe models)",
                      "Evals", "Simulate", "Metrics", "Model tiers"):
            self.assertIn(title, titles)

    async def test_m_opens_the_menu(self) -> None:
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.press("m")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommandPalette")

    async def test_s_shows_where_every_project_stands(self) -> None:
        from factory.workspace.projects import create_project

        create_project(self.db_path, home=Path(self._tmp.name), slug="habits")
        app = FactoryBoard(self.db_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            await self._settle(app, pilot)
            self.assertIsInstance(app.screen, ReviewScreen)
            self.assertIn("Next: factory interview habits", app.screen.text)

    async def test_new_project_asks_for_a_slug_and_creates_it(self) -> None:
        app = FactoryBoard(self.db_path)
        with patch(f"{TUI}.create_project", return_value={"id": "PROJ-001",
                                                          "slug": "habits"}) as create:
            async with app.run_test() as pilot:
                self._command(app, "New project")()
                await pilot.pause()
                self.assertIsInstance(app.screen, PromptScreen)
                app.screen.dismiss("habits")
                await self._settle(app, pilot)
                self.assertEqual(app._project_filter, "habits")
        create.assert_called_once_with(self.db_path, slug="habits")

    async def test_doctor_shows_its_report(self) -> None:
        report = SimpleNamespace(passed=True, checks=[
            SimpleNamespace(name="claude", status="ok", detail="/bin/claude")])
        app = FactoryBoard(self.db_path)
        with patch(f"{TUI}.run_doctor", return_value=report) as doctor:
            async with app.run_test() as pilot:
                self._command(app, "Doctor (offline)")()
                await self._settle(app, pilot)
                self.assertIsInstance(app.screen, ReviewScreen)
                self.assertIn("claude", app.screen.text)
                self.assertIn("/bin/claude", app.screen.text)
        doctor.assert_called_once_with(offline=True)

    async def test_a_new_story_runs_the_project_pipeline_with_the_typed_request(self) -> None:
        app = FactoryBoard(self.db_path)
        app._project_filter = "habits"
        outcome = SimpleNamespace(run_id=7, status="waiting_human", story_id="US-0001")
        with patch(f"{TUI}.has_brief", return_value=False), \
                patch(f"{TUI}.run_project_pipeline", return_value=outcome) as run:
            async with app.run_test() as pilot:
                self._command(app, "Story: run a new request")()
                await pilot.pause()
                app.screen.dismiss("add a habit")
                await self._settle(app, pilot)
        run.assert_called_once_with("habits", "add a habit", db_path=self.db_path)

    async def test_next_story_starts_the_backlog_row_through_the_backlog_service(self) -> None:
        """`run_backlog_story` marks the row started when the run exists (not after
        it returns) and starts from the row's base_commit."""
        app = FactoryBoard(self.db_path)
        app._project_filter = "habits"
        outcome = SimpleNamespace(run_id=7, status="waiting_human", story_id="US-0001")
        row = {"id": 3, "request": "log a habit", "title": "Log"}
        with patch(f"{TUI}.next_story", return_value=row), \
                patch(f"{TUI}.has_brief", return_value=False), \
                patch(f"{TUI}.run_backlog_story", return_value=outcome) as run:
            async with app.run_test() as pilot:
                self._command(app, "Story: run next from backlog")()
                await self._settle(app, pilot)
        run.assert_called_once_with("habits", row, "log a habit", db_path=self.db_path)

    async def test_a_failing_command_is_shown_not_a_crash(self) -> None:
        app = FactoryBoard(self.db_path)
        with patch(f"{TUI}.run_doctor", side_effect=RuntimeError("boom")):
            async with app.run_test() as pilot:
                notify = MagicMock()
                app.notify = notify
                self._command(app, "Doctor (offline)")()
                await self._settle(app, pilot)
        self.assertIn("boom", str(notify.call_args))


if __name__ == "__main__":
    unittest.main()
