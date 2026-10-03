"""On a retry, the coder may also change the test files that just failed (operator
decision, 2026-10-03).

Live (habits run #8): a story that deliberately changes a row's markup broke the previous
story's tests. The task may write three files, the gate runs the whole suite, and the
failing tests were outside the task's scope: no attempt could pass.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.pipeline.nodes.coder import node_coder_agent
from factory.state.db import create_story, get_db, init_db, start_run
from factory.workspace.git import git_commit_all, git_init

OLD_TEST = "from app import row\n\n\ndef test_row():\n    assert row() == 'Water 0 / 8'\n"
NEW_TEST = "from app import row\n\n\ndef test_row():\n    assert '<input' in row()\n"


def _coder(*blocks: tuple[str, str]) -> AgentResult:
    return AgentResult("coder-agent", json.dumps({
        "verdict": "complete", "implementation_summary": "s",
        "files_created": [], "files_modified": [p for p, _ in blocks], "tests_added": [],
        "code_blocks": [{"path": p, "content": c, "action": "modify"} for p, c in blocks],
    }), 0.1, 0)


class RetryMayFixFailingTestsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        (self.repo / "tests").mkdir(parents=True)
        git_init(self.repo)
        (self.repo / "app.py").write_text("def row():\n    return 'Water 0 / 8'\n")
        (self.repo / "other.py").write_text("x = 1\n")
        (self.repo / "tests" / "test_row.py").write_text(OLD_TEST)
        git_commit_all(self.repo, "factory: earlier story")
        self.db_path = root / "f.db"
        init_db(self.db_path)
        with get_db(self.db_path) as conn:
            create_story(conn, "US-0001", "S", "x")
            run_id = start_run(conn, "US-0001")
        tasks = [{"id": "T-1", "title": "t", "purpose": "p", "scope": ["app.py"],
                  "completion_evidence": "ok"}]
        self.state = {"request": "x", "story_id": "US-0001", "run_id": run_id,
                      "db_path": str(self.db_path), "opencode_cwd": str(self.repo),
                      "spec": {"title": "t", "problem": "p", "why": "w",
                               "acceptance_criteria": ["a"], "tasks": tasks},
                      "architect": {"architecture_notes": "n", "modules_affected": ["app.py"]}}
        env = patch.dict("os.environ", {"FACTORY_RUN_TESTS": "1"})
        env.start()
        self.addCleanup(env.stop)

    def _code(self, state: dict, output: AgentResult):
        with patch("factory.pipeline.agent_calls.run_agent", return_value=output) as agent:
            return node_coder_agent(state), agent

    def _attempt_1(self) -> dict:
        out, _ = self._code(self.state, _coder(
            ("app.py", "def row():\n    return 'Water <input> 0 / 8'\n")))
        self.assertEqual(out["next_action"], "retry", out.get("error"))
        return {**self.state, **out}

    def test_the_retry_is_told_which_test_files_it_may_now_change(self) -> None:
        state2 = self._attempt_1()
        self.assertEqual(state2["retry_scope"], ["tests/test_row.py"])
        _out, agent = self._code(state2, _coder(("tests/test_row.py", NEW_TEST)))
        prompt = agent.call_args.args[1]
        self.assertIn("tests/test_row.py", prompt)
        self.assertIn("assert row() == 'Water 0 / 8'", prompt)  # shown in full
        self.assertIn("never weaken", prompt)

    def test_the_retry_may_update_the_failing_test_and_the_task_passes(self) -> None:
        state2 = self._attempt_1()
        out, _ = self._code(state2, _coder(("tests/test_row.py", NEW_TEST)))
        self.assertEqual(out["next_action"], "complete", out.get("error"))
        self.assertEqual(out.get("retry_scope"), [])

    def test_any_other_file_outside_the_scope_is_still_refused(self) -> None:
        state2 = self._attempt_1()
        out, _ = self._code(state2, _coder(("tests/test_row.py", NEW_TEST),
                                           ("other.py", "x = 2\n")))
        self.assertNotEqual(out["next_action"], "complete")
        self.assertIn("other.py", out["gate_build"]["reason"])


if __name__ == "__main__":
    unittest.main()
