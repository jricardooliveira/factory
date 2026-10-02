"""The boundary review: when it is required, and what its verdict means (pure policy).

Original brief §5.5 / Gate 4: a blocking PRE-implementation review of tenant
isolation, authorization, the API contract and security-sensitive design, with
SEPARATE sub-verdicts — "if any required sub-verdict is fail, the overall verdict
must be fail". That rule is enforced here in code, not trusted to the agent.
"""

from __future__ import annotations

import unittest

from factory.domain.authorization import authorize_boundary
from factory.domain.contracts import ArchitectOutput, BoundaryOutput
from factory.domain.gates import (
    boundary_overall,
    boundary_review_reasons,
    gate_after_architect,
)

ARCH = ArchitectOutput(architecture_notes="layered", modules_affected=["api/"])


def _review(**subs) -> BoundaryOutput:
    data = {"overall": "pass"}
    data.update({k: {"verdict": v, "findings": [f"{k} finding"] if v in ("warn", "fail") else []}
                 for k, v in subs.items()})
    return BoundaryOutput.model_validate(data)


class WhenIsAReviewRequiredTests(unittest.TestCase):
    def test_a_design_with_no_boundary_impact_needs_no_review(self) -> None:
        self.assertEqual(boundary_review_reasons(ARCH), [])

    def test_api_db_breaking_and_sensitive_designs_need_one(self) -> None:
        for change in ({"api_impact": "yes — adds GET /tickets"}, {"db_impact": "yes"},
                       {"migration_needed": "yes"}, {"breaking_changes": ["renames a field"]},
                       {"sensitivity": ["pii"]}):
            with self.subTest(change=change):
                self.assertTrue(boundary_review_reasons(ARCH.model_copy(update=change)))

    def test_no_written_as_prose_is_still_no(self) -> None:
        arch = ARCH.model_copy(update={"api_impact": "No.", "db_impact": "none"})
        self.assertEqual(boundary_review_reasons(arch), [])


class OverallVerdictTests(unittest.TestCase):
    def test_any_failed_sub_verdict_fails_the_review_whatever_the_agent_says(self) -> None:
        review = _review(tenant="pass", authorization="fail")
        self.assertEqual(review.overall, "pass")  # the agent's claim...
        self.assertEqual(boundary_overall(review), "fail")  # ...is not the verdict

    def test_a_warning_without_failures_is_a_warning(self) -> None:
        self.assertEqual(boundary_overall(_review(api_contract="warn", tenant="pass")), "warn")

    def test_not_applicable_everywhere_passes(self) -> None:
        self.assertEqual(boundary_overall(_review()), "pass")


class GateTwoWithABoundaryReviewTests(unittest.TestCase):
    def test_a_failed_review_rejects_the_design(self) -> None:
        result = gate_after_architect(ARCH, boundary=_review(tenant="fail"))
        self.assertFalse(result.passed)
        self.assertIn("tenant finding", result.reason)

    def test_warnings_park_for_the_operator_with_the_findings(self) -> None:
        result = gate_after_architect(ARCH, boundary=_review(authorization="warn"))
        self.assertTrue(result.passed)
        self.assertTrue(result.needs_human)
        self.assertIn("authorization finding", "\n".join(result.human_questions))

    def test_a_clean_review_adds_no_interruption(self) -> None:
        result = gate_after_architect(ARCH, boundary=_review(tenant="pass", security="pass"))
        self.assertTrue(result.passed)
        self.assertFalse(result.needs_human)

    def test_breaking_changes_found_by_the_review_need_approval(self) -> None:
        review = BoundaryOutput.model_validate({"api_contract": {
            "verdict": "pass", "breaking_changes": ["drops the `page` param"]}})
        result = gate_after_architect(ARCH, boundary=review)
        self.assertTrue(result.needs_human)
        self.assertIn("drops the `page` param", "\n".join(result.human_questions))

    def test_a_required_review_that_could_not_run_asks_the_operator(self) -> None:
        arch = ARCH.model_copy(update={"api_impact": "yes"})
        result = gate_after_architect(arch, boundary=None, boundary_unavailable=True)
        self.assertTrue(result.needs_human)
        self.assertIn("BOUNDARY REVIEW", "\n".join(result.human_questions))

    def test_without_a_review_gate_two_is_unchanged(self) -> None:
        result = gate_after_architect(ARCH)
        self.assertTrue(result.passed)
        self.assertFalse(result.needs_human)


    def test_the_sensitivity_question_shows_what_the_boundary_review_found(self) -> None:
        # The park itself is the operator's policy and stays; the answer is beside it.
        arch = ARCH.model_copy(update={"sensitivity": ["security"]})
        result = gate_after_architect(arch, boundary=_review(tenant="not_applicable",
                                                             authorization="pass",
                                                             security="pass"))
        self.assertTrue(result.needs_human)
        text = "\n".join(result.human_questions)
        self.assertIn("boundary review", text.lower())
        self.assertIn("security: pass", text)

class BoundaryAuthorizationTests(unittest.TestCase):
    def test_a_design_that_needs_review_authorizes_the_reviewer(self) -> None:
        arch = ARCH.model_copy(update={"api_impact": "yes"})
        auth = authorize_boundary(arch)
        self.assertTrue(auth.allowed)
        self.assertEqual(auth.stage, "boundary-agent")
        self.assertIn("API", auth.granted_by)

    def test_no_design_or_no_need_is_refused(self) -> None:
        self.assertFalse(authorize_boundary(None).allowed)
        self.assertFalse(authorize_boundary(ARCH).allowed)  # nothing to review


if __name__ == "__main__":
    unittest.main()
