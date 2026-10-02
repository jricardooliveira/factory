"""Resuming from Checkpoint 1 — approve continues, reject re-specifies.

REVIEW_QUEUE.md's resume semantics: "Reject anywhere: increment attempt_number,
attach the operator's feedback as prior_findings, and re-enter at spec
(Checkpoint 1) or architecture (Checkpoint 2)... This makes the human a
first-class node in the graph rather than a blocking dead-end."

`resume_run` only ever knew about Checkpoint 2, so a Checkpoint-1 park had no way
back into the line. These tests pin the routing decision (which gate parked ->
which pipeline resumes) separately from the CLI's I/O, so it is testable offline
without a live opencode call.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory import pipeline as pl
from factory.state import db


class ResumeRoutingTests(unittest.TestCase):
    def test_checkpoint_1_approval_resumes_at_the_architect(self) -> None:
        """The story is accepted as written; the spec must NOT be re-run."""
        self.assertEqual(pl.resume_entry_for("gate-1-spec", "approve"), "architect")

    def test_checkpoint_1_rejection_re_specifies_the_story(self) -> None:
        """The operator answered the questions, so the story itself changes."""
        self.assertEqual(pl.resume_entry_for("gate-1-spec", "reject"), "spec")

    def test_checkpoint_2_keeps_its_existing_semantics(self) -> None:
        self.assertEqual(pl.resume_entry_for("gate-2-architect", "approve"), "coder")
        self.assertEqual(pl.resume_entry_for("gate-2-architect", "reject"), "architect")

    def test_unknown_gate_falls_back_to_checkpoint_2_behaviour(self) -> None:
        self.assertEqual(pl.resume_entry_for("gate-mystery", "approve"), "coder")
        self.assertEqual(pl.resume_entry_for(None, "reject"), "architect")


class SpecResumePipelineTests(unittest.TestCase):
    """A rejected story re-enters at the spec-agent carrying the answers, and then
    flows on through the normal line."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "f.db"
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_spec_resume_pipeline_reruns_spec_then_continues(self) -> None:
        clean_spec = json.dumps({
            "title": "Bookmark search", "problem": "p", "why": "w",
            "acceptance_criteria": ["search matches title", "results paginate"],
            "tasks": [{"id": "T-1", "title": "search", "purpose": "p",
                       "scope": [], "completion_evidence": "done"}],
            "verdict": "pass", "questions": [],
        })
        arch = json.dumps({"verdict": "pass", "architecture_notes": "n",
                           "modules_affected": ["search.py"]})
        coder = json.dumps({"verdict": "complete", "code_blocks": [
            {"path": "search.py", "content": "def s():\n    return []\n", "action": "create"}]})
        tester = json.dumps({"overall": "pass", "qa_verdict": "pass",
                             "ac_coverage": ["search matches title", "results paginate"],
                             "security_verdict": "pass", "highest_severity": "none",
                             "performance_verdict": "pass", "summary": "ok"})
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Pending", "add search")
            orig = db.start_run(conn, "US-0001")
            db.log_agent(conn, orig, "spec-agent", "in", clean_spec, verdict="pass")
            db.log_agent(conn, orig, "architect-agent", "in", arch, verdict="pass")
            db.log_agent(conn, orig, "coder-agent", "in", coder,
                         verdict="complete", stage_type="T-1")
            db.log_agent(conn, orig, "tester-agent", "in", tester, verdict="pass")
            new = db.start_run(conn, "US-0001")

        state = {
            "request": "add search", "story_id": "US-0001", "run_id": new,
            "db_path": str(self.db_path), "opencode_cwd": str(self.cwd),
            "replay_run_id": orig, "status": "running",
            "triggered_by": "spec-rejected",
            "prior_findings": ["Use title-only matching; no full-text index."],
        }
        final = dict(state)
        for event in pl.compile_spec_resume_pipeline().stream(state):
            for _node, out in event.items():
                final.update(out)

        self.assertEqual(final.get("status"), "waiting_human", final.get("error"))  # Checkpoint 3
        with db.get_db(self.db_path) as conn:
            gate_names = [g["gate_name"] for g in db.get_run_gates(conn, new)]
            spec_log = db.get_agent_log(conn, new, "spec-agent")
        # The whole line re-ran from the spec, not just the tail.
        self.assertIn("gate-1-spec", gate_names)
        self.assertIn("gate-2-architect", gate_names)
        self.assertIn("gate-test", gate_names)
        # And the operator's answers were actually in the spec-agent's prompt.
        self.assertIn("title-only matching", spec_log["input_text"])


if __name__ == "__main__":
    unittest.main()
