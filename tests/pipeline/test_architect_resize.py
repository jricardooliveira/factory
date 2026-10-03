"""A design over the file limit is sent back to the architect ONCE before gate-2 fails it.

Live (habits, runs #3 and #4): for one table of habits the architect designed db.py,
repository.py, services.py, a migrations folder and five test files — 16-17 files
against a limit of 12 it was never told about — and the run failed after it was paid for.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.agent_config.location import agents_dir
from factory.domain.gates import MAX_MODULES_PER_STORY
from factory.pipeline.nodes.architect import node_architect_agent
from factory.state.db import create_story, get_db, get_run_logs, init_db, log_agent, start_run


OVER = MAX_MODULES_PER_STORY + 4


def _design(files: int) -> AgentResult:
    return AgentResult("architect-agent", json.dumps({
        "verdict": "pass", "architecture_notes": "n", "risks": [],
        "modules_affected": [f"app/m{n}.py" for n in range(files)],
    }), 0.1, 0)


class ArchitectResizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "f.db"
        init_db(self.db_path)
        with get_db(self.db_path) as conn:
            create_story(conn, "US-0001", "S", "x")
            self.run_id = start_run(conn, "US-0001")
        self.state = {
            "request": "build it", "story_id": "US-0001", "run_id": self.run_id,
            "db_path": str(self.db_path),
            "spec": {"title": "t", "problem": "p", "why": "w", "acceptance_criteria": ["a", "b"],
                     "tasks": [{"id": "T-0001", "title": "t", "purpose": "p"}]},
        }

    def _run(self, *results: AgentResult):
        with patch("factory.pipeline.agent_calls.run_agent", side_effect=list(results)) as agent:
            return node_architect_agent(self.state), agent

    def test_an_oversized_design_is_resized_once(self) -> None:
        out, agent = self._run(_design(OVER), _design(9))
        self.assertEqual(len(out["architect"]["modules_affected"]), 9)
        self.assertEqual(agent.call_count, 2)
        second = agent.call_args_list[1].args[1]
        self.assertIn(f"at most {MAX_MODULES_PER_STORY} files", second)
        self.assertIn(f"{OVER} files", second)
        with get_db(self.db_path) as conn:
            self.assertEqual(len(get_run_logs(conn, self.run_id)), 2)

    def test_only_once_then_gate_2_decides(self) -> None:
        out, agent = self._run(_design(OVER), _design(OVER - 1))
        self.assertEqual(agent.call_count, 2)
        self.assertEqual(len(out["architect"]["modules_affected"]), OVER - 1)

    def test_a_design_that_fits_is_never_sent_back(self) -> None:
        _out, agent = self._run(_design(MAX_MODULES_PER_STORY))
        self.assertEqual(agent.call_count, 1)

    def test_a_replay_without_the_second_answer_keeps_the_recorded_design(self) -> None:
        with get_db(self.db_path) as conn:
            log_agent(conn, self.run_id, "architect-agent", "in", _design(OVER).output,
                      verdict="pass")
        out = node_architect_agent({**self.state, "replay_run_id": self.run_id})
        self.assertEqual(len(out["architect"]["modules_affected"]), OVER)

    def test_the_agent_definition_states_the_limit(self) -> None:
        text = (agents_dir() / "architect-agent.md").read_text(encoding="utf-8")
        self.assertIn(f"at most {MAX_MODULES_PER_STORY} files", text)


if __name__ == "__main__":
    unittest.main()
