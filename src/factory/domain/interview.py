"""The intake interview's deterministic half: what must be asked, and what an answer means.

The interview-agent only PROPOSES questions. Whether the product is defined well
enough to brief is decided here, from the answers actually recorded — a model that
says "done" with topics still open is simply asked the fallback questions.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, NamedTuple

from pydantic import BaseModel

REQUIRED_TOPICS: tuple[str, ...] = (
    "goal", "users", "must_do", "must_not_do", "data", "errors", "success", "screens",
    "security",
)

# Anything not listed here is grouped under OTHER_TITLE in the brief.
TOPIC_TITLES: dict[str, str] = {
    "goal": "Goal",
    "users": "Users",
    "must_do": "What it must do",
    "must_not_do": "What it must NOT do",
    "data": "Data",
    "errors": "Errors and edge cases",
    "success": "Success criteria",
    "screens": "Screens and flows",
    "security": "Security and access",
    "stack": "Tech stack",
    "correction": "Operator corrections",
}
OTHER_TITLE = "Other"

# Plain open questions, asked by Python when the agent stops (or the cap is hit)
# with a required topic still unanswered — the minimum is never waived.
FALLBACK_QUESTIONS: dict[str, str] = {
    "goal": "In one or two sentences, what is this product for and what problem does it solve?",
    "users": "Who will use it, and what is each kind of user trying to get done?",
    "must_do": "What are the things it absolutely must do?",
    "must_not_do": "What must it never do, or what is explicitly out of scope?",
    "data": "What information does it keep or handle, and where does that come from?",
    "errors": "What can go wrong, and what should happen when it does?",
    "success": "How will you know it works — what would you check to call it done?",
    "screens": "What does the user see and do, step by step (screens, commands or pages)?",
    "security": "Who may see or change what, and is any of the data sensitive?",
}

LEFT_TO_FACTORY = "(left to the factory to decide)"


class InterviewOption(BaseModel):
    label: str
    description: str = ""


class InterviewQuestion(BaseModel):
    topic: str
    question: str
    options: list[InterviewOption] = []


class InterviewTurn(BaseModel):
    questions: list[InterviewQuestion] = []
    done: bool = False


class ImportedAnswer(BaseModel):
    """One answer from an interview held elsewhere (the /factory-intake skill)."""

    topic: str
    question: str
    options: list[str] = []
    answer: str
    assumed: bool = False


class StoryClarification(NamedTuple):
    question: str
    answer: str
    assumed: bool


def with_clarifications(request: str, clarifications: list[StoryClarification]) -> str:
    """The story request plus what the operator settled for it in the story interview."""
    if not clarifications:
        return request
    lines = [
        f"- {c.question} — {c.answer}"
        + (' (assumption — the operator said "you decide")' if c.assumed else "")
        for c in clarifications
    ]
    return request + "\n\n## Operator clarifications\n" + "\n".join(lines)


def uncovered_topics(answers: Iterable[Mapping[str, Any]]) -> list[str]:
    """Required topics (in order) with no answered row."""
    covered = {a.get("topic") for a in answers if str(a.get("answer") or "").strip()}
    return [t for t in REQUIRED_TOPICS if t not in covered]


def fallback_questions(topics: Iterable[str]) -> list[InterviewQuestion]:
    return [InterviewQuestion(topic=t, question=FALLBACK_QUESTIONS[t]) for t in topics]


def resolve_answer(question: InterviewQuestion, raw: str) -> tuple[str, bool]:
    """(answer_text, assumed) for what the operator typed. "" means unanswered."""
    text = raw.strip()
    if text.lower() in ("you decide", "d"):
        # The agent lists its recommended option FIRST, so delegating takes that
        # one — and is recorded as an assumption, never as the operator's choice.
        if question.options:
            return question.options[0].label, True
        return LEFT_TO_FACTORY, True
    # isascii: str.isdigit accepts characters like "²" that int() rejects.
    if text.isascii() and text.isdigit() and 1 <= int(text) <= len(question.options):
        return question.options[int(text) - 1].label, False
    return text, False
