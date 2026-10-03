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


class NextStepHintTests(unittest.TestCase):
    """After the brief and the backlog, the operator is told the exact next command."""

    def _printed(self, fn, *args) -> str:
        from factory.interfaces.render import output

        with output.console.capture() as captured:
            fn(*args)
        return " ".join(captured.get().split())

    def test_brief_approved_names_factory_backlog(self) -> None:
        from pathlib import Path

        from factory.interfaces.render import print_interview_approved

        text = self._printed(print_interview_approved, Path("/x/BRIEF.md"), "habits")
        self.assertIn("factory backlog habits", text)

    def test_backlog_approved_names_factory_next_with_the_project(self) -> None:
        from factory.interfaces.render.backlog import print_backlog_approved

        text = self._printed(print_backlog_approved, 9, "habits")
        self.assertIn("factory next habits", text)


class PausedRunTests(unittest.TestCase):
    def _printed(self, fn, *args, **kwargs) -> str:
        from factory.interfaces.render import output

        with output.console.capture() as captured:
            fn(*args, **kwargs)
        return " ".join(captured.get().split())

    def test_a_checkpoint_question_keeps_its_bracketed_tags(self) -> None:
        # Gate text like "[security]" is data, not rich markup: it must not vanish.
        from factory.interfaces.render.run import print_final_status

        text = self._printed(print_final_status, "waiting_human",
                             human_questions=["SENSITIVE WORK detected [security] — ok?"],
                             run_id=12)
        self.assertIn("[security]", text)
        self.assertIn("factory approve 12", text)
        self.assertIn("factory reject 12", text)
