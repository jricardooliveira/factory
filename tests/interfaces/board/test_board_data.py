"""Tests for the board data layer (TUI-free, fully offline)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.interfaces.board.data import find_run, load_board_runs
from factory.state import db


class BoardDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, sid: str, status: str, *, error: str | None = None) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, sid, f"Story {sid}", "req")
            rid = db.start_run(conn, sid)
            db.finish_run(conn, rid, status, error=error)
        return rid

    def test_excludes_completed_and_groups_parked_first(self) -> None:
        self._run("US-0001", "completed")
        self._run("US-0002", "failed", error="boom")
        parked = self._run("US-0003", "waiting_human")

        runs = load_board_runs(self.db_path)
        ids = [r.story_id for r in runs]
        self.assertNotIn("US-0001", ids)  # completed runs are not on the board
        self.assertEqual(runs[0].id, parked)  # parked listed first
        self.assertTrue(runs[0].needs_input)

    def test_parked_run_carries_questions(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0009", "Auth", "req")
            rid = db.start_run(conn, "US-0009")
            db.log_gate(
                conn, rid, "gate-2-architect", True, "needs human",
                needs_human=True, human_questions="Approve breaking change?\n\nConfirm Stripe keys?",
            )
            db.finish_run(conn, rid, "waiting_human")

        run = find_run(self.db_path, rid)
        self.assertIsNotNone(run)
        assert run is not None
        self.assertTrue(run.needs_input)
        self.assertEqual(run.questions, ["Approve breaking change?", "Confirm Stripe keys?"])
        self.assertEqual(run.state_label, "NEEDS YOU")

    def test_failed_run_carries_error(self) -> None:
        rid = self._run("US-0007", "blocked", error="gate-build failed")
        run = find_run(self.db_path, rid)
        assert run is not None
        self.assertFalse(run.needs_input)
        self.assertEqual(run.error, "gate-build failed")


class KanbanColumnTests(unittest.TestCase):
    def _run(self, status: str, stage: str) -> "object":
        from factory.interfaces.board.data import BoardRun

        return BoardRun(id=1, story_id="US", title="t", status=status, stage=stage,
                        cost=0.0, started_at="")

    def test_column_for_maps_status_and_stage(self) -> None:
        from factory.interfaces.board.data import column_for

        self.assertEqual(column_for(self._run("waiting_human", "gate-2-human")), "Needs You")
        self.assertEqual(column_for(self._run("completed", "coder-agent")), "Done")
        self.assertEqual(column_for(self._run("blocked", "coder-agent")), "Blocked")
        self.assertEqual(column_for(self._run("failed", "gate-2-architect")), "Blocked")
        self.assertEqual(column_for(self._run("running", "spec-agent")), "Spec")
        self.assertEqual(column_for(self._run("running", "architect-agent")), "Architect")
        self.assertEqual(column_for(self._run("running", "gate-2-architect")), "Architect")
        self.assertEqual(column_for(self._run("running", "coder-agent")), "Coder")


if __name__ == "__main__":
    unittest.main()
