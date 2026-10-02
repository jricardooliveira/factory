"""Tests for the review queue: status querying + notification sanitization."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.adapters.notify import _clean
from factory.state import db


class RunsByStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            for sid, status in [
                ("US-0001", "completed"),
                ("US-0002", "waiting_human"),
                ("US-0003", "failed"),
                ("US-0004", "waiting_human"),
            ]:
                db.create_story(conn, sid, f"Story {sid}", "req")
                rid = db.start_run(conn, sid)
                db.finish_run(conn, rid, status)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_filters_to_requested_statuses_newest_first(self) -> None:
        with db.get_db(self.db_path) as conn:
            parked = db.get_runs_by_status(conn, ["waiting_human"])
        self.assertEqual([r["story_id"] for r in parked], ["US-0004", "US-0002"])
        self.assertIn("story_title", parked[0])  # joined story context

    def test_multiple_statuses(self) -> None:
        with db.get_db(self.db_path) as conn:
            rows = db.get_runs_by_status(conn, ["failed", "blocked"])
        self.assertEqual([r["story_id"] for r in rows], ["US-0003"])

    def test_run_cost_sums_agent_logs(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0009", "Cost", "req")
            rid = db.start_run(conn, "US-0009")
            db.log_agent(conn, rid, "spec-agent", "p", "o", cost_usd=0.01)
            db.log_agent(conn, rid, "architect-agent", "p", "o", cost_usd=0.02)
            cost = db.get_run_cost(conn, rid)
        self.assertAlmostEqual(cost, 0.03)


class NotifySanitizationTests(unittest.TestCase):
    def test_strips_applescript_injection_chars(self) -> None:
        dirty = 'Run #1 "; do shell script "rm -rf /" \\ \n done'
        cleaned = _clean(dirty)
        self.assertNotIn('"', cleaned)
        self.assertNotIn("\\", cleaned)
        self.assertNotIn("\n", cleaned)
        # Harmless content survives.
        self.assertIn("Run #1", cleaned)

    def test_truncates_long_messages(self) -> None:
        self.assertLessEqual(len(_clean("a" * 500)), 180)


class ReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stale_running_run_is_failed_fresh_one_kept(self) -> None:
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "old", "req")
            stale = db.start_run(conn, "US-0001")
            conn.execute("UPDATE pipeline_runs SET started_at = ? WHERE id = ?", (old, stale))
            db.create_story(conn, "US-0002", "fresh", "req")
            fresh = db.start_run(conn, "US-0002")  # started just now, still running

            reconciled = db.reconcile_stale_runs(conn, older_than_secs=3600)
            statuses = {
                r["id"]: r["status"]
                for r in conn.execute("SELECT id, status FROM pipeline_runs").fetchall()
            }
        self.assertEqual(reconciled, [stale])
        self.assertEqual(statuses[stale], "failed")
        self.assertEqual(statuses[fresh], "running")  # fresh run untouched


class CliRunIdValidationTests(unittest.TestCase):
    def test_non_integer_run_id_exits_cleanly(self) -> None:
        from factory.interfaces.cli.common import run_id_arg

        with self.assertRaises(SystemExit):
            run_id_arg(["abc"], "Usage: factory review <run_id>")

    def test_missing_run_id_exits_cleanly(self) -> None:
        from factory.interfaces.cli.common import run_id_arg

        with self.assertRaises(SystemExit):
            run_id_arg([], "Usage: factory review <run_id>")

    def test_valid_run_id_parsed(self) -> None:
        from factory.interfaces.cli.common import run_id_arg

        self.assertEqual(run_id_arg(["42"], "usage"), 42)


class SpecSummaryRobustnessTests(unittest.TestCase):
    """A blocked/off-script spec must never crash the CLI display layer."""

    def test_blocked_spec_does_not_raise(self) -> None:
        from factory.interfaces.render import print_spec_summary

        # The synthetic blocked dict omits required SpecOutput fields (problem/why).
        print_spec_summary(
            {
                "verdict": "blocked",
                "title": "",
                "acceptance_criteria": [],
                "tasks": [],
                "questions": ["Agent went off-script: token refresh failed: 401"],
            }
        )

    def test_valid_spec_still_renders(self) -> None:
        from factory.interfaces.render import print_spec_summary

        print_spec_summary(
            {
                "title": "T",
                "problem": "p",
                "why": "w",
                "acceptance_criteria": ["a", "b"],
                "tasks": [],
            }
        )


if __name__ == "__main__":
    unittest.main()
