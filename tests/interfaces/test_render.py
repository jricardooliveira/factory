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


class ResumeBannerTests(unittest.TestCase):
    """The banner after a decision names what actually happens next."""

    def _banner(self, entry: str, action: str) -> str:
        from factory.interfaces.render import output
        from factory.interfaces.render.run import print_resume_entered
        from factory.runs.events import ResumeEntered

        with output.console.capture() as captured:
            print_resume_entered(ResumeEntered(7, entry, action, "note"))
        return " ".join(captured.get().split())

    def test_checkpoint_3_approve_says_released(self) -> None:
        text = self._banner("release", "approve")
        self.assertIn("RELEASED", text)
        self.assertNotIn("coder-agent", text)

    def test_checkpoint_3_reject_says_remediation(self) -> None:
        text = self._banner("remediation", "reject")
        self.assertIn("REJECTED", text)
        self.assertNotIn("APPROVED", text)

    def test_checkpoint_2_approve_continues_to_the_coder(self) -> None:
        self.assertIn("coder-agent", self._banner("coder", "approve"))


class AgentTimingTests(unittest.TestCase):
    """Every agent line used to say "(0.0s)": the renderer was passed a literal 0."""

    def _printed(self, fn, *args, **kwargs) -> str:
        from factory.interfaces.render import output

        with output.console.capture() as captured:
            fn(*args, **kwargs)
        return " ".join(captured.get().split())

    def test_real_duration_and_cost_are_printed(self) -> None:
        from factory.interfaces.render.run import print_run_node

        text = self._printed(print_run_node, "spec-agent", {"spec": {"verdict": "pass"}},
                             duration=52.2, cost=0.16)
        self.assertIn("PASS (52.2s · $0.16)", text)
        self.assertNotIn("0.0s", text)

    def test_unknown_duration_is_omitted_not_zero(self) -> None:
        from factory.interfaces.render.run import print_resume_node

        text = self._printed(print_resume_node, "architect-agent",
                             {"architect": {"verdict": "pass"}})
        self.assertIn("PASS", text)
        self.assertNotIn("0.0s", text)

    def test_an_agent_announces_itself_when_it_starts(self) -> None:
        from factory.interfaces.render.run import print_agent_start

        self.assertIn("coder-agent running…", self._printed(print_agent_start, "coder-agent"))

    def test_an_agent_start_shows_its_detail(self) -> None:
        from factory.interfaces.render.run import print_agent_start

        text = self._printed(print_agent_start, "coder-agent", "task 2/5 T-0002 Routes (attempt 1)")
        self.assertIn("coder-agent running… task 2/5 T-0002 Routes (attempt 1)", text)

    def test_run_header_says_when_tests_will_not_execute(self) -> None:
        from factory.interfaces.render.run import print_run_started
        from factory.runs import RunStarted

        off = self._printed(print_run_started, RunStarted(1, "US-1", "req"))
        self.assertIn("tests will not execute (set FACTORY_RUN_TESTS=1 to run them)", off)
        on = self._printed(print_run_started, RunStarted(1, "US-1", "req", tests_run=True))
        self.assertNotIn("tests will not execute", on)


class TrustPackageCoverageTests(unittest.TestCase):
    """`factory review` said "AC covered: 3" before any tester had run: it counted
    the criteria, every one of them still unassessed."""

    def _pkg(self, statuses: list[str], covered: int) -> dict:
        return {
            "verdict": "warn", "next_authorization": "fix",
            "tests": {"passed": False, "ac_coverage": [
                {"criterion": f"ac {i}", "covered_by": [], "status": s}
                for i, s in enumerate(statuses)]},
            "ac_traceability": {"total": len(statuses), "covered": covered,
                                "flagged_missing": [], "unassessed": []},
            "diff": {"files": []}, "adr": {"path": ""},
            "security_boundary": {"overall": "not_applicable", "highest_severity": "none",
                                  "findings": []},
            "cost": {"usd": 0.0, "tokens_in": 0, "tokens_out": 0},
        }

    def _printed(self, pkg: dict) -> str:
        from factory.interfaces.render import output
        from factory.interfaces.render.review import _print_trust_package

        with output.console.capture() as captured:
            _print_trust_package(pkg, [])
        return " ".join(captured.get().split())

    def test_before_the_tester_ran_coverage_is_not_yet_measured(self) -> None:
        text = self._printed(self._pkg(["unassessed"] * 3, covered=0))
        self.assertIn("AC covered: not yet measured", text)

    def test_after_the_tester_ran_it_is_covered_of_total(self) -> None:
        text = self._printed(self._pkg(["covered", "covered", "unassessed"], covered=2))
        self.assertIn("AC covered: 2/3", text)
