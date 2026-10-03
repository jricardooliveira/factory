"""A run that has ended never loops back into the coder.

Live incident (2026-10-03, habits run #1): tasks 1-4 passed, then the provider
returned "You've hit your session limit" for task 5. The off-script exit blocked
the run but left `next_action: next_task` from task 4 in the graph state, so the
router sent it straight back to the coder — ~20 failed calls until LangGraph's
recursion limit crashed the process.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.pipeline.graph import route_after_coder
from factory.pipeline.nodes.coder import node_coder_agent
from factory.state.db import create_story, get_db, init_db, start_run
from factory.workspace.git import git_init


class RouterTests(unittest.TestCase):
    def test_an_ended_run_ends_whatever_next_action_is_left_in_state(self) -> None:
        for status in ("blocked", "failed", "waiting_human"):
            for stale in ("next_task", "retry", "complete", "rearchitect"):
                with self.subTest(status=status, stale=stale):
                    self.assertEqual(
                        route_after_coder({"status": status, "next_action": stale}), "__end__")

    def test_a_live_run_still_routes_on_next_action(self) -> None:
        self.assertEqual(route_after_coder({"next_action": "next_task"}), "coder-agent")
        self.assertEqual(route_after_coder({"next_action": "complete"}), "tester-agent")


class OffScriptAfterAPassedTaskTests(unittest.TestCase):
    def test_a_provider_error_on_a_later_task_stops_the_line(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = root / "repo"
        repo.mkdir()
        git_init(repo)
        init_db(root / "f.db")
        with get_db(root / "f.db") as conn:
            create_story(conn, "US-0001", "S", "x")
            run_id = start_run(conn, "US-0001")
        tasks = [{"id": f"T-{n}", "title": "t", "purpose": "p", "scope": [f"m{n}.py"],
                  "completion_evidence": "ok"} for n in (1, 2)]
        # The state the graph holds entering task 2: task 1 passed and said next_task.
        state = {"request": "x", "story_id": "US-0001", "run_id": run_id,
                 "db_path": str(root / "f.db"), "opencode_cwd": str(repo),
                 "spec": {"title": "t", "problem": "p", "why": "w",
                          "acceptance_criteria": ["a"], "tasks": tasks},
                 "architect": {"architecture_notes": "n", "modules_affected": ["."]},
                 "task_index": 1, "next_action": "next_task"}
        limit = AgentResult("coder-agent", "ERROR: You've hit your session limit", 2.0, 1)
        with patch("factory.pipeline.agent_calls.run_agent", return_value=limit):
            out = node_coder_agent(state)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(route_after_coder({**state, **out}), "__end__")


if __name__ == "__main__":
    unittest.main()
