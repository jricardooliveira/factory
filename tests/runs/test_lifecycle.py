"""Run curation has ONE policy, whichever surface asks.

Found by review: the TUI refused to dismiss a run awaiting a decision, while
`factory dismiss` archived anything — a parked or even a running run — so the
CLI and the board applied different governance to the same run. Both now call
`runs.dismiss_run`.
"""

from __future__ import annotations

import unittest

from factory.state import db
from factory.workspace import layout


class DismissPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        # The autouse fixture points $FACTORY_HOME at a throwaway home, so the CLI
        # and these calls share one DB.
        self.db_path = layout.db_path()
        db.init_db(self.db_path)

    def _run(self, status: str) -> int:
        with db.get_db(self.db_path) as conn:
            sid = db.next_story_id(conn)
            db.create_story(conn, sid, "S", "req")
            rid = db.start_run(conn, sid)
            if status != "running":
                db.finish_run(conn, rid, status)
        return rid

    def _status(self, rid: int) -> str:
        with db.get_db(self.db_path) as conn:
            return db.get_run(conn, rid)["status"]

    def test_a_run_awaiting_a_decision_cannot_be_dismissed(self) -> None:
        from factory import runs

        rid = self._run("waiting_human")
        with self.assertRaises(runs.RunError) as raised:
            runs.dismiss_run(rid, db_path=self.db_path)
        self.assertIn("approve or reject", str(raised.exception))
        self.assertEqual(self._status(rid), "waiting_human")

    def test_a_running_run_cannot_be_dismissed(self) -> None:
        from factory import runs

        rid = self._run("running")
        with self.assertRaises(runs.RunError) as raised:
            runs.dismiss_run(rid, db_path=self.db_path)
        self.assertIn("still running", str(raised.exception))
        self.assertEqual(self._status(rid), "running")

    def test_a_finished_run_is_archived(self) -> None:
        from factory import runs

        for status in ("failed", "blocked", "completed"):
            with self.subTest(status=status):
                rid = self._run(status)
                runs.dismiss_run(rid, db_path=self.db_path)
                self.assertEqual(self._status(rid), "archived")

    def test_an_unknown_run_is_refused(self) -> None:
        from factory import runs

        with self.assertRaises(runs.RunError) as raised:
            runs.dismiss_run(42, db_path=self.db_path)
        self.assertEqual(str(raised.exception), "No run found with id #42")

    def test_the_cli_applies_the_same_policy(self) -> None:
        from unittest.mock import patch

        from factory.interfaces.cli.main import main

        rid = self._run("waiting_human")
        with patch("sys.argv", ["factory", "dismiss", str(rid)]):
            with self.assertRaises(SystemExit) as exited:
                main()
        self.assertNotEqual(exited.exception.code, 0)
        self.assertEqual(self._status(rid), "waiting_human")


if __name__ == "__main__":
    unittest.main()
