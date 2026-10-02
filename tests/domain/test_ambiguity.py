"""Threshold-term detection (`factory.domain.ambiguity`): pure, deterministic policy.

Split out of the Checkpoint-1 suite to mirror the package; the gate that consumes
it is pinned in tests/domain/test_gate_after_spec.py and its pipeline wiring in
tests/pipeline/test_checkpoint_1.py.
"""

from __future__ import annotations

import unittest

from factory.domain import ambiguity


class UnboundCriteriaTests(unittest.TestCase):
    """Deterministically catch a criterion that requires inventing a threshold.

    The live test showed ambiguity detection is a coin flip: the same Story 10
    request produced four precise questions on one run and `verdict: pass` on the
    next. For a factory whose premise is "policy is deterministic Python, never
    delegated to an LLM", whether an under-specified requirement gets flagged is
    the wrong thing to leave to the model's mood.

    The check is deliberately NARROW. It looks only for terms that oblige the
    implementation to invent a NUMBER it was never given — the class of silent
    business-rule invention Story 10 exists to test. Quality adjectives
    ("appropriate tests", "reasonable error handling") are excluded on purpose:
    the challenge uses "appropriate" in eight of its fifteen stories, so flagging
    those would park nearly every story and train the operator to click through —
    the crying-wolf failure this whole check exists to avoid.
    """

    def test_the_real_story_10_criterion_is_flagged(self) -> None:
        found = ambiguity.unbound_criteria([
            "Overdue high-priority tickets are highlighted in the ticket list",
        ])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], "overdue")

    def test_a_criterion_carrying_its_threshold_is_not_flagged(self) -> None:
        """Story 11 supplies the definition, so it must sail through."""
        self.assertEqual(
            ambiguity.unbound_criteria([
                "A HIGH-priority ticket is overdue when status != CLOSED and "
                "createdAt is more than 24 hours ago",
            ]),
            [],
        )

    def test_spelled_out_numbers_and_units_count_as_bound(self) -> None:
        for criterion in (
            "tickets with no update for seven days are marked stale",
            "a search returning more than fifty results is paginated",
            "requests slower than 200ms are logged",
        ):
            with self.subTest(criterion=criterion):
                self.assertEqual(ambiguity.unbound_criteria([criterion]), [])

    def test_quality_adjectives_are_deliberately_not_flagged(self) -> None:
        """These are review-policy concerns (REVIEW.md + the tester), not
        unstated business rules — and the challenge is full of them."""
        self.assertEqual(
            ambiguity.unbound_criteria([
                "Appropriate unit tests exist",
                "Appropriate database/API integration tests exist",
                "API errors use a consistent response format",
                "Invalid input returns an appropriate HTTP 400 response",
                "Frontend provides a loading state",
                "Empty ticket collections are handled correctly",
            ]),
            [],
        )

    def test_the_challenge_story_1_criteria_all_pass_cleanly(self) -> None:
        """A well-specified story must never trip this check."""
        self.assertEqual(
            ambiguity.unbound_criteria([
                "Title is mandatory",
                "Description is mandatory",
                "Priority must be one of LOW, MEDIUM, HIGH",
                "Status defaults to OPEN",
                "Ticket is persisted in SQLite",
                "Invalid input returns an appropriate HTTP 400 response",
            ]),
            [],
        )

    def test_a_term_already_settled_in_project_memory_is_not_re_litigated(self) -> None:
        """Once Story 11 defines 'overdue' in a committed ADR, Story 13's
        dashboard criterion must not park again — the factory does not
        re-litigate settled decisions (EFFECTIVENESS §7)."""
        self.assertEqual(
            ambiguity.unbound_criteria(
                ["The dashboard shows the number of overdue HIGH or URGENT tickets"],
                defined_terms=frozenset({"overdue"}),
            ),
            [],
        )

    def test_defined_terms_are_extracted_from_committed_project_context(self) -> None:
        from factory.domain.ambiguity import defined_threshold_terms

        context = (
            "## Prior Architecture Decisions\n\n"
            "ADR-US-0011: a HIGH ticket is OVERDUE when it is not CLOSED and was "
            "created more than 24 hours ago.\n"
        )
        self.assertIn("overdue", defined_threshold_terms(context))
        self.assertNotIn("stale", defined_threshold_terms(context))

    def test_empty_context_defines_nothing(self) -> None:
        from factory.domain.ambiguity import defined_threshold_terms

        self.assertEqual(defined_threshold_terms(""), frozenset())


class UnitMatchingTests(unittest.TestCase):
    """Units must match as WORDS, never as substrings.

    The first implementation tested `unit in criterion.lower()`, so "nu**mb**er"
    matched the megabyte unit "mb" and the criterion was judged to carry a
    threshold it did not have. "ms" is worse: it matches almost any plural ending
    in -ms — "items", "terms", "problems", "forms" — which would have silently
    disabled this check across most real acceptance criteria. A check that
    quietly stops checking is the worst kind.
    """

    def test_a_unit_hidden_inside_another_word_does_not_count_as_bound(self) -> None:
        for criterion in (
            "The dashboard shows the number of overdue HIGH tickets",   # nu(mb)er
            "Overdue items are listed first",                          # ite(ms)
            "Stale search terms are highlighted",                      # ter(ms)
            "Recent problems appear at the top",                       # proble(ms)
        ):
            with self.subTest(criterion=criterion):
                found = ambiguity.unbound_criteria([criterion])
                self.assertEqual(
                    len(found), 1,
                    f"a unit hidden inside a word must not bind: {criterion}",
                )

    def test_a_real_unit_as_its_own_word_does_count_as_bound(self) -> None:
        for criterion in (
            "tickets with no update for seven days are stale",
            "requests slower than 200 ms are logged",
            "uploads larger than 5 mb are rejected",
            "a ticket is overdue after twenty four hours",
        ):
            with self.subTest(criterion=criterion):
                self.assertEqual(ambiguity.unbound_criteria([criterion]), [])

    def test_plural_units_bind_too(self) -> None:
        self.assertEqual(
            ambiguity.unbound_criteria(["a ticket is overdue after two weeks"]), []
        )


if __name__ == "__main__":
    unittest.main()
