"""Checkpoint 1 as policy: `gates.gate_after_spec` parks for the operator.

`spec.questions` — the ONE place where the playbook's Stage-1 clarifying-question
round-trip belongs, and the one thing only a human can answer — used to be
appended to `failures` and killed the run. EFFECTIVENESS.md §2 and REVIEW_QUEUE.md
both specify Checkpoint 1 (spec sign-off) for exactly this. These are the pure
gate decisions; the graph wiring and persistence live in
tests/pipeline/test_checkpoint_1.py.
"""

from __future__ import annotations

import unittest

from factory.domain import ambiguity, gates
from factory.domain.contracts import SpecOutput


def _spec(**overrides) -> SpecOutput:
    data = {
        "title": "Bookmark search",
        "problem": "no way to find a bookmark",
        "why": "the list is unusable past 50 entries",
        "acceptance_criteria": ["search matches on title", "results are paginated"],
        "tasks": [{"id": "T-1", "title": "add search", "purpose": "p",
                   "scope": ["src/bookmarks/"], "completion_evidence": "test passes"}],
        "verdict": "pass",
        "questions": [],
    }
    data.update(overrides)
    return SpecOutput.model_validate(data)


class GateAfterSpecTests(unittest.TestCase):
    def test_clean_spec_passes_without_a_checkpoint(self) -> None:
        result = gates.gate_after_spec(_spec())
        self.assertTrue(result.passed)
        self.assertFalse(result.needs_human)

    def test_open_questions_park_for_the_operator_instead_of_failing(self) -> None:
        result = gates.gate_after_spec(_spec(questions=["Which auth model — JWT or session?"]))
        self.assertTrue(
            result.passed,
            "an otherwise well-formed story with questions is not a malformed story",
        )
        self.assertTrue(result.needs_human)
        self.assertTrue(result.human_questions)
        self.assertIn("JWT or session", "\n".join(result.human_questions))

    def test_structural_failure_still_fails_even_with_questions(self) -> None:
        """A malformed story must not be laundered into a checkpoint: there is
        nothing for the operator to sign off on."""
        result = gates.gate_after_spec(_spec(acceptance_criteria=["only one"],
                                             questions=["and also, which db?"]))
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)

    def test_scope_explosion_still_fails(self) -> None:
        many = [{"id": f"T-{i}", "title": "t", "purpose": "p"} for i in range(1, 9)]
        self.assertFalse(gates.gate_after_spec(_spec(tasks=many)).passed)


class AgentAmbiguityContractTests(unittest.TestCase):
    """The gate must honour the contract the agent was actually GIVEN.

    `.opencode/agents/spec-agent.md` instructs: "If the request is ambiguous, set
    `verdict: fail` and put your questions in a `questions` array." But
    `gate_after_spec` checked `verdict != "pass"` first and rejected the run as
    structurally malformed, so the Checkpoint-1 park — which only fired when the
    verdict PASSED — could never trigger for the one case it exists to serve.

    Found by a live run of the challenge's Story 10, where the spec-agent
    correctly refused to invent a definition of "overdue" and asked four precise
    product questions. The pipeline discarded all of it and reported
    "Spec verdict is 'fail', not 'pass'". The replay probe missed this because
    the case was hand-authored with `verdict: "pass"` — it encoded an assumption
    about the agent instead of the agent's real contract.
    """

    def test_verdict_fail_WITH_questions_is_a_checkpoint_not_a_rejection(self) -> None:
        result = gates.gate_after_spec(_spec(
            verdict="fail",
            questions=[
                "What exact rule defines an overdue ticket: age since creation, "
                "time since last update, due date field, or SLA by priority?",
                "Where must the highlighting appear?",
            ],
        ))
        self.assertTrue(
            result.passed,
            "an agent following its own ambiguity contract must not be treated as "
            "having produced a malformed story",
        )
        self.assertTrue(result.needs_human)
        self.assertIn("overdue", "\n".join(result.human_questions))

    def test_verdict_blocked_with_questions_also_parks(self) -> None:
        result = gates.gate_after_spec(_spec(verdict="blocked", questions=["which db?"]))
        self.assertTrue(result.passed)
        self.assertTrue(result.needs_human)

    def test_verdict_fail_WITHOUT_questions_is_still_a_rejection(self) -> None:
        """A bare 'fail' with nothing to ask is a genuine refusal, not a
        checkpoint — there is nothing for the operator to answer."""
        result = gates.gate_after_spec(_spec(verdict="fail", questions=[]))
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)
        self.assertIn("verdict", result.reason.lower())

    def test_a_structurally_broken_story_is_rejected_even_with_questions(self) -> None:
        """Ambiguity does not excuse a malformed story: with one acceptance
        criterion there is nothing coherent to sign off on."""
        result = gates.gate_after_spec(_spec(
            verdict="fail", acceptance_criteria=["only one"], questions=["which db?"],
        ))
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)

    def test_the_real_story_10_output_parks(self) -> None:
        """Verbatim shape of what the spec-agent returned on the live run."""
        result = gates.gate_after_spec(SpecOutput.model_validate({
            "title": "Highlight overdue high-priority tickets",
            "type": "feature",
            "problem": "Support managers have no explicit way to identify "
                       "high-priority tickets that are overdue.",
            "why": "important cases are forgotten",
            "acceptance_criteria": [
                "The system identifies high-priority tickets as overdue according "
                "to a clearly defined overdue rule.",
                "Overdue high-priority tickets are highlighted in the support agent "
                "ticket list.",
            ],
            "non_goals": ["Authentication or user management"],
            "tasks": [
                {"id": "T-0001", "title": "Define overdue rule and API contract",
                 "purpose": "p", "scope": [], "completion_evidence": "e"},
                {"id": "T-0002", "title": "Expose overdue flag in ticket listing",
                 "purpose": "p", "scope": [], "completion_evidence": "e"},
                {"id": "T-0003", "title": "Render overdue highlight in frontend",
                 "purpose": "p", "scope": [], "completion_evidence": "e"},
            ],
            "verdict": "fail",
            "questions": [
                "What exact rule defines an overdue ticket?",
                "Where must the highlighting appear?",
                "What visual treatment counts as highlighted?",
                "Should overdue status be filterable or searchable?",
            ],
        }))
        self.assertTrue(result.passed, result.reason)
        self.assertTrue(result.needs_human)
        self.assertIn("4 open question", result.reason)
        # BOTH triggers fire on the real output: the agent asked, and the
        # deterministic check independently found "overdue" carrying no number.
        # That redundancy is the point — the deterministic half holds when the
        # agent's half does not (which a live re-run showed happens).
        self.assertEqual(len(result.human_questions), 2)
        joined = "\n".join(result.human_questions)
        self.assertIn("What exact rule defines an overdue ticket?", joined)
        self.assertIn("UNAUTHORISED THRESHOLD", joined)
        self.assertIn("undefined threshold", result.reason)


class UnboundCriteriaGateTests(unittest.TestCase):
    """An unbound criterion PARKS the story — it is a question, not a defect."""

    def test_gate_parks_and_names_the_undefined_term(self) -> None:
        result = gates.gate_after_spec(_spec(acceptance_criteria=[
            "Overdue high-priority tickets are highlighted in the list",
            "The highlight is visible on the dashboard",
        ]))
        self.assertTrue(result.passed, "an under-specified story is not malformed")
        self.assertTrue(result.needs_human)
        joined = "\n".join(result.human_questions)
        self.assertIn("overdue", joined.lower())
        self.assertIn("Overdue high-priority tickets", joined)

    def test_a_well_specified_story_still_needs_no_human(self) -> None:
        result = gates.gate_after_spec(_spec())
        self.assertTrue(result.passed)
        self.assertFalse(result.needs_human)

    def test_the_agents_own_questions_and_the_deterministic_check_combine(self) -> None:
        result = gates.gate_after_spec(_spec(
            verdict="fail",
            acceptance_criteria=["Overdue tickets are highlighted", "It looks nice"],
            questions=["Where should the highlight appear?"],
        ))
        self.assertTrue(result.passed)
        self.assertTrue(result.needs_human)
        joined = "\n".join(result.human_questions)
        self.assertIn("Where should the highlight appear?", joined)
        self.assertIn("overdue", joined.lower())

    def test_settled_terms_do_not_park_the_gate(self) -> None:
        result = gates.gate_after_spec(
            _spec(acceptance_criteria=["Overdue tickets are counted on the dashboard",
                                       "Counts are correct for an empty database"]),
            defined_terms=frozenset({"overdue"}),
        )
        self.assertFalse(result.needs_human)

    def test_a_structural_failure_still_wins_over_the_ambiguity_park(self) -> None:
        result = gates.gate_after_spec(_spec(
            acceptance_criteria=["Overdue tickets are highlighted"],  # only 1 AC
        ))
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)


class InventedThresholdTests(unittest.TestCase):
    """An INVENTED threshold must park too, not just a missing one.

    The first version of this check keyed only off the acceptance criteria, so a
    criterion carrying a number was "bound" and sailed through — even when the
    request never supplied that number. Observed live: the request said only
    "overdue high-priority tickets", and one run wrote "created_at is older than
    48 hours" while an earlier run's architect had chosen 24. Both are
    undocumented business rules, both unapproved; writing the number down makes
    the invention visible but does not make it authorised.

    So the question is asked of the REQUEST: if the operator used a threshold
    term without a number, the definition needs sign-off however the agent
    resolves it.
    """

    REQUEST = ("As a support manager, I want overdue high-priority tickets "
               "highlighted so that important cases are not forgotten.")

    def test_a_number_the_request_never_supplied_is_flagged(self) -> None:
        found = ambiguity.unbound_criteria(
            ["A ticket is overdue when priority is HIGH and created_at is older "
             "than 48 hours."],
            request=self.REQUEST,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], "overdue")

    def test_a_threshold_the_request_DID_supply_is_not_flagged(self) -> None:
        """Story 11 hands over the definition, so nothing needs approving."""
        self.assertEqual(
            ambiguity.unbound_criteria(
                ["A HIGH ticket is overdue when status != CLOSED and createdAt is "
                 "more than 24 hours ago."],
                request="A HIGH-priority ticket is overdue when status != CLOSED "
                        "and createdAt is more than 24 hours ago.",
            ),
            [],
        )

    def test_an_operator_settled_term_is_not_flagged_even_from_the_request(self) -> None:
        self.assertEqual(
            ambiguity.unbound_criteria(
                ["Overdue tickets are counted on the dashboard"],
                request=self.REQUEST,
                defined_terms=frozenset({"overdue"}),
            ),
            [],
        )

    def test_a_term_absent_from_the_request_is_still_judged_on_the_criterion(self) -> None:
        """The agent introducing a NEW vague qualifier of its own must still be
        caught — the request-based rule widens the net, it does not replace it."""
        found = ambiguity.unbound_criteria(
            ["Stale tickets are archived"],
            request="I want a dashboard showing ticket counts",
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], "stale")

    def test_the_message_distinguishes_invented_from_missing(self) -> None:
        result = gates.gate_after_spec(
            _spec(acceptance_criteria=[
                "A ticket is overdue when created_at is older than 48 hours.",
                "Overdue tickets are highlighted in the list.",
            ]),
            request=self.REQUEST,
        )
        self.assertTrue(result.needs_human)
        joined = "\n".join(result.human_questions)
        self.assertIn("48 hours", joined)
        self.assertIn("never", joined.lower())

    def test_no_request_falls_back_to_criterion_only_judgement(self) -> None:
        self.assertEqual(
            ambiguity.unbound_criteria(
                ["A ticket is overdue after 24 hours"], request=""
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
