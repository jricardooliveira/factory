"""The coder can bounce an infeasible design back to the architect (bounded)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.graph import END

from factory import pipeline
from factory.gates import MAX_REARCHITECT_LOOPS
from factory.opencode_client import AgentResult
from factory.state import db


def _ar(output: str) -> AgentResult:
    return AgentResult(agent="coder-agent", output=output, duration_secs=0.0, returncode=0)


class ReviewerFeedbackBlockTests(unittest.TestCase):
    def test_design_infeasible_renders_implementer_feedback(self) -> None:
        block = pipeline._reviewer_feedback_block(
            {"triggered_by": "design-infeasible", "prior_findings": ["needs a queue, not a cron"]}
        )
        self.assertIn("coder", block.lower())
        self.assertIn("needs a queue, not a cron", block)

    def test_architecture_rejected_still_renders(self) -> None:
        block = pipeline._reviewer_feedback_block(
            {"triggered_by": "architecture-rejected", "prior_findings": ["use Postgres"]}
        )
        self.assertIn("rejected", block.lower())
        self.assertIn("use Postgres", block)


class RouteAfterCoderTests(unittest.TestCase):
    def test_rearchitect_routes_to_architect(self) -> None:
        self.assertEqual(pipeline.route_after_coder({"next_action": "rearchitect"}), "architect-agent")

    def test_other_actions_unchanged(self) -> None:
        self.assertEqual(pipeline.route_after_coder({"next_action": "complete"}), "tester-agent")
        self.assertEqual(pipeline.route_after_coder({}), END)


class CoderDesignFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        self.db_path = self.root / "factory.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Seed", "do the thing")
            self.run_id = db.start_run(conn, "US-0001")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, **extra) -> dict:
        return {
            "run_id": self.run_id,
            "db_path": str(self.db_path),
            "story_id": "US-0001",
            "opencode_cwd": str(self.cwd),
            "spec": {
                "title": "T", "problem": "p", "why": "w",
                "acceptance_criteria": ["a", "b"],
                "tasks": [{"id": "T-0001", "title": "do it", "purpose": "x"}],
            },
            "architect": {"verdict": "pass", "architecture_notes": "n", "modules_affected": ["m"]},
            "task_index": 0,
            "attempt_number": 1,
            **extra,
        }

    def test_design_feedback_routes_back_to_architect(self) -> None:
        out_json = json.dumps(
            {"verdict": "blocked", "design_feedback": "The schema can't support this query pattern.",
             "code_blocks": []}
        )
        with patch.object(pipeline, "_run_or_replay", return_value=_ar(out_json)):
            out = pipeline.node_coder_agent(self._state())

        self.assertEqual(out.get("next_action"), "rearchitect")
        self.assertEqual(out.get("triggered_by"), "design-infeasible")
        self.assertEqual(out.get("rearchitect_count"), 1)
        self.assertIn("schema can't support", out["prior_findings"][0])
        # Fresh restart for the re-designed story; nothing committed yet.
        self.assertEqual(out.get("task_index"), 0)
        self.assertEqual(out.get("tasks_completed"), [])

    def test_design_feedback_past_budget_parks_for_human(self) -> None:
        out_json = json.dumps(
            {"verdict": "blocked", "design_feedback": "Still infeasible.", "code_blocks": []}
        )
        state = self._state(rearchitect_count=MAX_REARCHITECT_LOOPS)
        with patch.object(pipeline, "_run_or_replay", return_value=_ar(out_json)):
            out = pipeline.node_coder_agent(state)
        self.assertEqual(out.get("status"), "failed")
        self.assertIn("infeasible", out.get("error", "").lower())


if __name__ == "__main__":
    unittest.main()
