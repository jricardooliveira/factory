"""A per-task retry in a git repo must not read attempt 1's files as out-of-band.

Attempt 1's files are materialized by the FACTORY but only committed when the task
passes, so on a retry git still lists them as changed. A coder that returns only the
file it fixed (what the retry prompt asks for) used to be BLOCKED for "writing"
the files the factory itself wrote on attempt 1. A genuinely new undeclared file
must still block.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.pipeline.nodes.coder import node_coder_agent
from factory.state.db import create_story, get_db, get_run_gates, init_db, start_run
from factory.workspace.git import git_commit_all, git_init


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
        # Blocked: the factory's own attempt files go; the undeclared one stays as evidence.
        self.assertFalse((self.repo / "a.py").exists())
        self.assertTrue((self.repo / "sneaky.py").exists())


def _porcelain(repo: Path) -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                         cwd=repo, capture_output=True, text=True, check=True).stdout
    return sorted(line[3:] for line in out.splitlines())


class GiveUpDiscardsAttemptTests(unittest.TestCase):
    """A story that gives up must leave the repo as the next story expects it:
    only the factory's own uncommitted attempt writes are undone, nothing else."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "factory.db"
        self.repo = root / "repo"
        self.repo.mkdir()
        git_init(self.repo)
        (self.repo / "a.py").write_text("orig = 0\n")
        git_commit_all(self.repo, "factory: T-0 seed")
        # The operator's product venv and the factory's evidence: never ours to discard.
        # (An untracked operator file elsewhere blocks the run as out-of-band; its
        # survival is pinned by test_retry_with_new_undeclared_file_still_blocks.)
        (self.repo / ".venv").mkdir()
        (self.repo / ".venv" / "marker").write_text("venv\n")
        (self.repo / "docs" / "work").mkdir(parents=True)
        (self.repo / "docs" / "work" / "PIPELINE.md").write_text("record\n")
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, story: str, scope: list[str]) -> dict:
        with get_db(self.db_path) as conn:
            create_story(conn, story, "Pending", "x")
            run_id = start_run(conn, story)
        tasks = [{"id": "T-1", "title": "t", "purpose": "p", "scope": scope,
                  "completion_evidence": "ok"}]
        return {"request": "x", "story_id": story, "run_id": run_id,
                "db_path": str(self.db_path), "opencode_cwd": str(self.repo),
                "project_dir": str(self.repo),
                "spec": {"title": "t", "problem": "p", "why": "w",
                         "acceptance_criteria": ["a"], "tasks": tasks},
                "architect": {"architecture_notes": "n", "modules_affected": ["src/"]}}

    def _code(self, state: dict, output: str) -> dict:
        with patch("factory.pipeline.agent_calls.run_agent",
                   return_value=AgentResult("coder-agent", output, 0.1, 0)):
            return node_coder_agent(state)

    def _assert_only_operator_files_left(self) -> None:
        self.assertEqual([p for p in _porcelain(self.repo) if not p.startswith("docs/")],
                         [])
        self.assertEqual((self.repo / "a.py").read_text(), "orig = 0\n")
        self.assertFalse((self.repo / "b.py").exists())
        self.assertFalse((self.repo / "tests").exists())  # the dir it created goes too
        self.assertTrue((self.repo / ".venv" / "marker").is_file())
        self.assertTrue((self.repo / "docs" / "work" / "PIPELINE.md").is_file())

    def _next_story_completes(self) -> None:
        state = self._state("US-0002", ["c.py"])
        out = self._code(state, _coder(("c.py", "ok = True\n")))
        self.assertEqual(out["next_action"], "complete", out.get("error"))
        with get_db(self.db_path) as conn:
            gates = get_run_gates(conn, state["run_id"])
        self.assertFalse([g for g in gates if "GOVERNANCE" in (g["reason"] or "")])

    def test_gate_build_give_up_discards_both_attempts(self) -> None:
        state = self._state("US-0001", ["a.py", "b.py"])
        out = self._code(state, _coder(
            ("a.py", "x = 1\n"), ("b.py", "def (:\n"),
            ("tests/unit/test_a.py", "def test_a():\n    assert True\n")))
        self.assertEqual(out["next_action"], "retry")
        out = self._code({**state, **out}, _coder(("b.py", "def (:\n")))
        self.assertEqual(out["next_action"], "give_up")
        self._assert_only_operator_files_left()
        self._next_story_completes()

    def test_partial_verdict_give_up_discards_the_attempt(self) -> None:
        state = self._state("US-0001", ["a.py", "b.py"])
        partial = json.dumps({"verdict": "partial", "code_blocks": [
            {"path": "a.py", "content": "x = 1\n", "action": "modify"},
            {"path": "b.py", "content": "y = 2\n", "action": "create"},
            {"path": "tests/test_b.py", "content": "def test_b():\n    pass\n",
             "action": "create"}]})
        out = self._code(state, partial)
        self.assertEqual(out["next_action"], "give_up")
        self._assert_only_operator_files_left()
        self._next_story_completes()

    def test_crash_on_retry_discards_the_earlier_attempt(self) -> None:
        state = self._state("US-0001", ["a.py", "b.py"])
        out = self._code(state, _coder(
            ("a.py", "x = 1\n"), ("b.py", "def (:\n"), ("tests/test_a.py", "x = 1\n")))
        self.assertEqual(out["next_action"], "retry")
        with patch("factory.pipeline.agent_calls.run_agent", side_effect=RuntimeError("boom")):
            out = node_coder_agent({**state, **out})
        self.assertEqual(out["status"], "failed")
        self._assert_only_operator_files_left()


if __name__ == "__main__":
    unittest.main()
