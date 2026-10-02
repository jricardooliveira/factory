"""The backlog-agent's output contract and its revision budget."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from factory.domain import gates
from factory.domain.backlog import BacklogOutput, BacklogStory


class BacklogContractTests(unittest.TestCase):
    def test_a_story_needs_title_and_request_rationale_is_optional(self) -> None:
        out = BacklogOutput.model_validate(
            {"stories": [{"title": "Skeleton", "request": "Build the empty app."}]}
        )
        self.assertEqual(out.stories, [BacklogStory(title="Skeleton", request="Build the empty app.")])
        self.assertEqual(out.stories[0].rationale, "")
        with self.assertRaises(ValidationError):
            BacklogOutput.model_validate({"stories": [{"title": "No request"}]})

    def test_revision_budget_is_a_named_constant(self) -> None:
        self.assertEqual(gates.MAX_BACKLOG_REVISIONS, 5)
