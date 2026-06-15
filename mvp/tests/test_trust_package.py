"""Tests for trust-package assembly + schema validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory import trust_package as tp
from factory.state import db


class TrustPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _completed_run(self) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Converter", "build it")
            rid = db.start_run(conn, "US-0001")
            db.log_agent(conn, rid, "coder-agent", "p",
                         json.dumps({"verdict": "complete",
                                     "code_blocks": [{"path": "c.py", "content": "x=1\n"}]}),
                         verdict="complete", stage_type="T-1", cost_usd=0.02)
            db.log_gate(conn, rid, "gate-build", True, "ok")
            db.log_agent(conn, rid, "tester-agent", "p",
                         json.dumps({"overall": "pass", "ac_coverage": ["a", "b"],
                                     "security_verdict": "pass", "highest_severity": "none"}),
                         verdict="pass", cost_usd=0.01)
            db.log_gate(conn, rid, "gate-test", True, "ok")
            db.finish_run(conn, rid, "completed")
        return rid

    def test_assemble_has_all_required_fields(self) -> None:
        pkg = tp.assemble(self.db_path, self._completed_run())
        self.assertEqual(tp.validate(pkg), [])  # matches schema's required keys
        self.assertEqual(pkg["verdict"], "pass")
        self.assertTrue(pkg["tests"]["passed"])
        self.assertEqual(len(pkg["tests"]["ac_coverage"]), 2)
        self.assertEqual([f["path"] for f in pkg["diff"]["files"]], ["c.py"])
        self.assertEqual(pkg["next_authorization"], "release")
        self.assertAlmostEqual(pkg["cost"]["usd"], 0.03)

    def test_failed_run_is_not_release_ready(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0002", "Broken", "x")
            rid = db.start_run(conn, "US-0002")
            db.log_gate(conn, rid, "gate-build", False, "py_compile:fail")
            db.finish_run(conn, rid, "failed", error="gate-build failed")
        pkg = tp.assemble(self.db_path, rid)
        self.assertEqual(pkg["verdict"], "failed")
        self.assertFalse(pkg["tests"]["passed"])
        self.assertEqual(pkg["next_authorization"], "operator-review")
        self.assertTrue(pkg["blockers"])

    def test_ac_traceability_flags_unassessed_criterion(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0003", "Search", "build search")
            rid = db.start_run(conn, "US-0003")
            db.log_agent(conn, rid, "spec-agent", "p",
                         json.dumps({"title": "Search", "problem": "p", "why": "w",
                                     "acceptance_criteria": [
                                         "search returns matching items",
                                         "results are paginated",  # tester never assessed this
                                     ]}),
                         verdict="pass")
            db.log_agent(conn, rid, "coder-agent", "p",
                         json.dumps({"verdict": "complete",
                                     "code_blocks": [{"path": "s.py", "content": "x=1\n"}]}),
                         verdict="complete", stage_type="T-1")
            db.log_gate(conn, rid, "gate-build", True, "ok")
            db.log_agent(conn, rid, "tester-agent", "p",
                         json.dumps({"overall": "pass",
                                     "ac_coverage": ["search returns matching items"],
                                     "security_verdict": "pass", "highest_severity": "none"}),
                         verdict="pass")
            db.log_gate(conn, rid, "gate-test", True, "ok")
            db.finish_run(conn, rid, "completed")

        pkg = tp.assemble(self.db_path, rid)
        self.assertEqual(tp.validate(pkg), [])
        trace = pkg["ac_traceability"]
        self.assertEqual(trace["total"], 2)
        self.assertEqual(trace["covered"], 1)
        self.assertEqual(trace["unassessed"], ["results are paginated"])
        # tests.ac_coverage is now keyed off the REAL acceptance criteria + status.
        statuses = {e["criterion"]: e["status"] for e in pkg["tests"]["ac_coverage"]}
        self.assertEqual(statuses["search returns matching items"], "covered")
        self.assertEqual(statuses["results are paginated"], "unassessed")

    def test_assemble_unknown_run_is_empty(self) -> None:
        self.assertEqual(tp.assemble(self.db_path, 999), {})


if __name__ == "__main__":
    unittest.main()
