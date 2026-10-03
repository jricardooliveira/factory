"""Backlog rows: an ordered, per-project list; started rows are never replaced."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.domain.backlog import BacklogStory
from factory.state import backlog, db
from factory.state.projects import insert_project


def _stories(*titles: str) -> list[BacklogStory]:
    return [BacklogStory(title=t, request=f"Do {t}.", rationale=f"why {t}") for t in titles]


class BacklogStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            for n in (1, 2):
                insert_project(conn, project_id=f"PROJ-00{n}", slug=f"p{n}", name=f"P{n}",
                               repo_path=f"projects/p{n}", spec_path=None)

    def _rows(self, project_id: str = "PROJ-001") -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return backlog.list_backlog(conn, project_id)

    def test_replace_writes_approved_rows_in_order_per_project(self) -> None:
        with db.get_db(self.db_path) as conn:
            backlog.replace_unstarted(conn, "PROJ-001", _stories("a", "b"))
            backlog.replace_unstarted(conn, "PROJ-002", _stories("other"))
        rows = self._rows()
        self.assertEqual([(r["title"], r["position"], r["status"]) for r in rows],
                         [("a", 1, "approved"), ("b", 2, "approved")])
        self.assertEqual(rows[0]["request"], "Do a.")
        self.assertEqual(rows[0]["rationale"], "why a")
        self.assertIsNone(rows[0]["story_id"])
        self.assertTrue(rows[0]["created_at"])

    def test_next_is_the_first_approved_and_started_rows_survive_a_replace(self) -> None:
        with db.get_db(self.db_path) as conn:
            backlog.replace_unstarted(conn, "PROJ-001", _stories("a", "b", "c"))
            first = backlog.next_approved(conn, "PROJ-001")
            backlog.mark_started(conn, first["id"], story_id="STORY-007", run_id=42)
            backlog.replace_unstarted(conn, "PROJ-001", _stories("x", "y"))
            nxt = backlog.next_approved(conn, "PROJ-001")
        self.assertEqual(first["title"], "a")
        rows = self._rows()
        self.assertEqual([(r["title"], r["position"], r["status"]) for r in rows],
                         [("a", 1, "started"), ("x", 2, "approved"), ("y", 3, "approved")])
        self.assertEqual((rows[0]["story_id"], rows[0]["run_id"]), ("STORY-007", 42))
        self.assertEqual(nxt["title"], "x")

    def test_next_is_none_when_nothing_is_approved(self) -> None:
        with db.get_db(self.db_path) as conn:
            self.assertIsNone(backlog.next_approved(conn, "PROJ-001"))
            backlog.replace_unstarted(conn, "PROJ-001", _stories("a"))
            row = backlog.next_approved(conn, "PROJ-001")
            backlog.mark_started(conn, row["id"], story_id="S", run_id=1)
            self.assertIsNone(backlog.next_approved(conn, "PROJ-001"))
            self.assertEqual(backlog.get_backlog_row(conn, row["id"])["project_id"], "PROJ-001")

    def test_a_returned_story_remembers_the_base_commit_of_the_run_that_failed(self) -> None:
        """Its passed tasks stay committed; the re-run must review from before them."""
        with db.get_db(self.db_path) as conn:
            backlog.replace_unstarted(conn, "PROJ-001", _stories("a"))
            row = backlog.next_approved(conn, "PROJ-001")
            self.assertIsNone(row["base_commit"])
            db.create_story(conn, "S", "a", "Do a.", project_id="PROJ-001")
            rid = db.start_run(conn, "S", project_id="PROJ-001", base_commit="abc123")
            backlog.mark_started(conn, row["id"], story_id="S", run_id=rid)
            self.assertEqual(backlog.return_to_backlog(conn, rid), ["PROJ-001"])
            again = backlog.next_approved(conn, "PROJ-001")
        self.assertEqual((again["id"], again["base_commit"]), (row["id"], "abc123"))
        self.assertEqual(self._rows()[0]["base_commit"], "abc123")
