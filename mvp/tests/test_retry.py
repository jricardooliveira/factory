"""A transient failure must not strand the operator's decision.

Observed live: the operator answered a Checkpoint-1 question with `factory reject
20 "..."`. The answer was recorded correctly and the re-specify path fired — then
the model provider returned "Unexpected server error" and the run ended `blocked`.

At that point the decision was unreachable: `approve`/`reject` only accept a run
whose status is `waiting_human`, and this one no longer was. The operator's
answer sat in `gate_results.human_response` with nothing able to read it. An
interruption is the scarcest thing this factory spends (EFFECTIVENESS §1: "trust
per interruption") — losing one to someone else's 500 is unacceptable.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.state import db


class AnsweredCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_with_answer(self, response: str | None, status: str = "blocked") -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0020", "Overdue", "highlight overdue tickets")
            rid = db.start_run(conn, "US-0020")
            db.log_agent(conn, rid, "spec-agent", "in",
                         json.dumps({"title": "Overdue", "problem": "p", "why": "w",
                                     "acceptance_criteria": ["a", "b"], "tasks": []}),
                         verdict="pass")
            gid = db.log_gate(conn, rid, "gate-1-spec", True, "parked",
                              needs_human=True, human_questions="what is overdue?")
            if response is not None:
                db.respond_to_gate(conn, gid, response)
            db.finish_run(conn, rid, status, error="provider blew up")
        return rid

    def test_the_last_answered_checkpoint_is_recoverable(self) -> None:
        from factory.state.db import get_answered_human_gate

        rid = self._run_with_answer("REJECTED: overdue means HIGH, not closed, >24h")
        with db.get_db(self.db_path) as conn:
            gate = get_answered_human_gate(conn, rid)
        self.assertIsNotNone(gate, "the operator's answer must be findable again")
        self.assertEqual(gate["gate_name"], "gate-1-spec")
        self.assertIn("overdue means HIGH", gate["human_response"])

    def test_an_unanswered_checkpoint_is_not_returned(self) -> None:
        from factory.state.db import get_answered_human_gate

        rid = self._run_with_answer(None)
        with db.get_db(self.db_path) as conn:
            self.assertIsNone(get_answered_human_gate(conn, rid))

    def test_the_most_recent_answer_wins(self) -> None:
        """After several checkpoints, the retry must resume from the LATEST — the
        same 'read the newest log' rule that resume already follows."""
        from factory.state.db import get_answered_human_gate

        rid = self._run_with_answer("REJECTED: first answer")
        with db.get_db(self.db_path) as conn:
            gid = db.log_gate(conn, rid, "gate-2-architect", True, "parked",
                              needs_human=True, human_questions="approve design?")
            db.respond_to_gate(conn, gid, "APPROVED: second answer")
            gate = get_answered_human_gate(conn, rid)
        self.assertEqual(gate["gate_name"], "gate-2-architect")
        self.assertIn("second answer", gate["human_response"])


class RetryDecisionTests(unittest.TestCase):
    """Recovering the ACTION from a recorded answer."""

    def test_a_rejection_is_recovered_as_a_rejection(self) -> None:
        from factory.cli import decision_from_response

        action, feedback = decision_from_response(
            "REJECTED: overdue means HIGH, not closed, older than 24 hours")
        self.assertEqual(action, "reject")
        self.assertEqual(feedback, "overdue means HIGH, not closed, older than 24 hours")

    def test_an_approval_is_recovered_as_an_approval(self) -> None:
        from factory.cli import decision_from_response

        action, feedback = decision_from_response("APPROVED: looks fine")
        self.assertEqual(action, "approve")
        self.assertEqual(feedback, "looks fine")

    def test_an_unrecognised_response_is_treated_as_an_approval_note(self) -> None:
        """Never guess a REJECTION — that would silently re-run work the operator
        may have accepted."""
        from factory.cli import decision_from_response

        action, _ = decision_from_response("some free text")
        self.assertEqual(action, "approve")


if __name__ == "__main__":
    unittest.main()
