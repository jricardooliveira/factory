"""Presentation helpers (`factory.interfaces.render`) must render, never crash."""

from __future__ import annotations

import unittest


class SpecSummaryRobustnessTests(unittest.TestCase):
    """A blocked/off-script spec must never crash the CLI display layer."""

    def test_blocked_spec_does_not_raise(self) -> None:
        from factory.interfaces.render import print_spec_summary

        # The synthetic blocked dict omits required SpecOutput fields (problem/why).
        print_spec_summary(
            {
                "verdict": "blocked",
                "title": "",
                "acceptance_criteria": [],
                "tasks": [],
                "questions": ["Agent went off-script: token refresh failed: 401"],
            }
        )

    def test_valid_spec_still_renders(self) -> None:
        from factory.interfaces.render import print_spec_summary

        print_spec_summary(
            {
                "title": "T",
                "problem": "p",
                "why": "w",
                "acceptance_criteria": ["a", "b"],
                "tasks": [],
            }
        )



class GateResultLabelTests(unittest.TestCase):
    """A release that is not ready is WAITING for the operator, not a failed run."""

    def test_passed_and_failed_gates_keep_their_labels(self) -> None:
        from factory.interfaces.render.run import gate_result_label

        self.assertEqual(gate_result_label({"passed": 1, "needs_human": 0})[0], "PASS")
        self.assertEqual(gate_result_label({"passed": 0, "needs_human": 0})[0], "FAIL")

    def test_an_unready_gate_that_asks_the_operator_is_not_a_failure(self) -> None:
        from factory.interfaces.render.run import gate_result_label

        label, style = gate_result_label({"passed": 0, "needs_human": 1})
        self.assertEqual(label, "NOT READY")
        self.assertEqual(style, "yellow")

if __name__ == "__main__":
    unittest.main()
