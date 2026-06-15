"""Tests for deterministic acceptance-criteria traceability."""

from __future__ import annotations

import unittest

from factory.traceability import trace_criteria, unassessed_criteria


class TraceCriteriaTests(unittest.TestCase):
    def test_exact_coverage_marks_covered(self) -> None:
        trace = trace_criteria(
            ["Converts 0C to 32F", "Converts 100C to 212F"],
            ["Converts 0C to 32F", "Converts 100C to 212F"],
            [],
        )
        self.assertTrue(all(e["status"] == "covered" for e in trace))

    def test_paraphrased_claim_still_matches(self) -> None:
        trace = trace_criteria(
            ["search returns matching items"],
            ["search endpoint returns the matching items"],
            [],
        )
        self.assertEqual(trace[0]["status"], "covered")

    def test_criterion_only_in_missing_is_flagged(self) -> None:
        trace = trace_criteria(
            ["search returns matching items"],
            [],
            ["search returns matching items"],
        )
        self.assertEqual(trace[0]["status"], "flagged_missing")

    def test_unmentioned_criterion_is_unassessed(self) -> None:
        trace = trace_criteria(
            ["Converts 0C to 32F", "rejects negative input"],
            ["Converts 0C to 32F"],
            [],
        )
        self.assertEqual(unassessed_criteria(trace), ["rejects negative input"])

    def test_no_criteria_yields_empty(self) -> None:
        self.assertEqual(trace_criteria([], ["x"], []), [])


if __name__ == "__main__":
    unittest.main()
