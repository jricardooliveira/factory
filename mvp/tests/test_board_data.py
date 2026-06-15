"""Tests for the board data layer (TUI-free, fully offline)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import json

from factory.board_data import (
    find_run,
    load_board_runs,
    render_flow,
    run_pipeline_progress,
)
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


class TimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_timeline_is_chronological_with_run_bookends(self) -> None:
        from factory.board_data import render_timeline, run_timeline

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "req")
            rid = db.start_run(conn, "US-0001")
            db.log_agent(conn, rid, "spec-agent", "p", "{}", verdict="pass", cost_usd=0.01)
            db.log_gate(conn, rid, "gate-1-spec", True, "ok")
            db.finish_run(conn, rid, "completed")

        events = run_timeline(self.db_path, rid)
        kinds = [e.kind for e in events]
        labels = [e.label for e in events]
        self.assertEqual(kinds[0], "run")            # starts with run-started
        self.assertEqual(events[-1].label, "run completed")  # ends with finish
        self.assertIn("spec-agent", labels)
        self.assertIn("gate-1-spec", labels)
        # chronological
        self.assertEqual([e.when for e in events], sorted(e.when for e in events))
        # rendering doesn't crash and includes an icon
        self.assertIn("✓", render_timeline(events))


class KanbanColumnTests(unittest.TestCase):
    def _run(self, status: str, stage: str) -> "object":
        from factory.board_data import BoardRun

        return BoardRun(id=1, story_id="US", title="t", status=status, stage=stage,
                        cost=0.0, started_at="")

    def test_column_for_maps_status_and_stage(self) -> None:
        from factory.board_data import column_for

        self.assertEqual(column_for(self._run("waiting_human", "gate-2-human")), "Needs You")
        self.assertEqual(column_for(self._run("completed", "coder-agent")), "Done")
        self.assertEqual(column_for(self._run("blocked", "coder-agent")), "Blocked")
        self.assertEqual(column_for(self._run("failed", "gate-2-architect")), "Blocked")
        self.assertEqual(column_for(self._run("running", "spec-agent")), "Spec")
        self.assertEqual(column_for(self._run("running", "architect-agent")), "Architect")
        self.assertEqual(column_for(self._run("running", "gate-2-architect")), "Architect")
        self.assertEqual(column_for(self._run("running", "coder-agent")), "Coder")


class PipelineProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _stages(self, run_id: int) -> dict[str, str]:
        return {s.label: s.status for s in run_pipeline_progress(self.db_path, run_id)}

    def test_parked_at_gate2_shows_waiting_and_pending_tail(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "req")
            rid = db.start_run(conn, "US-0001")
            db.log_agent(conn, rid, "spec-agent", "p", "{}", verdict="pass")
            db.log_gate(conn, rid, "gate-1-spec", True, "ok")
            # architect passed WITH WARNINGS -> still counts as done in the flow
            db.log_agent(conn, rid, "architect-agent", "p", "{}", verdict="warn")
            db.log_gate(conn, rid, "gate-2-architect", True, "needs human", needs_human=True,
                        human_questions="Approve?")
            db.update_run_stage(conn, rid, "gate-2-human")
            db.finish_run(conn, rid, "waiting_human")

        s = self._stages(rid)
        self.assertEqual(s["Spec"], "done")
        self.assertEqual(s["Gate 1"], "done")
        self.assertEqual(s["Architect"], "done")
        self.assertEqual(s["Gate 2"], "waiting")
        self.assertEqual(s["Coder"], "pending")
        self.assertEqual(s["Build"], "pending")
        self.assertEqual(s["Release"], "pending")

    def test_coder_task_counts_and_build_fail(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0002", "S", "req")
            rid = db.start_run(conn, "US-0002")
            db.log_agent(conn, rid, "spec-agent", "p", "{}", verdict="pass")
            db.log_gate(conn, rid, "gate-1-spec", True, "ok")
            db.log_agent(conn, rid, "architect-agent", "p", "{}", verdict="pass")
            db.log_gate(conn, rid, "gate-2-architect", True, "ok")
            db.log_agent(conn, rid, "coder-agent", "p", "{}", verdict="complete", stage_type="T-0001")
            db.log_agent(conn, rid, "coder-agent", "p", "{}", verdict="fail", stage_type="T-0002")
            db.log_gate(conn, rid, "gate-build", False, "py_compile:fail")
            db.finish_run(conn, rid, "failed")

        stages = run_pipeline_progress(self.db_path, rid)
        by_label = {s.label: s for s in stages}
        self.assertEqual(by_label["Coder"].status, "failed")
        self.assertEqual(by_label["Coder"].detail, "1/2 tasks")
        self.assertEqual(by_label["Build"].status, "failed")

    def test_render_flow_has_icons_and_arrows(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0003", "S", "req")
            rid = db.start_run(conn, "US-0003")
            db.log_agent(conn, rid, "spec-agent", "p", "{}", verdict="pass")
            db.finish_run(conn, rid, "completed")  # not the current stage anymore
        flow = render_flow(run_pipeline_progress(self.db_path, rid))
        self.assertIn("✓", flow)   # spec done
        self.assertIn("○", flow)   # later stages pending
        self.assertIn("Spec", flow)
        self.assertIn("→", flow)   # arrows between stages


if __name__ == "__main__":
    unittest.main()
