"""Tests for trust-package assembly + schema validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from factory.evidence import trust_package as tp
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
        for key in ("work_id", "parent_story", "project_id", "stage", "verdict", "tests",
                    "diff", "adr", "security_boundary", "cost", "blockers",
                    "next_authorization"):
            self.assertIn(key, pkg)
        # Two evidence bars are unmet (below), so the package's verdict is `warn`,
        # not `pass` — it used to say `pass` here, overstating the evidence.
        self.assertEqual(pkg["verdict"], "warn")
        self.assertEqual(len(pkg["tests"]["ac_coverage"]), 2)
        self.assertEqual([f["path"] for f in pkg["diff"]["files"]], ["c.py"])
        self.assertAlmostEqual(pkg["cost"]["usd"], 0.03)
        # This run has no project repo, so the change set cannot be git-measured
        # and no test body ran. Both bars are unmet, so the package must say so
        # and must NOT offer release sign-off (see test_trust_truthfulness.py).
        self.assertEqual(pkg["diff"]["source"], "unavailable")
        self.assertFalse(pkg["tests"]["executed"])
        self.assertFalse(pkg["tests"]["passed"])
        self.assertEqual(pkg["next_authorization"], "operator-review")
        self.assertEqual(
            tp.validate(pkg),
            ["diff.source is 'unavailable': the change set was not measured from git, "
             "so §5.2 (real git diff, not the agent's self-report) is unmet"],
        )

    def test_failed_run_is_not_release_ready(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0002", "Broken", "x")
            rid = db.start_run(conn, "US-0002")
            db.log_gate(conn, rid, "gate-build", False, "py_compile:fail")
            db.finish_run(conn, rid, "failed", error="gate-build failed")
        pkg = tp.assemble(self.db_path, rid)
        self.assertEqual(pkg["verdict"], "fail")  # the schema's word; "failed" was invalid
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
        trace = pkg["ac_traceability"]
        self.assertEqual(trace["total"], 2)
        self.assertEqual(trace["covered"], 1)
        self.assertEqual(trace["unassessed"], ["results are paginated"])
        # tests.ac_coverage is now keyed off the REAL acceptance criteria + status.
        statuses = {e["criterion"]: e["status"] for e in pkg["tests"]["ac_coverage"]}
        self.assertEqual(statuses["search returns matching items"], "covered")
        self.assertEqual(statuses["results are paginated"], "unassessed")
        # A silently-dropped acceptance criterion is an unmet evidence bar, so it
        # must be named as a blocker rather than buried in the traceability block.
        self.assertTrue(any("never" in b and "assessed" in b for b in pkg["blockers"]),
                        pkg["blockers"])

    def test_assemble_unknown_run_is_empty(self) -> None:
        self.assertEqual(tp.assemble(self.db_path, 999), {})



class EvidenceBarTests(unittest.TestCase):
    """Bars the package claimed but never enforced (independent assessment, F1)."""

    def test_a_missing_design_record_is_a_named_blocker(self) -> None:
        blockers = tp._blockers(
            {"status": "waiting_human"}, True, True,
            {"source": "git", "scope_violations": []}, {"unassessed": []}, adr_path="",
        )
        self.assertTrue(any("ADR" in b for b in blockers), blockers)

    def test_a_present_design_record_is_not_a_blocker(self) -> None:
        blockers = tp._blockers(
            {"status": "waiting_human"}, True, True,
            {"source": "git", "scope_violations": []}, {"unassessed": []},
            adr_path="docs/architecture/adr/ADR-US-0001-x.md",
        )
        self.assertEqual(blockers, [])

    def test_schema_errors_report_shape_violations_only(self) -> None:
        errors = tp.schema_errors({"verdict": "shipped", "diff": {"source": "unavailable"}})
        self.assertTrue(any("verdict" in e for e in errors), errors)
        # The unmeasured diff is an evidence bar (already a blocker), not a shape error.
        self.assertFalse(any("unavailable" in e for e in errors), errors)


class ReleasedPackageTests(unittest.TestCase):
    """The saved package and the database must agree on what happens next (T01)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, status: str, release_response: str | None) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "T", "x")
            rid = db.start_run(conn, "US-0001")
            if release_response is not None:
                gid = db.log_gate(conn, rid, "gate-release", False, "NOT READY",
                                  needs_human=True)
                if release_response:
                    db.respond_to_gate(conn, gid, release_response)
            db.finish_run(conn, rid, status)
        return rid

    def test_a_run_the_operator_released_needs_no_further_authorization(self) -> None:
        pkg = tp.assemble(self.db_path, self._run("completed", "APPROVED: ship it"))
        self.assertEqual(pkg["next_authorization"], "none")
        self.assertEqual(pkg["verdict"], "warn")  # its gaps were accepted, not met

    def test_a_parked_release_awaits_the_operator(self) -> None:
        pkg = tp.assemble(self.db_path, self._run("waiting_human", ""))
        self.assertEqual(pkg["next_authorization"], "operator-review")

    def test_a_legacy_self_completed_run_still_needs_review(self) -> None:
        pkg = tp.assemble(self.db_path, self._run("completed", None))
        self.assertEqual(pkg["next_authorization"], "operator-review")

    def test_an_unreadable_schema_fails_closed(self) -> None:
        with patch.object(tp, "_SCHEMA_PATH", Path(self._tmp.name) / "missing.json"):
            errors = tp.schema_errors({"verdict": "pass"})
        self.assertTrue(errors and "schema" in errors[0], errors)

class SecurityFindingsTests(unittest.TestCase):
    """`findings` are what needs attention; a passing dimension's explanations of why
    it is fine are `notes` — kept in the record, off the Checkpoint 3 line."""

    def _block(self, review: dict, tester: dict | None = None) -> dict:
        import json

        from factory.evidence.trust_package import _security_boundary

        logs = [{"agent": "boundary-agent", "output_text": json.dumps(review)}]
        return _security_boundary(tester or {"security_verdict": "pass"}, logs)

    def test_a_passing_dimensions_findings_are_notes(self) -> None:
        block = self._block({
            "tenant": {"verdict": "not_applicable", "findings": ["single user"]},
            "authorization": {"verdict": "pass", "findings": ["loopback only"]},
            "api_contract": {"verdict": "pass", "findings": []},
            "security": {"verdict": "pass", "findings": ["Host and Origin are checked"]},
        })
        self.assertEqual(block["overall"], "pass")
        self.assertEqual(block["findings"], [])
        self.assertEqual(block["notes"], [
            "boundary review: single user", "boundary review: loopback only",
            "boundary review: Host and Origin are checked"])

    def test_a_warned_dimensions_findings_stay_findings(self) -> None:
        block = self._block(
            {"tenant": {"verdict": "pass", "findings": ["scoped by customer id"]},
             "security": {"verdict": "warn", "findings": ["no rate limit on export"]}},
            tester={"security_verdict": "pass", "security_findings": ["token logged at debug"]},
        )
        self.assertEqual(block["findings"], ["token logged at debug",
                                             "boundary review: no rate limit on export"])
        self.assertEqual(block["notes"], ["boundary review: scoped by customer id"])


if __name__ == "__main__":
    unittest.main()
