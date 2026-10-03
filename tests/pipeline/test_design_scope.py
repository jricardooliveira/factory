"""Writes are checked against the design the operator approved, not one task's file list
(operator decision, 2026-10-03).

Live (habits run #8): the spec-agent split the files between tasks before the design
existed. Task 3 left part of `app/routes.py` undone; task 4 needed it and could only
refuse — twice, on the escalation model.
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
from factory.verification.scope import design_scope
from factory.workspace.git import git_commit_all, git_init


def _coder(*blocks: tuple[str, str]) -> AgentResult:
    return AgentResult("coder-agent", json.dumps({
        "verdict": "complete", "implementation_summary": "s",
        "files_created": [], "files_modified": [p for p, _ in blocks], "tests_added": [],
        "code_blocks": [{"path": p, "content": c, "action": "modify"} for p, c in blocks],
    }), 0.1, 0)


class DesignScopeParsingTests(unittest.TestCase):
    def test_paths_are_read_from_annotated_entries_and_tests_are_left_out(self) -> None:
        self.assertEqual(
            design_scope(["app/routes.py (changed: index lists habits)", "`app/db.py` (new)",
                          "tests/test_app.py (changed)", "app/templates/index.html",
                          "the data layer", ""]),
            ["app/routes.py", "app/db.py", "app/templates/index.html"],
        )


class DesignScopeEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git_init(self.repo)
        (self.repo / "page.py").write_text("PAGE = 'old'\n")
        (self.repo / "routes.py").write_text("DONE = False  # routes marker\n")
        (self.repo / "other.py").write_text("x = 1\n")
        git_commit_all(self.repo, "factory: earlier tasks")
        self.db_path = root / "f.db"
        init_db(self.db_path)
        with get_db(self.db_path) as conn:
            create_story(conn, "US-0001", "S", "x")
            run_id = start_run(conn, "US-0001")
        tasks = [{"id": "T-1", "title": "t", "purpose": "p", "scope": ["page.py"],
                  "completion_evidence": "ok"}]
        self.state = {
            "request": "x", "story_id": "US-0001", "run_id": run_id,
            "db_path": str(self.db_path), "opencode_cwd": str(self.repo),
            "spec": {"title": "t", "problem": "p", "why": "w", "acceptance_criteria": ["a"],
                     "tasks": tasks},
            "architect": {"architecture_notes": "n", "modules_affected": [
                "page.py (changed)", "routes.py (changed: sets done)", "tests/test_page.py"]},
        }

    def _code(self, output: AgentResult):
        with patch("factory.pipeline.agent_calls.run_agent", return_value=output) as agent:
            return node_coder_agent(self.state), agent

    def test_a_task_may_write_a_file_the_approved_design_lists(self) -> None:
        out, agent = self._code(_coder(("page.py", "PAGE = 'new'\n"),
                                       ("routes.py", "DONE = True\n")))
        self.assertEqual(out["next_action"], "complete", out.get("error"))
        prompt = agent.call_args.args[1]
        self.assertIn("Also allowed", prompt)
        self.assertIn("- routes.py", prompt)
        self.assertIn("routes marker", prompt)  # shown, so it can be edited, not rewritten blind

    def test_a_file_outside_the_design_is_still_refused(self) -> None:
        out, _ = self._code(_coder(("page.py", "PAGE = 'new'\n"), ("other.py", "x = 2\n")))
        self.assertNotEqual(out["next_action"], "complete")
        self.assertIn("other.py", out["gate_build"]["reason"])
        self.assertEqual((self.repo / "other.py").read_text(), "x = 1\n")


if __name__ == "__main__":
    unittest.main()
