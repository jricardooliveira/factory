"""`runs.project_status`: the facts behind `factory status`, read from the DB and repo."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory import runs
from factory.domain.backlog import BacklogStory
from factory.evidence.brief import BRIEF_RELPATH
from factory.state import db
from factory.state.backlog import list_backlog, mark_started, replace_unstarted
from factory.state.interviews import add_answer, log_turn
from factory.workspace.projects import create_project


class ProjectStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="habits")
        self.pid = self.project["id"]
        self.repo = Path(self.project["repo_path"])

    def test_a_fresh_project_needs_its_interview(self) -> None:
        status = runs.project_status("habits", db_path=self.db_path)
        self.assertFalse(status.facts.has_brief)
        self.assertEqual(status.next.command, "factory interview habits")
        self.assertEqual(status.phases[0].state, "now")

    def test_it_reads_answers_backlog_runs_and_spend(self) -> None:
        (self.repo / BRIEF_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / BRIEF_RELPATH).write_text("# Brief\n")
        with db.get_db(self.db_path) as conn:
            add_answer(conn, self.pid, topic="goal", question="q", options=[], answer="a",
                       assumed=False)
            add_answer(conn, self.pid, topic="data", question="q", options=[], answer="b",
                       assumed=True)
            log_turn(conn, self.pid, prompt="p", output_text="o", model_name="m",
                     tokens_in=1, tokens_out=1, cost_usd=0.5, duration_secs=1.0)
            replace_unstarted(conn, self.pid, [BacklogStory(title="Skeleton", request="r"),
                                               BacklogStory(title="Habits", request="r")])
            db.create_story(conn, "US-0001", "Skeleton", "r", project_id=self.pid)
            rid = db.start_run(conn, "US-0001", project_id=self.pid)
            db.log_agent(conn, rid, "spec-agent", "in", "{}", verdict="pass", tokens_in=0,
                         tokens_out=0, cost_usd=0.25, model_name="claude/claude-opus-5-5")
            db.update_run_stage(conn, rid, "gate-2-human")
            db.finish_run(conn, rid, "waiting_human")
            first = list_backlog(conn, self.pid)[0]["id"]
            mark_started(conn, first, story_id="US-0001", run_id=rid)

        status = runs.project_status("habits", db_path=self.db_path)
        facts = status.facts
        self.assertTrue(facts.has_brief)
        self.assertEqual((facts.answers, facts.assumptions), (2, 1))
        self.assertEqual([s.title for s in facts.backlog], ["Skeleton", "Habits"])
        self.assertEqual(facts.backlog[0].run.status, "waiting_human")
        self.assertIsNone(facts.backlog[1].run)
        self.assertEqual(facts.intake_usd, 0.5)
        self.assertEqual(facts.stories_usd, 0.25)
        self.assertEqual(status.next.command, f"factory approve {rid}")

    def test_a_run_is_retryable_only_with_an_answered_checkpoint(self) -> None:
        """The same rule `retry_run` applies: status never suggests a refused retry."""
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "r", project_id=self.pid)
            built = db.start_run(conn, "US-0001", project_id=self.pid)
            db.log_gate(conn, built, "gate-build", False, "py_compile failed")
            db.finish_run(conn, built, "failed")
            answered = db.start_run(conn, "US-0001", project_id=self.pid)
            gate = db.log_gate(conn, answered, "gate-2-architect", True, "ok", needs_human=True)
            db.respond_to_gate(conn, gate, "REJECTED: simpler")
            db.finish_run(conn, answered, "failed")
        retryable = {r.id: r.retryable
                     for r in runs.project_status("habits", db_path=self.db_path).facts.runs}
        self.assertEqual(retryable, {built: False, answered: True})

    def test_dismissing_a_released_run_keeps_the_story_released(self) -> None:
        with db.get_db(self.db_path) as conn:
            replace_unstarted(conn, self.pid, [BacklogStory(title="Skeleton", request="r"),
                                               BacklogStory(title="Habits", request="r")])
            db.create_story(conn, "US-0001", "Skeleton", "r", project_id=self.pid)
            rid = db.start_run(conn, "US-0001", project_id=self.pid)
            db.update_run_stage(conn, rid, "release")
            db.finish_run(conn, rid, "completed")
            mark_started(conn, list_backlog(conn, self.pid)[0]["id"], story_id="US-0001",
                         run_id=rid)
        runs.dismiss_run(rid, db_path=self.db_path)
        runs.dismiss_run(rid, db_path=self.db_path)  # dismissing twice keeps the origin
        status = runs.project_status("habits", db_path=self.db_path)
        run = status.facts.backlog[0].run
        self.assertEqual((run.status, run.archived_from), ("archived", "completed"))
        self.assertIn("1 of 2 released", status.phases[3].detail)

    def test_every_project_when_none_is_named(self) -> None:
        create_project(self.db_path, home=self.home, slug="shop")
        self.assertEqual([s.facts.slug for s in runs.all_project_status(db_path=self.db_path)],
                         ["habits", "shop"])


if __name__ == "__main__":
    unittest.main()
