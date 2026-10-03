"""A story over the task limit is sent back to the spec-agent ONCE before gate-1 fails it.

Live (habits story 2): the spec-agent returned 9 tasks, gate-1 allows 6, and the run
simply failed — the agent was never told the limit, and nothing asked it to fit.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.agent_config.location import agents_dir
from factory.domain.gates import MAX_TASKS_PER_STORY
from factory.pipeline.nodes.spec import node_spec_agent
from factory.state.db import create_story, get_db, get_run_logs, init_db, start_run


def _spec(tasks: int) -> AgentResult:
    return AgentResult("spec-agent", json.dumps({
        "story_id": "US-0001", "title": "T", "type": "feature", "problem": "p", "why": "w",
        "acceptance_criteria": ["a", "b"], "non_goals": [], "verdict": "pass",
        "tasks": [{"id": f"T-{n:04d}", "title": f"t{n}", "purpose": "p"}
                  for n in range(1, tasks + 1)],
    }), 0.1, 0)


class SpecResizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "f.db"
        init_db(self.db_path)
        with get_db(self.db_path) as conn:
            create_story(conn, "US-0001", "S", "x")
            self.run_id = start_run(conn, "US-0001")
        self.state = {"request": "build it", "story_id": "US-0001", "run_id": self.run_id,
                      "db_path": str(self.db_path)}

    def _run(self, *results: AgentResult):
        with patch("factory.pipeline.agent_calls.run_agent", side_effect=list(results)) as agent:
            return node_spec_agent(self.state), agent

    def test_an_oversized_story_is_resized_once(self) -> None:
        out, agent = self._run(_spec(9), _spec(MAX_TASKS_PER_STORY))
        self.assertEqual(len(out["spec"]["tasks"]), MAX_TASKS_PER_STORY)
        self.assertEqual(agent.call_count, 2)
        second = agent.call_args_list[1].args[1]
        self.assertIn(f"at most {MAX_TASKS_PER_STORY} tasks", second)
        self.assertIn("9 tasks", second)
        with get_db(self.db_path) as conn:
            self.assertEqual(len(get_run_logs(conn, self.run_id)), 2)  # both calls on record

    def test_only_once_then_gate_1_decides(self) -> None:
        out, agent = self._run(_spec(9), _spec(8))
        self.assertEqual(agent.call_count, 2)
        self.assertEqual(len(out["spec"]["tasks"]), 8)

    def test_a_story_that_fits_is_never_sent_back(self) -> None:
        _out, agent = self._run(_spec(MAX_TASKS_PER_STORY))
        self.assertEqual(agent.call_count, 1)

    def test_a_replay_without_the_second_answer_keeps_the_recorded_story(self) -> None:
        # Runs recorded before this rule have one spec output: their replay must not break.
        with get_db(self.db_path) as conn:
            from factory.state.db import log_agent
            log_agent(conn, self.run_id, "spec-agent", "in", _spec(9).output, verdict="pass")
        out = node_spec_agent({**self.state, "replay_run_id": self.run_id})
        self.assertEqual(len(out["spec"]["tasks"]), 9)

    def test_the_agent_definition_states_the_limit(self) -> None:
        text = (agents_dir() / "spec-agent.md").read_text(encoding="utf-8")
        self.assertIn(f"at most {MAX_TASKS_PER_STORY} tasks", text)


if __name__ == "__main__":
    unittest.main()
