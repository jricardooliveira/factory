"""The boundary review in the line: architect → [boundary-agent] → gate-2.

Runs only when the design declares a boundary impact (API, data, breaking change,
sensitive area). A failed review sends the design back to the architect once with
the required changes; failing again, gate-2 rejects it. Warnings park at
Checkpoint 2. A clean review's rules reach the coder and the tester, and its
sub-verdicts fill the trust package's tenant/authorization evidence (§5 #4).

Driven offline through the real run service on frozen agent outputs.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import runs
from factory.evidence import trust_package
from factory.state import db
from factory.workspace.projects import create_project

SPEC = {
    "title": "Ticket list", "problem": "agents cannot see tickets", "why": "triage",
    "acceptance_criteria": ["agents list open tickets", "customers see only their own"],
    "tasks": [{"id": "T-1", "title": "list", "purpose": "GET /tickets", "scope": ["src/"],
               "completion_evidence": "test passes"}],
    "verdict": "pass", "questions": [],
}
PLAIN_ARCH = {"verdict": "pass", "architecture_notes": "a pure helper",
              "modules_affected": ["src/util.py"]}
API_ARCH = {"verdict": "pass", "architecture_notes": "GET /tickets filtered by role",
            "modules_affected": ["src/tickets.py"], "api_impact": "yes — adds GET /tickets"}
CODER = json.dumps({"verdict": "complete", "code_blocks": [
    {"path": "src/tickets.py", "content": "def tickets():\n    return []\n", "action": "create"}]})
TESTER = {"overall": "pass", "qa_verdict": "pass",
          "ac_coverage": ["agents list open tickets", "customers see only their own"],
          "security_verdict": "pass", "highest_severity": "none",
          "performance_verdict": "pass", "summary": "ok"}


def _review(tenant: str = "pass", authorization: str = "pass", **extra) -> dict:
    out = {
        "overall": "pass",
        "tenant": {"verdict": tenant, "findings": [] if tenant == "pass" else
                   ["customer id is read from the query string"]},
        "authorization": {"verdict": authorization, "findings": [] if authorization == "pass"
                          else ["no role check on GET /tickets"]},
        "api_contract": {"verdict": "pass", "findings": [], "breaking_changes": []},
        "security": {"verdict": "not_applicable", "findings": []},
        "rules_for_coder": ["scope every ticket query by the caller's customer id from the session"],
        "required_changes": [] if tenant == "pass" else ["take the customer id from auth"],
    }
    out.update(extra)
    return out


def _blocked(*_a, **_k):
    raise RuntimeError("LIVE CALL BLOCKED")


class BoundaryReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="support")
        guard = patch("factory.pipeline.agent_calls.run_agent", _blocked)
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, arch: dict, boundary: object = None) -> runs.RunOutcome:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Pending", "list tickets",
                            project_id=self.project["id"])
            orig = db.start_run(conn, "US-0001", project_id=self.project["id"])
            db.log_agent(conn, orig, "spec-agent", "in", json.dumps(SPEC), verdict="pass")
            db.log_agent(conn, orig, "architect-agent", "in", json.dumps(arch), verdict="pass")
            if boundary is not None:
                text = boundary if isinstance(boundary, str) else json.dumps(boundary)
                db.log_agent(conn, orig, "boundary-agent", "in", text, verdict="pass")
            db.log_agent(conn, orig, "coder-agent", "in", CODER, verdict="complete",
                         stage_type="T-1")
            db.log_agent(conn, orig, "tester-agent", "in", json.dumps(TESTER), verdict="pass")
        return runs.run_pipeline("list tickets", opencode_cwd=self.project["repo_path"],
                                 db_path=self.db_path, project_id=self.project["id"],
                                 replay_run_id=orig)

    def _logs(self, run_id: int, agent: str) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [log for log in db.get_run_logs(conn, run_id) if log["agent"] == agent]

    def _gate(self, run_id: int, name: str) -> dict:
        with db.get_db(self.db_path) as conn:
            return [g for g in db.get_run_gates(conn, run_id) if g["gate_name"] == name][-1]

    def test_a_design_without_boundary_impact_skips_the_review(self) -> None:
        outcome = self._run(PLAIN_ARCH)
        self.assertEqual(outcome.status, "waiting_human", outcome.error)  # Checkpoint 3
        self.assertEqual(self._logs(outcome.run_id, "boundary-agent"), [])

    def test_a_clean_review_reaches_the_coder_tester_and_trust_package(self) -> None:
        outcome = self._run(API_ARCH, _review())
        self.assertEqual(outcome.status, "waiting_human", outcome.error)
        self.assertFalse(self._gate(outcome.run_id, "gate-2-architect")["needs_human"])
        rule = "scope every ticket query by the caller's customer id from the session"
        [coder] = self._logs(outcome.run_id, "coder-agent")
        [tester] = self._logs(outcome.run_id, "tester-agent")
        self.assertIn(rule, coder["input_text"])
        self.assertIn(rule, tester["input_text"])
        pkg = trust_package.assemble(self.db_path, outcome.run_id)
        self.assertEqual(pkg["security_boundary"]["tenant_isolation"], "pass")
        self.assertEqual(pkg["security_boundary"]["authorization"], "pass")
        self.assertEqual(trust_package.schema_errors(pkg), [])

    def test_a_failed_review_redesigns_once_then_gate_two_rejects(self) -> None:
        outcome = self._run(API_ARCH, _review(tenant="fail"))
        self.assertEqual(outcome.status, "failed")
        architects = self._logs(outcome.run_id, "architect-agent")
        self.assertEqual(len(architects), 2)  # the original design + one redesign
        self.assertIn("take the customer id from auth", architects[1]["input_text"])
        gate = self._gate(outcome.run_id, "gate-2-architect")
        self.assertFalse(gate["passed"])
        self.assertIn("customer id is read from the query string", gate["reason"])
        self.assertEqual(self._logs(outcome.run_id, "coder-agent"), [])  # never coded

    def test_warnings_park_at_checkpoint_2(self) -> None:
        outcome = self._run(API_ARCH, _review(authorization="warn"))
        self.assertEqual(outcome.status, "waiting_human")
        gate = self._gate(outcome.run_id, "gate-2-architect")
        self.assertTrue(gate["needs_human"])
        self.assertIn("no role check on GET /tickets", gate["human_questions"])

    def test_a_run_recorded_before_the_boundary_agent_replays_unchanged(self) -> None:
        outcome = self._run(API_ARCH, boundary=None)
        self.assertEqual(outcome.status, "waiting_human", outcome.error)  # Checkpoint 3
        [log] = self._logs(outcome.run_id, "boundary-agent")
        self.assertEqual(log["verdict"], "skipped")
        self.assertFalse(self._gate(outcome.run_id, "gate-2-architect")["needs_human"])

    def test_a_review_that_went_off_script_asks_the_operator(self) -> None:
        outcome = self._run(API_ARCH, boundary="I think the design is fine.")
        self.assertEqual(outcome.status, "waiting_human")
        self.assertIn("BOUNDARY REVIEW MISSING",
                      self._gate(outcome.run_id, "gate-2-architect")["human_questions"])


if __name__ == "__main__":
    unittest.main()
