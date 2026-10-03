"""A per-task retry in a git repo must not read attempt 1's files as out-of-band.

Attempt 1's files are materialized by the FACTORY but only committed when the task
passes, so on a retry git still lists them as changed. A coder that returns only the
file it fixed (what the retry prompt asks for) used to be BLOCKED for "writing"
the files the factory itself wrote on attempt 1. A genuinely new undeclared file
must still block.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.pipeline.nodes.coder import node_coder_agent
from factory.state.db import create_story, get_db, get_run_gates, init_db, start_run
from factory.workspace.git import git_init


def _coder(*blocks: tuple[str, str]) -> str:
    return json.dumps({"verdict": "complete", "code_blocks": [
        {"path": p, "content": c, "action": "create"} for p, c in blocks]})


class RetryInGitRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "factory.db"
        self.repo = root / "repo"
        self.repo.mkdir()
        git_init(self.repo)
        init_db(self.db_path)
        with get_db(self.db_path) as conn:
            create_story(conn, "US-0001", "Pending", "x")
            self.run_id = start_run(conn, "US-0001")
        tasks = [{"id": "T-1", "title": "t", "purpose": "p", "scope": ["a.py", "b.py"],
                  "completion_evidence": "ok"}]
        self.state = {"request": "x", "story_id": "US-0001", "run_id": self.run_id,
                      "db_path": str(self.db_path), "opencode_cwd": str(self.repo),
                      "spec": {"title": "t", "problem": "p", "why": "w",
                               "acceptance_criteria": ["a"], "tasks": tasks},
                      "architect": {"architecture_notes": "n", "modules_affected": ["src/"]}}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _code(self, state: dict, output: str) -> dict:
        with patch("factory.pipeline.agent_calls.run_agent",
                   return_value=AgentResult("coder-agent", output, 0.1, 0)):
            return node_coder_agent(state)

    def _attempt_1(self) -> dict:
        out = self._code(self.state, _coder(
            ("a.py", "x = 1\n"), ("b.py", "def (:\n"),
            ("tests/test_a.py", "def test_a():\n    assert True\n")))
        self.assertEqual(out["next_action"], "retry")
        return {**self.state, **out}

    def _governance_gates(self) -> list:
        with get_db(self.db_path) as conn:
            return [g for g in get_run_gates(conn, self.run_id)
                    if "GOVERNANCE" in (g["reason"] or "")]

    def test_retry_fixing_one_file_completes(self) -> None:
        state2 = self._attempt_1()
        out = self._code(state2, _coder(("b.py", "y = 2\n")))
        self.assertEqual(out["next_action"], "complete", out.get("error"))
        self.assertEqual(self._governance_gates(), [])
        self.assertEqual(out.get("attempt_written"), [])

    def test_retry_with_new_undeclared_file_still_blocks(self) -> None:
        state2 = self._attempt_1()
        (self.repo / "sneaky.py").write_text("z = 3\n")
        out = self._code(state2, _coder(("b.py", "y = 2\n")))
        self.assertEqual(out["status"], "blocked")
        self.assertIn("sneaky.py", out["error"])
        self.assertNotIn("a.py", out["error"])


if __name__ == "__main__":
    unittest.main()
