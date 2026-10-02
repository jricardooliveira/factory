"""A coder write outside the task's declared files is refused BEFORE it lands.

Operator decision (2026-10-02, review task T06): out-of-scope writes are blocked
and retried, not merely reported at Checkpoint 3. The check runs on the declared
code_blocks before materialization, so a refused attempt leaves nothing on disk
for the retry to trip over. Tests and toolchain manifests stay allowed (the scope
policy in `verification.scope` already exempts them); a task with no declared
scope is unrestricted, as before. Measured first on the live SupportFlow run:
zero false positives across its six coder outputs.
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


def _coder(*paths: str) -> str:
    return json.dumps({"verdict": "complete", "code_blocks": [
        {"path": p, "content": "x = 1\n", "action": "create"} for p in paths]})


class ScopeEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "factory.db"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        init_db(self.db_path)
        with get_db(self.db_path) as conn:
            create_story(conn, "US-0001", "Pending", "x")
            self.run_id = start_run(conn, "US-0001")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, scope: list[str] | None, **extra) -> dict:
        tasks = [{"id": "T-1", "title": "t", "purpose": "p", "scope": scope or [],
                  "completion_evidence": "ok"}]
        state = {"request": "x", "story_id": "US-0001", "run_id": self.run_id,
                 "db_path": str(self.db_path), "opencode_cwd": str(self.repo),
                 "spec": {"title": "t", "problem": "p", "why": "w",
                          "acceptance_criteria": ["a", "b"], "tasks": tasks},
                 "architect": {"architecture_notes": "n", "modules_affected": ["src/"]}}
        state.update(extra)
        return state

    def _code(self, state: dict, output: str) -> dict:
        with patch("factory.pipeline.agent_calls.run_agent",
                   return_value=AgentResult("coder-agent", output, 0.1, 0)):
            return node_coder_agent(state)

    def test_a_write_outside_scope_is_refused_and_retried_with_the_reason(self) -> None:
        out = self._code(self._state(["src/"]), _coder("src/a.py", "lib/b.py"))
        self.assertFalse(out["gate_build"]["passed"])
        self.assertIn("lib/b.py", out["gate_build"]["reason"])
        self.assertEqual(out["next_action"], "retry")
        self.assertIn("lib/b.py", " ".join(out["prior_findings"]))
        # Nothing landed — not even the in-scope file of the refused attempt.
        self.assertFalse((self.repo / "src" / "a.py").exists())
        self.assertFalse((self.repo / "lib" / "b.py").exists())
        with get_db(self.db_path) as conn:
            [gate] = get_run_gates(conn, self.run_id)
        self.assertIn("SCOPE", gate["reason"])

    def test_tests_and_manifests_are_still_allowed(self) -> None:
        out = self._code(self._state(["src/"]),
                         _coder("src/a.py", "tests/test_a.py", "requirements.txt"))
        self.assertTrue(out["gate_build"]["passed"], out["gate_build"]["reason"])
        self.assertTrue((self.repo / "tests" / "test_a.py").exists())

    def test_a_repo_prefixed_write_is_judged_in_the_same_space_as_the_scope(self) -> None:
        # Found by the eval corpus: a REAL run wrote `repo/cli.py` under scope `cli.py`,
        # in a working directory NOT named `repo` (so the path keeps its prefix).
        work = self.root / "work"
        work.mkdir()
        out = self._code(self._state(["cli.py"], opencode_cwd=str(work)), _coder("repo/cli.py"))
        self.assertTrue(out["gate_build"]["passed"], out["gate_build"]["reason"])

    def test_a_python_package_marker_is_structural_not_scope_creep(self) -> None:
        out = self._code(self._state(["calc/models.py"]), _coder("calc/__init__.py",
                                                                 "calc/models.py"))
        self.assertTrue(out["gate_build"]["passed"], out["gate_build"]["reason"])

    def test_a_factory_owned_path_keeps_its_own_precise_refusal(self) -> None:
        state = self._state(["src/"], project_dir=str(self.repo))
        out = self._code(state, _coder("PROJECT_RULES.md"))
        error = out.get("error", "") + (out.get("gate_build") or {}).get("reason", "")
        self.assertNotIn("SCOPE", error)
        self.assertIn("factory-owned", error)

    def test_a_task_without_declared_scope_is_unrestricted(self) -> None:
        out = self._code(self._state(None), _coder("anywhere/x.py"))
        self.assertTrue(out["gate_build"]["passed"], out["gate_build"]["reason"])

    def test_the_retry_budget_still_ends_the_run(self) -> None:
        out = self._code(self._state(["src/"], attempt_number=2), _coder("lib/b.py"))
        self.assertEqual(out["status"], "failed")
        self.assertIn("lib/b.py", out["error"])

    def test_a_remediation_pass_is_held_to_the_story_s_declared_scope(self) -> None:
        state = self._state(["src/"], remediation=True, prior_findings=["fix it"],
                            attempt_number=2)
        with patch("factory.pipeline.nodes.coder.collect_repo_diff", return_value=""):
            out = self._code(state, _coder("lib/b.py"))
        self.assertFalse(out["gate_build"]["passed"])
        self.assertIn("lib/b.py", out["gate_build"]["reason"])
        self.assertFalse((self.repo / "lib" / "b.py").exists())


if __name__ == "__main__":
    unittest.main()
