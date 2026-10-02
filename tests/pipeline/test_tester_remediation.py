"""Tester failures route back to the coder for a bounded remediation pass.

The full fail→remediate→pass round-trip can't be expressed in replay (the tester
would re-fetch its single most-recent stored output), so the new routing + gate
decision + remediation coder pass are tested directly against a real DB, mocking
only the single agent-call boundary.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.graph import END

from factory.pipeline import agent_calls
from factory.pipeline.graph import route_after_gate_test
from factory.pipeline.nodes.coder import node_coder_agent
from factory.pipeline.nodes.gates import node_gate_test
from factory.domain.gates import MAX_TESTER_REMEDIATIONS
from factory.adapters.opencode import AgentResult
from factory.state import db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "agent_outputs"


def _run_status(db_path, run_id) -> str:
    with db.get_db(db_path) as conn:
        row = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    return row["status"]


def _ar(output: str) -> AgentResult:
    return AgentResult(agent="coder-agent", output=output, duration_secs=0.0, returncode=0)


class RouteAfterGateTestTests(unittest.TestCase):
    def test_remediation_routes_back_to_coder(self) -> None:
        self.assertEqual(
            route_after_gate_test({"remediation": True}), "coder-agent"
        )

    def test_completed_ends(self) -> None:
        self.assertEqual(
            route_after_gate_test({"remediation": True, "status": "completed"}), END
        )

    def test_no_remediation_ends(self) -> None:
        self.assertEqual(route_after_gate_test({}), END)


class GateTestRemediationDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
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
            "tester": json.loads((FIXTURES / "tester_fail.json").read_text()),
            **extra,
        }

    def test_first_failure_routes_to_remediation_and_keeps_run_running(self) -> None:
        out = node_gate_test(self._state())
        self.assertTrue(out.get("remediation"))
        self.assertEqual(out.get("triggered_by"), "tester-agent")
        self.assertEqual(out.get("tester_attempt"), 2)
        self.assertEqual(out.get("attempt_number"), 2)  # escalates coder to frontier
        # Findings were flattened from the sub-verdicts.
        self.assertTrue(any("search" in f.lower() for f in out["prior_findings"]))
        self.assertNotIn("status", out)  # run not finished
        self.assertEqual(_run_status(self.db_path, self.run_id), "running")

    def test_exhausted_budget_fails_the_run(self) -> None:
        out = node_gate_test(self._state(tester_attempt=MAX_TESTER_REMEDIATIONS + 1))
        self.assertEqual(out.get("status"), "failed")
        self.assertNotIn("remediation", out)
        self.assertEqual(_run_status(self.db_path, self.run_id), "failed")

    def test_passing_gate_annotates_unassessed_acs(self) -> None:
        # Tester passes overall, but never assessed one of the spec's criteria.
        state = {
            "run_id": self.run_id,
            "db_path": str(self.db_path),
            "story_id": "US-0001",
            "spec": {"title": "T", "problem": "p", "why": "w",
                     "acceptance_criteria": ["alpha works", "beta is paginated"]},
            "tester": {"overall": "pass", "qa_verdict": "pass",
                       "ac_coverage": ["alpha works"], "security_verdict": "pass",
                       "highest_severity": "none", "performance_verdict": "pass"},
        }
        out = node_gate_test(state)
        # Still passes — non-blocking. Passing no longer COMPLETES the story: it
        # routes to release (Checkpoint 3), so no terminal status is set here.
        self.assertTrue(out["gate_test"]["passed"])
        self.assertNotIn("status", out)
        self.assertIn("unassessed", out["gate_test"]["reason"].lower())
        self.assertIn("beta is paginated", out["gate_test"]["reason"])


class CoderRemediationPassTests(unittest.TestCase):
    def setUp(self) -> None:
        from factory.workspace.git import git_commit_all, git_init

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        self.db_path = self.root / "factory.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Seed", "do the thing")
            self.run_id = db.start_run(conn, "US-0001")
        # A git repo with a prior factory commit, so collect_repo_diff has a baseline.
        git_init(self.cwd)
        (self.cwd / "app.py").write_text("def add(a, b):\n    return a + b\n")
        git_commit_all(self.cwd, "factory: T-0001 add")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self) -> dict:
        return {
            "run_id": self.run_id,
            "db_path": str(self.db_path),
            "story_id": "US-0001",
            "opencode_cwd": str(self.cwd),
            "spec": {"title": "Adder", "problem": "need a test", "why": "coverage",
                     "acceptance_criteria": ["add works"]},
            "architect": {"verdict": "pass"},
            "remediation": True,
            "triggered_by": "tester-agent",
            "prior_findings": ["Missing test coverage: add works"],
            "attempt_number": 2,
        }

    def test_successful_remediation_routes_back_to_tester(self) -> None:
        fix = json.dumps(
            {
                "verdict": "complete",
                "implementation_summary": "added a test",
                "code_blocks": [
                    {"path": "test_app.py",
                     "content": "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                     "action": "create"}
                ],
            }
        )
        with patch.object(agent_calls, "_run_or_replay", return_value=_ar(fix)):
            out = node_coder_agent(self._state())

        self.assertEqual(out.get("next_action"), "complete")  # back to tester
        self.assertFalse(out.get("remediation"))
        self.assertTrue((self.cwd / "test_app.py").is_file())
        self.assertTrue(out["gate_build"]["passed"])
        # The remediation prompt carried the findings + a real diff.
        # (verified indirectly: the coder log was stored under the 'remediation' stage)
        with db.get_db(self.db_path) as conn:
            logs = db.get_run_logs(conn, self.run_id)
        rem = [l for l in logs if l["stage_type"] == "remediation"]
        self.assertEqual(len(rem), 1)
        self.assertIn("Findings to resolve", rem[0]["input_text"])

    def test_remediation_that_breaks_build_fails_the_run(self) -> None:
        broken = json.dumps(
            {
                "verdict": "complete",
                "code_blocks": [
                    {"path": "test_app.py", "content": "def test(:\n  pass\n", "action": "create"}
                ],
            }
        )
        with patch.object(agent_calls, "_run_or_replay", return_value=_ar(broken)):
            out = node_coder_agent(self._state())
        self.assertEqual(out.get("status"), "failed")
        self.assertEqual(out.get("next_action"), "give_up")
        self.assertFalse(out["gate_build"]["passed"])


if __name__ == "__main__":
    unittest.main()
