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


if __name__ == "__main__":
    unittest.main()
