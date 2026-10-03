"""The intake interview's deterministic half: the topic checklist and answer resolution.

Coverage is decided from the recorded answers, never from the model saying "done".
"""

from __future__ import annotations

import unittest

from factory.domain import gates
from factory.domain.interview import (
    FALLBACK_QUESTIONS,
    REQUIRED_TOPICS,
    TOPIC_TITLES,
    InterviewOption,
    InterviewQuestion,
    InterviewTurn,
    StoryClarification,
    fallback_questions,
    with_clarifications,
    resolve_answer,
    uncovered_topics,
)


def _q(*labels: str) -> InterviewQuestion:
    return InterviewQuestion(
        topic="users", question="Who uses it?",
        options=[InterviewOption(label=label) for label in labels],
    )


class TopicChecklistTests(unittest.TestCase):
    def test_required_topics_are_the_agreed_nine(self) -> None:
        self.assertEqual(
            REQUIRED_TOPICS,
            ("goal", "users", "must_do", "must_not_do", "data", "errors", "success",
             "screens", "security"),
        )

    def test_every_required_topic_has_a_title_and_a_fallback_question(self) -> None:
        for topic in REQUIRED_TOPICS:
            self.assertTrue(TOPIC_TITLES[topic])
            self.assertTrue(FALLBACK_QUESTIONS[topic])
        self.assertEqual(TOPIC_TITLES["must_not_do"], "What it must NOT do")
        self.assertEqual(TOPIC_TITLES["stack"], "Tech stack")
        self.assertEqual(TOPIC_TITLES["correction"], "Operator corrections")

    def test_nothing_answered_leaves_every_topic_uncovered_in_order(self) -> None:
        self.assertEqual(uncovered_topics([]), list(REQUIRED_TOPICS))

    def test_an_answered_topic_is_covered_and_extras_do_not_count(self) -> None:
        answers = [
            {"topic": "users", "answer": "Clerks"},
            {"topic": "stack", "answer": "Python"},
            {"topic": "goal", "answer": "   "},
        ]
        missing = uncovered_topics(answers)
        self.assertNotIn("users", missing)
        self.assertIn("goal", missing)  # a blank answer covers nothing
        self.assertEqual(len(missing), len(REQUIRED_TOPICS) - 1)

    def test_fallback_questions_are_plain_open_questions_for_the_topics(self) -> None:
        qs = fallback_questions(["data", "goal"])
        self.assertEqual([q.topic for q in qs], ["data", "goal"])
        self.assertEqual(qs[0].question, FALLBACK_QUESTIONS["data"])
        self.assertEqual(qs[0].options, [])

    def test_the_question_cap_is_a_named_gate_constant(self) -> None:
        self.assertEqual(gates.MAX_INTERVIEW_QUESTIONS, 40)


class TurnContractTests(unittest.TestCase):
    def test_turn_defaults_and_nested_parsing(self) -> None:
        self.assertEqual(InterviewTurn().questions, [])
        self.assertFalse(InterviewTurn().done)
        turn = InterviewTurn.model_validate({
            "questions": [{"topic": "goal", "question": "Why?",
                           "options": [{"label": "A"}]}],
        })
        self.assertEqual(turn.questions[0].options[0].description, "")


class ResolveAnswerTests(unittest.TestCase):
    def test_you_decide_takes_the_first_option_and_is_marked_assumed(self) -> None:
        for raw in ("you decide", " You Decide ", "d", "D"):
            self.assertEqual(resolve_answer(_q("Clerks", "Admins"), raw), ("Clerks", True))

    def test_you_decide_without_options_is_left_to_the_factory(self) -> None:
        self.assertEqual(
            resolve_answer(_q(), "you decide"), ("(left to the factory to decide)", True)
        )

    def test_a_number_picks_that_option(self) -> None:
        self.assertEqual(resolve_answer(_q("Clerks", "Admins"), " 2 "), ("Admins", False))

    def test_a_picked_option_keeps_its_description(self) -> None:
        # The brief and every later turn see only the recorded text: a label alone
        # ("Name, kind and a daily goal") drops what the operator actually chose.
        q = InterviewQuestion(topic="must_do", question="Setup?", options=[
            InterviewOption(label="Daily goal", description="the day counts once reached"),
            InterviewOption(label="Name only")])
        self.assertEqual(resolve_answer(q, "1"), ("Daily goal — the day counts once reached", False))
        self.assertEqual(resolve_answer(q, "d"), ("Daily goal — the day counts once reached", True))
        self.assertEqual(resolve_answer(q, "2"), ("Name only", False))

    def test_an_out_of_range_number_is_free_text(self) -> None:
        self.assertEqual(resolve_answer(_q("Clerks", "Admins"), "3"), ("3", False))
        self.assertEqual(resolve_answer(_q("Clerks"), "0"), ("0", False))
        self.assertEqual(resolve_answer(_q(), "1"), ("1", False))

    def test_free_text_is_stripped_and_empty_stays_empty(self) -> None:
        self.assertEqual(resolve_answer(_q("Clerks"), "  both of them "), ("both of them", False))
        self.assertEqual(resolve_answer(_q("Clerks"), "   "), ("", False))


class StoryClarificationTests(unittest.TestCase):
    def test_new_caps_are_named_gate_constants(self) -> None:
        self.assertEqual(gates.MAX_STACK_PROPOSALS, 3)
        self.assertEqual(gates.MAX_STORY_INTERVIEW_QUESTIONS, 8)

    def test_nothing_asked_leaves_the_request_unchanged(self) -> None:
        self.assertEqual(with_clarifications("Add login", []), "Add login")

    def test_answers_are_appended_and_assumptions_marked(self) -> None:
        text = with_clarifications("Add login", [
            StoryClarification("How many tries?", "5", False),
            StoryClarification("Lockout?", "15 minutes", True),
        ])
        self.assertEqual(
            text,
            "Add login\n\n## Operator clarifications\n"
            "- How many tries? — 5\n"
            "- Lockout? — 15 minutes (assumption — the operator said \"you decide\")",
        )


if __name__ == "__main__":
    unittest.main()
