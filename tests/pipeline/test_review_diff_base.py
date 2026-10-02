"""Every diff an agent reviews starts at THIS run's base commit (review task T05).

The tester, the release notes and a remediation pass all read `collect_repo_diff`;
without the run's base it starts at the first factory commit, so a later story's
review is mostly earlier stories' code.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from factory.pipeline import build_tester_prompt
from factory.pipeline.prompts.release import build_release_prompt

STATE = {"spec": {}, "architect": {}, "opencode_cwd": "/repo", "base_commit": "abc123",
         "gate_build": {}}


class ReviewDiffBaseTests(unittest.TestCase):
    def test_the_tester_reviews_from_the_runs_base(self) -> None:
        with patch("factory.pipeline.prompts.tester.collect_repo_diff", return_value="") as diff:
            build_tester_prompt(dict(STATE))
        self.assertEqual(diff.call_args.kwargs.get("base"), "abc123")

    def test_the_release_notes_describe_the_runs_change(self) -> None:
        with patch("factory.pipeline.prompts.release.collect_repo_diff", return_value="") as diff:
            build_release_prompt(dict(STATE), [])
        self.assertEqual(diff.call_args.kwargs.get("base"), "abc123")


if __name__ == "__main__":
    unittest.main()
