"""A live project run works on its own story branch from the first agent call.

Release = merged PR (operator decision, 2026-10-02): `factory/<story>` is cut from
the main line before anything is written, recorded on the run, and checked out
again on resume. A product repo with uncommitted changes is refused BEFORE a story
or run is created — the factory never switches branches over someone's edits.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory import runs
from factory.runs import RunError
from factory.state import db
from factory.workspace import git
from factory.workspace.projects import create_project


def _quiet_graph():
    graph = MagicMock()
    graph.stream.return_value = iter(())
    return graph


class StoryBranchLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="shop")
        self.repo = Path(self.project["repo_path"])
        self.main = git.current_branch(self.repo)
        for target in ("factory.runs.service.compile_pipeline",
                       "factory.runs.service.compile_coder_only_pipeline"):
            patcher = patch(target, side_effect=_quiet_graph)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _start(self) -> runs.RunOutcome:
        return runs.run_pipeline("add a cart", opencode_cwd=str(self.repo),
                                 db_path=self.db_path, project_id=self.project["id"])

    def _run(self, run_id: int) -> dict:
        with db.get_db(self.db_path) as conn:
            return db.get_run(conn, run_id)

    def test_a_live_run_works_on_its_own_story_branch(self) -> None:
        outcome = self._start()
        run = self._run(outcome.run_id)
        self.assertEqual(run["story_branch"], f"factory/{run['story_id']}")
        self.assertEqual(run["target_branch"], self.main)
        self.assertEqual(git.current_branch(self.repo), run["story_branch"])

    def test_uncommitted_changes_refuse_the_run_before_anything_is_created(self) -> None:
        (self.repo / "PROJECT_RULES.md").write_text("# edited by hand, not committed\n")
        with self.assertRaises(RunError) as raised:
            self._start()
        self.assertIn("uncommitted", str(raised.exception))
        with db.get_db(self.db_path) as conn:
            self.assertEqual(db.list_runs(conn), [])
        self.assertEqual(git.current_branch(self.repo), self.main)

    def test_a_resume_returns_to_the_story_branch(self) -> None:
        first = self._start()
        story_branch = self._run(first.run_id)["story_branch"]
        with db.get_db(self.db_path) as conn:
            gate = db.log_gate(conn, first.run_id, "gate-2-architect", True, "r",
                               needs_human=True, human_questions="ok?")
            db.log_agent(conn, first.run_id, "spec-agent", "in",
                         '{"title": "t", "problem": "p", "why": "w", '
                         '"acceptance_criteria": ["a", "b"], "tasks": []}', verdict="pass")
            db.log_agent(conn, first.run_id, "architect-agent", "in",
                         '{"architecture_notes": "n", "modules_affected": ["x"]}', verdict="pass")
            db.finish_run(conn, first.run_id, "waiting_human")
        git.checkout_branch(self.repo, self.main)  # e.g. the operator looked around
        runs.resume_run(first.run_id, "approve", "ok", db_path=self.db_path)
        self.assertEqual(git.current_branch(self.repo), story_branch)
        self.assertTrue(gate)


if __name__ == "__main__":
    unittest.main()
