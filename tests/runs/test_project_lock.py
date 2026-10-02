"""One live run per product repository at a time (review task T10).

A project directory IS one git working tree, and the coder's per-task checkpoint
stages everything in it. Two live stories in the same repo would commit each
other's changes and pass each other's governance checks. Until stories get their
own worktrees, the second one is refused — before it spends a token.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import runs
from factory.runs import RunError
from factory.state import db
from factory.workspace.projects import create_project


def _blocked(*_a, **_k):
    raise RuntimeError("LIVE CALL BLOCKED")


class ProjectLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="shop")
        guard = patch("factory.pipeline.agent_calls.run_agent", _blocked)
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _running_run(self, project_id: str | None) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0009", "Busy", "x", project_id=project_id)
            return db.start_run(conn, "US-0009", project_id=project_id)  # status running

    def _start(self):
        return runs.run_pipeline("add a cart", opencode_cwd=self.project["repo_path"],
                                 db_path=self.db_path, project_id=self.project["id"])

    def test_a_second_live_run_on_the_same_project_is_refused(self) -> None:
        busy = self._running_run(self.project["id"])
        with self.assertRaises(RunError) as raised:
            self._start()
        self.assertIn(f"#{busy}", str(raised.exception))
        with db.get_db(self.db_path) as conn:
            self.assertEqual(len(db.list_runs(conn)), 1)  # nothing was started

    def test_a_run_on_another_project_does_not_block(self) -> None:
        self._running_run(None)
        outcome = self._start()  # gets as far as the (blocked) agent call
        self.assertIsNotNone(outcome.run_id)


if __name__ == "__main__":
    unittest.main()
