"""Durable refinement end to end, on a simulated factory (scripted agents, in-process worker).

Brief approved → backlog proposed and approved → two stories refined independently
(questions answered as records, nobody blocked) → both plans ready → one batch
proposal holding both. Never the coding stage.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.domain.gates import MAX_BACKLOG_REVISIONS
from factory.evidence.brief import BRIEF_RELPATH
from factory.runs import RunError
from factory.runs.batches import propose_batch
from factory.runs.refinement import answer, start_backlog, start_refinement
from factory.state import db
from factory.state import workflow as store
from factory.state.backlog import list_backlog
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import create_project
from tests.simulated import ScriptedAgents, drain, simulate


class RefinementFlowTests(unittest.TestCase):
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
        self.agents = ScriptedAgents(stories=("Catalogue", "Basket"))
        self.enterContext(simulate(self.agents))

    def _pending(self, kind: str | None = None) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return [d for d in store.list_decisions(conn, self.project["id"])
                    if d["status"] == "pending" and kind in (None, d["kind"])]

    def _approve_backlog(self) -> list[dict]:
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        [proposal] = self._pending("backlog")
        answer(proposal["id"], "approve", db_path=self.db_path)
        with db.get_db(self.db_path) as conn:
            return list_backlog(conn, self.project["id"])

    def test_two_stories_refine_to_ready_plans_and_one_batch_holds_both(self) -> None:
        rows = self._approve_backlog()
        self.assertEqual([r["title"] for r in rows], ["Catalogue", "Basket"])

        for row in rows:
            start_refinement("shop", row["id"], db_path=self.db_path)
        drain(self.db_path)
        questions = self._pending("question")
        self.assertEqual(len(questions), 4)  # two per story, both stories waiting at once

        for question in questions:
            answer(question["id"], "1", db_path=self.db_path)
        drain(self.db_path)

        with db.get_db(self.db_path) as conn:
            sessions = store.list_sessions(conn, self.project["id"])
            plans = store.list_plans(conn, self.project["id"])
        self.assertEqual({s["status"] for s in sessions}, {"ready"})
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(p.ready for p in plans))

        proposal = propose_batch("shop", db_path=self.db_path)
        self.assertEqual(len(proposal["payload"]["plan_ids"]), 2)
        self.assertNotIn("coder", [mode for _agent, mode in self.agents.calls])

    def test_requested_changes_go_back_to_the_backlog_agent_with_the_feedback(self) -> None:
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        [first] = self._pending("backlog")
        answer(first["id"], "merge the basket into the catalogue", db_path=self.db_path)
        drain(self.db_path)
        [second] = self._pending("backlog")
        self.assertNotEqual(second["id"], first["id"])
        self.assertIn("merge the basket into the catalogue", self.agents.prompts[-1])
        with db.get_db(self.db_path) as conn:
            self.assertEqual(list_backlog(conn, self.project["id"]), [])  # nothing saved yet

    def test_backlog_feedback_is_bounded_then_the_brief_must_be_amended(self) -> None:
        start_backlog("shop", db_path=self.db_path)
        drain(self.db_path)
        for n in range(MAX_BACKLOG_REVISIONS):
            [proposal] = self._pending("backlog")
            answer(proposal["id"], f"change {n}", db_path=self.db_path)
            drain(self.db_path)
        [last] = self._pending("backlog")
        with self.assertRaises(RunError) as raised:
            answer(last["id"], "one more change", db_path=self.db_path)
        self.assertIn("amend", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
