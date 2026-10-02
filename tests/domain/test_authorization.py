"""The boss's authorization rules: may this stage START, given what exists?

Gates judge a stage's OUTPUT after it ran; authorization judges its INPUTS
before a token is spent (original brief §5.3, "boss may call X only if…" and the
failure mode "boss starts stages with missing artifacts"). Pure decisions here;
the DB reads and the blocking live in tests/pipeline/test_boss.py.
"""

from __future__ import annotations

import unittest

from factory.domain.authorization import (
    GateRecord,
    authorize_architect,
    authorize_release,
    authorize_release_notes,
    authorize_remediation,
    authorize_task,
    authorize_tester,
    latest_gates,
)
from factory.domain.contracts import ArchitectOutput, SpecOutput, TaskDef

SPEC = SpecOutput.model_validate({
    "title": "Search", "problem": "p", "why": "w",
    "acceptance_criteria": ["finds by title", "paginates"],
    "tasks": [{"id": "T-1", "title": "a", "purpose": "a", "scope": ["src/"],
               "completion_evidence": "test passes"}],
})
ARCH = ArchitectOutput(architecture_notes="layered", modules_affected=["src/search"])
PASSED_1 = GateRecord("gate-1-spec", passed=True)
PASSED_2 = GateRecord("gate-2-architect", passed=True)


def _task(tid: str = "T-2", *deps: str, scope: list[str] | None = None,
          evidence: str = "test passes", purpose: str = "do it") -> TaskDef:
    return TaskDef(id=tid, title=tid, purpose=purpose,
                   scope=["src/"] if scope is None else scope,
                   completion_evidence=evidence, depends_on=list(deps))


class LatestGatesTests(unittest.TestCase):
    def test_the_newest_row_per_gate_wins(self) -> None:
        rows = [
            {"gate_name": "gate-2-architect", "passed": 1, "needs_human": 1,
             "human_response": "REJECTED: no"},
            {"gate_name": "gate-2-architect", "passed": 1, "needs_human": 0,
             "human_response": None},
        ]
        gate = latest_gates(rows)["gate-2-architect"]
        self.assertTrue(gate.passed)
        self.assertFalse(gate.needs_human)


class ArchitectAuthorizationTests(unittest.TestCase):
    def test_a_passed_story_gate_authorizes_the_architect(self) -> None:
        auth = authorize_architect(SPEC, PASSED_1)
        self.assertTrue(auth.allowed)
        self.assertIn("gate-1-spec passed", auth.granted_by)

    def test_no_story_is_refused(self) -> None:
        auth = authorize_architect(None, PASSED_1)
        self.assertFalse(auth.allowed)
        self.assertTrue(any("story" in m for m in auth.missing))

    def test_a_gate_that_never_ran_is_refused(self) -> None:
        self.assertFalse(authorize_architect(SPEC, None).allowed)

    def test_a_failed_story_gate_is_refused(self) -> None:
        auth = authorize_architect(SPEC, GateRecord("gate-1-spec", passed=False))
        self.assertFalse(auth.allowed)

    def test_an_unanswered_checkpoint_is_refused(self) -> None:
        auth = authorize_architect(SPEC, GateRecord("gate-1-spec", True, needs_human=True))
        self.assertFalse(auth.allowed)
        self.assertTrue(any("operator" in m for m in auth.missing))

    def test_an_operator_approval_authorizes_and_says_who(self) -> None:
        gate = GateRecord("gate-1-spec", True, needs_human=True,
                          human_response="APPROVED: go")
        auth = authorize_architect(SPEC, gate)
        self.assertTrue(auth.allowed)
        self.assertIn("operator", auth.granted_by)

    def test_an_operator_rejection_is_refused(self) -> None:
        gate = GateRecord("gate-1-spec", True, needs_human=True,
                          human_response="REJECTED: overdue means 24h")
        self.assertFalse(authorize_architect(SPEC, gate).allowed)

    def test_a_story_without_tasks_or_criteria_is_refused(self) -> None:
        empty = SpecOutput(title="t", problem="p", why="w")
        auth = authorize_architect(empty, PASSED_1)
        self.assertFalse(auth.allowed)
        self.assertEqual(len(auth.missing), 2)


class TaskAuthorizationTests(unittest.TestCase):
    def test_an_approved_design_authorizes_a_ready_task(self) -> None:
        auth = authorize_task(_task("T-2", "T-1"), ["T-1"], ARCH, PASSED_2)
        self.assertTrue(auth.allowed, auth.missing)
        self.assertEqual(auth.stage, "coder-agent:T-2")
        self.assertEqual(auth.warnings, ())

    def test_an_unimplemented_dependency_is_refused(self) -> None:
        # The bug class order_tasks forgives: building T-2 before T-1 exists.
        auth = authorize_task(_task("T-2", "T-1"), [], ARCH, PASSED_2)
        self.assertFalse(auth.allowed)
        self.assertTrue(any("T-1" in m for m in auth.missing))

    def test_no_approved_design_is_refused(self) -> None:
        self.assertFalse(authorize_task(_task(), [], None, PASSED_2).allowed)
        self.assertFalse(authorize_task(_task(), [], ArchitectOutput(), PASSED_2).allowed)
        self.assertFalse(authorize_task(_task(), [], ARCH, None).allowed)

    def test_an_unanswered_design_checkpoint_is_refused(self) -> None:
        gate = GateRecord("gate-2-architect", True, needs_human=True)
        self.assertFalse(authorize_task(_task(), [], ARCH, gate).allowed)

    def test_an_approved_design_checkpoint_authorizes(self) -> None:
        gate = GateRecord("gate-2-architect", True, needs_human=True,
                          human_response="APPROVED: accepted the risk")
        self.assertTrue(authorize_task(_task(), [], ARCH, gate).allowed)

    def test_a_task_with_no_purpose_is_refused(self) -> None:
        self.assertFalse(authorize_task(_task(purpose=" "), [], ARCH, PASSED_2).allowed)

    def test_missing_scope_and_evidence_are_recorded_but_do_not_block(self) -> None:
        auth = authorize_task(_task(scope=[], evidence=""), [], ARCH, PASSED_2)
        self.assertTrue(auth.allowed)
        self.assertEqual(len(auth.warnings), 2)

    def test_no_task_at_all_is_refused(self) -> None:
        auth = authorize_task(None, [], ARCH, PASSED_2)
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.stage, "coder-agent")


class RemediationAuthorizationTests(unittest.TestCase):
    def test_a_failed_test_gate_with_findings_authorizes_remediation(self) -> None:
        auth = authorize_remediation(GateRecord("gate-test", False), ["no negative test"])
        self.assertTrue(auth.allowed)
        self.assertEqual(auth.stage, "coder-agent:remediation")

    def test_remediation_without_a_failed_test_gate_is_refused(self) -> None:
        self.assertFalse(authorize_remediation(None, ["x"]).allowed)
        self.assertFalse(authorize_remediation(GateRecord("gate-test", True), ["x"]).allowed)

    def test_remediation_without_findings_is_refused(self) -> None:
        self.assertFalse(authorize_remediation(GateRecord("gate-test", False), []).allowed)


    def test_an_operator_rejection_at_release_authorizes_remediation(self) -> None:
        # Checkpoint 3 reject = "send it back": gate-test PASSED, the operator did not.
        release = GateRecord("gate-release", True, needs_human=True,
                             human_response="REJECTED: the error page leaks a stack trace")
        auth = authorize_remediation(GateRecord("gate-test", True), ["leaks a stack trace"],
                                     gate_release=release)
        self.assertTrue(auth.allowed, auth.missing)
        self.assertIn("Checkpoint 3", auth.granted_by)


class ReleaseAuthorizationTests(unittest.TestCase):
    def test_release_notes_need_a_passed_test_gate(self) -> None:
        self.assertTrue(authorize_release_notes(GateRecord("gate-test", True)).allowed)
        self.assertFalse(authorize_release_notes(GateRecord("gate-test", False)).allowed)
        self.assertFalse(authorize_release_notes(None).allowed)

    def test_only_an_operator_approval_releases(self) -> None:
        approved = GateRecord("gate-release", True, needs_human=True,
                              human_response="APPROVED: ship it")
        auth = authorize_release(approved)
        self.assertTrue(auth.allowed)
        self.assertEqual(auth.stage, "release")
        self.assertIn("operator", auth.granted_by)

    def test_no_decision_or_a_rejection_does_not_release(self) -> None:
        self.assertFalse(authorize_release(None).allowed)
        self.assertFalse(authorize_release(
            GateRecord("gate-release", True, needs_human=True)).allowed)
        self.assertFalse(authorize_release(GateRecord(
            "gate-release", True, needs_human=True, human_response="REJECTED: no")).allowed)

    def test_a_release_gate_that_did_not_ask_cannot_release(self) -> None:
        # No agent passes the release gate: a verdict with no operator in it is refused.
        self.assertFalse(authorize_release(GateRecord("gate-release", True)).allowed)

    def test_approving_an_unready_release_is_allowed_and_recorded_as_accepted_risk(self) -> None:
        gate = GateRecord("gate-release", False, needs_human=True,
                          human_response="APPROVED: tests run in CI")
        auth = authorize_release(gate)
        self.assertTrue(auth.allowed)
        self.assertTrue(any("accepted" in w for w in auth.warnings))

class TesterAuthorizationTests(unittest.TestCase):
    def test_every_task_built_and_a_green_build_authorizes_the_tester(self) -> None:
        auth = authorize_tester(["T-1", "T-2"], ["T-1", "T-2"], GateRecord("gate-build", True))
        self.assertTrue(auth.allowed)

    def test_an_unimplemented_task_is_refused(self) -> None:
        auth = authorize_tester(["T-1", "T-2"], ["T-1"], GateRecord("gate-build", True))
        self.assertFalse(auth.allowed)
        self.assertTrue(any("T-2" in m for m in auth.missing))

    def test_a_red_or_missing_build_is_refused(self) -> None:
        self.assertFalse(authorize_tester(["T-1"], ["T-1"], None).allowed)
        self.assertFalse(
            authorize_tester(["T-1"], ["T-1"], GateRecord("gate-build", False)).allowed
        )


class SummaryTests(unittest.TestCase):
    def test_a_refusal_names_the_stage_and_everything_missing(self) -> None:
        auth = authorize_tester(["T-1"], [], None)
        text = auth.summary()
        self.assertIn("tester-agent", text)
        self.assertIn("T-1", text)
        self.assertIn("gate-build", text)


if __name__ == "__main__":
    unittest.main()
