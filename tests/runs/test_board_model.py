"""The board's read model on a simulated factory (design_handoff_factory_board)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.evidence.brief import BRIEF_RELPATH
from factory.runs.board import board
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, park_run, simulate


class BoardModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        self.db_path = home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=home, slug="shop")
        repo = Path(self.project["repo_path"])
        brief = repo / BRIEF_RELPATH
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text("# Product brief — shop\n\nSells socks.\n")
        git_commit_paths(repo, [brief], "factory: approved brief")
        self.agents = ScriptedAgents(stories=("Catalogue", "Basket", "Checkout"))
        self.enterContext(simulate(self.agents))
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        answer(self._pending()[0]["id"], "approve", db_path=self.db_path)

    def _pending(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"])
                    if d["status"] == "pending"]

    def _board(self):
        return board("shop", db_path=self.db_path)

    def test_one_storys_questions_are_one_row_counted_one_by_one(self) -> None:
        start_refinement("shop", 1, db_path=self.db_path)
        drain(self.db_path)
        [row] = self._board().decisions
        self.assertEqual((row.kind, row.title, row.count, row.sub),
                         ("questions", "Story #1 Catalogue", 2, "2 questions"))
        answer(row.data["pending"][0]["id"], "1", db_path=self.db_path)
        model = self._board()
        [row] = model.decisions
        self.assertEqual((row.sub, row.data["qi"], row.data["total"], model.need_count),
                         ("1 question · 1 answered", 1, 2, 1))
        self.assertEqual(next(s for s in model.stories if s.n == 1).state, "needs")

    def test_failed_first_then_approvals_then_questions(self) -> None:
        start_refinement("shop", 1, db_path=self.db_path)
        drain(self.db_path)
        self.agents.fail_next("backlog")
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        kinds = [d.kind for d in self._board().decisions]
        self.assertEqual(kinds, ["fail", "backlog", "questions"])

    def test_a_parked_run_is_a_checkpoint_on_its_story(self) -> None:
        park_run(self.db_path, self.project, title="Catalogue", stage="gate-2-architect",
                 questions="A new view: approve?", backlog_id=1,
                 logs={"spec-agent": {"title": "t"}, "architect-agent": {
                     "architecture_notes": "One view.", "modules_affected": ["a.py"]}})
        model = self._board()
        [ckpt] = model.decisions
        self.assertEqual((ckpt.kind, ckpt.title, ckpt.sub, ckpt.story),
                         ("ckpt", "Story #1 Catalogue", "Checkpoint 2 of 3 · design", 1))
        self.assertEqual(ckpt.data["design"]["architecture_notes"], "One view.")
        self.assertEqual(ckpt.data["questions"], ["A new view: approve?"])
        story = model.stories[0]
        self.assertEqual((story.state, story.meta), ("working", "waiting for you"))
        self.assertEqual(story.doing, "Waiting for you · checkpoint 2 of 3 (design)")
        self.assertEqual(story.stage[1], "wait")

    def test_a_run_parked_at_release_is_a_release(self) -> None:
        park_run(self.db_path, self.project, title="Basket", stage="gate-release-human",
                 backlog_id=2, logs={"release-agent": {"summary": "Basket works."}})
        model = self._board()
        [release] = model.decisions
        self.assertEqual(release.kind, "release")
        self.assertEqual(release.data["release"]["summary"], "Basket works.")
        self.assertEqual(next(s for s in model.stories if s.n == 2).state, "release")

    def test_ready_stories_make_the_next_start_a_batch(self) -> None:
        for story in (1, 2):
            start_refinement("shop", story, db_path=self.db_path)
        drain(self.db_path)
        for q in self._pending():
            answer(q["id"], "1", db_path=self.db_path)
        drain(self.db_path)
        model = self._board()
        self.assertEqual(model.next.action, "batch")
        self.assertIn("#1 and #2 can run together", model.next.title)
        self.assertEqual(model.lifecycle[0], ("Define", "done"))

    def test_queued_work_with_no_worker_is_stuck(self) -> None:
        start_refinement("shop", 1, db_path=self.db_path)  # queued, nothing drains it
        self.assertEqual(self._board().worker, "stuck")


if __name__ == "__main__":
    unittest.main()
