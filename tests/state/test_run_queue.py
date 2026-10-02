"""The review queue as stored state: status querying, run cost, stale-run reconciliation.

`factory queue` / `factory reconcile` are thin over these `factory.state.db` accessors.
Notification sanitization lives in tests/adapters/test_notify.py, CLI argument parsing in
tests/interfaces/cli/test_common.py and spec rendering in tests/interfaces/test_render.py.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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

    def test_usage_rows_cover_a_run_or_every_live_run_of_a_story(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0009", "Cost", "req")
            rid = db.start_run(conn, "US-0009")
            db.log_agent(conn, rid, "spec-agent", "p", "o", cost_usd=0.01, tokens_in=10,
                         tokens_out=2, model_name="openai/x")
            second = db.start_run(conn, "US-0009")
            db.log_agent(conn, second, "architect-agent", "p", "o", cost_usd=0.02)
            replay = db.start_run(conn, "US-0009", replay_of=rid)
            db.log_agent(conn, replay, "spec-agent", "p", "o", cost_usd=0.01)
            self.assertEqual(len(db.usage_rows(conn, run_id=rid)), 1)
            story = db.usage_rows(conn, story_id="US-0009")
        self.assertEqual(len(story), 2)  # the replay spends nothing
        self.assertEqual(story[0]["model_name"], "openai/x")


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


if __name__ == "__main__":
    unittest.main()
