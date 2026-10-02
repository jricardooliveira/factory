"""No model call is made once a user story has spent its $10 (operator decision).

Checked before EVERY live call at the single agent-call boundary — including a
JSON-repair retry inside a stage — and by the boss before a stage starts, which
blocks the run with the spend named. Replays spend nothing and are never refused.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.adapters.opencode import AgentResult
from factory.pipeline.agent_calls import BudgetExhausted, run_agent_json
from factory.pipeline.boss import authorized
from factory.state import db


class StoryBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "x")
            self.run_id = db.start_run(conn, "US-0001")
            db.log_gate(conn, self.run_id, "gate-1-spec", True, "ok")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _spent(self, output_tokens: int, model: str = "openai/gpt-5.5", run_id=None) -> None:
        with db.get_db(self.db_path) as conn:
            db.log_agent(conn, run_id or self.run_id, "coder-agent", "in", "{}",
                         verdict="complete", tokens_in=0, tokens_out=output_tokens,
                         cost_usd=0.0, model_name=model)

    def _state(self, **extra) -> dict:
        state = {"run_id": self.run_id, "db_path": str(self.db_path), "story_id": "US-0001",
                 "spec": {"title": "t", "problem": "p", "why": "w",
                          "acceptance_criteria": ["a", "b"],
                          "tasks": [{"id": "T-1", "title": "t", "purpose": "p"}]}}
        state.update(extra)
        return state

    def test_a_story_over_its_budget_makes_no_further_model_call(self) -> None:
        self._spent(400_000)  # 0.4M output tokens x $30/M = $12
        live = MagicMock()
        with patch("factory.pipeline.agent_calls.run_agent", live):
            with self.assertRaises(BudgetExhausted) as raised:
                run_agent_json(self._state(), "architect-agent", "design it")
        live.assert_not_called()
        self.assertIn("$12.00", str(raised.exception))

    def test_spend_counts_every_live_run_of_the_story(self) -> None:
        with db.get_db(self.db_path) as conn:
            earlier = db.start_run(conn, "US-0001")
        self._spent(200_000, run_id=earlier)  # $6
        self._spent(200_000)                  # $6 -> $12 for the story
        with patch("factory.pipeline.agent_calls.run_agent", MagicMock()):
            with self.assertRaises(BudgetExhausted):
                run_agent_json(self._state(), "architect-agent", "design it")

    def test_under_budget_the_call_is_made(self) -> None:
        self._spent(100_000)  # $3
        result = AgentResult("architect-agent", '{"verdict": "pass"}', 0.1, 0)
        with patch("factory.pipeline.agent_calls.run_agent", return_value=result) as live:
            _res, parsed = run_agent_json(self._state(), "architect-agent", "design it")
        live.assert_called_once()
        self.assertEqual(parsed["verdict"], "pass")

    def test_a_replay_is_never_refused(self) -> None:
        self._spent(400_000)
        with db.get_db(self.db_path) as conn:
            db.log_agent(conn, self.run_id, "architect-agent", "in", '{"verdict": "pass"}',
                         verdict="pass")
        _res, parsed = run_agent_json(self._state(replay_run_id=self.run_id),
                                      "architect-agent", "design it")
        self.assertEqual(parsed["verdict"], "pass")

    def test_the_boss_blocks_a_stage_and_names_the_spend(self) -> None:
        self._spent(400_000)
        node = MagicMock()
        out = authorized("architect-agent", node)(self._state())
        node.assert_not_called()
        self.assertEqual(out["status"], "blocked")
        self.assertIn("$10.00", out["error"])
        with db.get_db(self.db_path) as conn:
            run = db.get_run(conn, self.run_id)
        self.assertEqual(run["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
