"""Per-run stage progress + timeline, derived from the stored record (fully offline)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import re

from factory.evidence.progress import plain_flow, run_pipeline_progress, run_timeline
from factory.interfaces.render.review import render_flow, render_timeline
from factory.state import db


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

    def test_plain_flow_is_the_markup_flow_without_its_tags(self) -> None:
        """The scenario matrix prints plain_flow where it used to strip render_flow's
        tags; the two must stay the same text."""
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0004", "S", "req")
            rid = db.start_run(conn, "US-0004")
            db.log_agent(conn, rid, "spec-agent", "p", "{}", verdict="pass")
            db.log_agent(conn, rid, "coder-agent", "p", "{}", verdict="complete", stage_type="T-1")
            db.log_gate(conn, rid, "gate-1-spec", False, "nope")
            db.finish_run(conn, rid, "failed")
        stages = run_pipeline_progress(self.db_path, rid)
        stripped = re.sub(r"\[/?[a-z0-9 #]*\]", "", render_flow(stages))
        self.assertEqual(plain_flow(stages), stripped)


class TimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_timeline_is_chronological_with_run_bookends(self) -> None:
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


    def test_boss_decisions_are_on_the_timeline(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "req")
            rid = db.start_run(conn, "US-0001")
            db.log_authorization(conn, rid, "architect-agent", True,
                                 granted_by="gate-1-spec passed")
            db.log_authorization(conn, rid, "coder-agent:T-2", False,
                                 missing=["T-1 to be implemented first"])

        boss = [e for e in run_timeline(self.db_path, rid) if e.kind == "boss"]
        self.assertEqual([e.label for e in boss],
                         ["authorize architect-agent", "authorize coder-agent:T-2"])
        self.assertEqual([e.status for e in boss], ["done", "failed"])
        self.assertEqual(boss[0].detail, "gate-1-spec passed")
        self.assertIn("T-1 to be implemented first", boss[1].detail)

    def test_multiline_gate_reason_and_error_keep_one_line_on_the_timeline(self) -> None:
        # gate-build puts the compiler output under its verdict line; one event, one line.
        why = "[T-1] py_compile:fail\npy_compile: SyntaxError: invalid syntax"
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "req")
            rid = db.start_run(conn, "US-0001")
            db.log_gate(conn, rid, "gate-build", False, why)
            db.finish_run(conn, rid, "failed", error="gate-build failed: " + why)

        events = run_timeline(self.db_path, rid)
        self.assertTrue(all("\n" not in e.detail for e in events), events)
        self.assertEqual(next(e for e in events if e.label == "gate-build").detail,
                         "[T-1] py_compile:fail")


if __name__ == "__main__":
    unittest.main()
