"""The interview's answers and model turns are stored per project, verbatim."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.state import db, interviews
from factory.state.projects import insert_project


class InterviewStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            for n in (1, 2):
                insert_project(conn, project_id=f"PROJ-00{n}", slug=f"p{n}", name=f"P{n}",
                               repo_path=f"projects/p{n}", spec_path=None)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_answers_round_trip_in_asked_order_per_project(self) -> None:
        with db.get_db(self.db_path) as conn:
            interviews.add_answer(conn, "PROJ-001", topic="goal", question="Why?",
                                  options=[], answer="Track stock", assumed=False)
            interviews.add_answer(conn, "PROJ-002", topic="goal", question="Why?",
                                  options=[], answer="Other project", assumed=False)
            interviews.add_answer(conn, "PROJ-001", topic="users", question="Who?",
                                  options=["Clerks", "Admins"], answer="Clerks", assumed=True)
            rows = interviews.list_answers(conn, "PROJ-001")
        self.assertEqual([r["topic"] for r in rows], ["goal", "users"])
        self.assertEqual(rows[0]["options"], [])
        self.assertIs(rows[0]["assumed"], False)
        self.assertEqual(rows[1]["options"], ["Clerks", "Admins"])
        self.assertIs(rows[1]["assumed"], True)
        self.assertEqual(rows[1]["answer"], "Clerks")
        self.assertTrue(rows[1]["created_at"])

    def test_no_answers_is_an_empty_list(self) -> None:
        with db.get_db(self.db_path) as conn:
            self.assertEqual(interviews.list_answers(conn, "PROJ-001"), [])

    def test_a_turn_is_logged_verbatim_with_its_usage(self) -> None:
        with db.get_db(self.db_path) as conn:
            interviews.log_turn(conn, "PROJ-001", prompt="ask", output_text='{"done": true}',
                                model_name="m", tokens_in=10, tokens_out=3, cost_usd=0.01,
                                duration_secs=1.5)
            interviews.log_turn(conn, "PROJ-001", prompt="ask again", output_text=None)
            rows = conn.execute("SELECT * FROM interview_turns ORDER BY id").fetchall()
        self.assertEqual(rows[0]["prompt"], "ask")
        self.assertEqual(rows[0]["output_text"], '{"done": true}')
        self.assertEqual(rows[0]["tokens_in"], 10)
        self.assertEqual(rows[0]["cost_usd"], 0.01)
        self.assertIsNone(rows[1]["model_name"])
        self.assertTrue(rows[1]["created_at"])


if __name__ == "__main__":
    unittest.main()
