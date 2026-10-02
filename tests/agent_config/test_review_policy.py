"""The review policy must be a versioned file the tester actually reads.

The playbook's Stage-5 play: "the tech lead writes REVIEW.md defining the review
passes, the importance levels, and what to skip." Here those passes lived in prose
inside `tester-agent.md` while the blocking severity threshold lived in `gates.py`
— two homes for one policy, free to drift, and neither tunable without editing an
agent definition.

A policy file nothing consumes is decoration, so these tests pin the wiring, not
just the file.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.agent_config import review_policy


class PolicyFileTests(unittest.TestCase):
    def test_the_shipped_policy_loads(self) -> None:
        text = review_policy.load_review_policy()
        self.assertTrue(text.strip())

    def test_the_shipped_policy_covers_every_sub_verdict(self) -> None:
        """Each tester sub-verdict needs a pass, or the tester is guessing."""
        text = review_policy.load_review_policy()
        for token in ("qa_verdict", "security_verdict", "performance_verdict"):
            self.assertIn(token, text, f"no review pass maps to {token}")

    def test_the_shipped_policy_declares_a_skip_list_and_a_nit_cap(self) -> None:
        text = review_policy.load_review_policy()
        self.assertIn("What to skip", text)
        self.assertIn("Nit cap", text)

    def test_the_severity_ladder_matches_the_blocking_gate(self) -> None:
        """`gate_after_tester` blocks on high/critical. If the doc and the gate
        disagree, the tester is told one thing and judged by another."""
        text = review_policy.load_review_policy().lower()
        self.assertIn("critical", text)
        self.assertIn("high", text)
        # The gate's actual thresholds, read from the code rather than restated.
        from factory.domain.gates import gate_after_tester
        from factory.domain.contracts import TesterOutput

        for severity in ("high", "critical"):
            verdict = gate_after_tester(TesterOutput(
                overall="pass", qa_verdict="pass", security_verdict="pass",
                highest_severity=severity, performance_verdict="pass",
            ))
            self.assertFalse(verdict.passed, f"{severity} must block")
        for severity in ("low", "medium"):
            verdict = gate_after_tester(TesterOutput(
                overall="pass", qa_verdict="pass", security_verdict="pass",
                highest_severity=severity, performance_verdict="pass",
            ))
            self.assertTrue(verdict.passed, f"{severity} must not block")

    def test_a_missing_policy_file_degrades_to_empty_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(review_policy.load_review_policy(Path(tmp) / "nope.md"), "")

    def test_policy_block_is_empty_when_there_is_no_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(review_policy.policy_block(Path(tmp) / "nope.md"), "")

    def test_policy_block_is_a_labelled_prompt_section(self) -> None:
        block = review_policy.policy_block()
        self.assertIn("## Review policy", block)
        self.assertIn("Nit cap", block)


class TesterWiringTests(unittest.TestCase):
    """The tester's prompt must actually carry the policy."""

    def test_the_tester_prompt_includes_the_review_policy(self) -> None:
        from factory.pipeline import build_tester_prompt

        prompt = build_tester_prompt({
            "spec": {"title": "T", "acceptance_criteria": ["a"]},
            "architect": {"verdict": "pass"},
            "coder": {"verdict": "complete"},
            "gate_build": {"passed": True},
            "opencode_cwd": ".",
        })
        self.assertIn("## Review policy", prompt)
        self.assertIn("Nit cap", prompt)
        # And it still carries what it carried before.
        self.assertIn("## Story", prompt)
        self.assertIn("## Architecture", prompt)


if __name__ == "__main__":
    unittest.main()
