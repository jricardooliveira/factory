"""The backlog service: the agent proposes stories, the operator approves them.

Offline: `factory.runs.backlog.run_agent` is patched to return frozen JSON.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import runs
from factory.adapters.opencode import AgentResult
from factory.agent_config import tiers
from factory.domain.gates import MAX_BACKLOG_REVISIONS
from factory.evidence.backlog import BACKLOG_RELPATH
from factory.evidence.brief import BRIEF_RELPATH
from factory.runs import RunError
from factory.state import db
from factory.state.backlog import list_backlog
from factory.workspace.projects import create_project


def _proposal(*titles: str, ok: bool = True) -> AgentResult:
    stories = [{"title": t, "request": f"Build {t}.", "rationale": f"why {t}"} for t in titles]
    return AgentResult(
        agent="backlog-agent",
        output=json.dumps({"stories": stories}),
        duration_secs=0.1,
        returncode=0 if ok else 1,
    )


class BacklogServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="shop")
        self.repo = Path(self.project["repo_path"])
        (self.repo / BRIEF_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / BRIEF_RELPATH).write_text("# Product brief — shop\n\nSells socks.\n")
        self.reviewed: list[list[str]] = []

    def _agent(self, *results: AgentResult):
        mock = patch("factory.runs.backlog.run_agent", side_effect=list(results))
        started = mock.start()
        self.addCleanup(mock.stop)
        return started

    def _propose(self, *verdicts):
        replies = iter(verdicts)

        def review(stories):
            self.reviewed.append([s.title for s in stories])
            return next(replies)

        return runs.propose_backlog("shop", db_path=self.db_path, review=review)

    def _rows(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return list_backlog(conn, self.project["id"])

    def _last_commit(self) -> str:
        return subprocess.run(
            ["git", "log", "-1", "--name-only", "--format=%s"],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout

    def test_backlog_agent_runs_on_the_intake_tier(self) -> None:
        self.assertEqual(tiers.tier_for_agent("backlog-agent"), "intake")

    def test_refuses_without_an_approved_brief(self) -> None:
        (self.repo / BRIEF_RELPATH).unlink()
        agent = self._agent()
        with self.assertRaises(RunError):
            self._propose(True)
        agent.assert_not_called()

    def test_approved_proposal_is_stored_written_and_committed(self) -> None:
        agent = self._agent(_proposal("skeleton", "login"))
        outcome = self._propose(True)

        self.assertEqual(outcome, runs.BacklogOutcome(approved=True, stories=2))
        self.assertEqual([(r["title"], r["status"]) for r in self._rows()],
                         [("skeleton", "approved"), ("login", "approved")])
        prompt = agent.call_args.args[1]
        self.assertEqual(agent.call_args.args[0], "backlog-agent")
        self.assertIn("Sells socks.", prompt)
        self.assertEqual(agent.call_args.kwargs["cwd"], str(self.repo))
        self.assertIn("2. **login** — approved", (self.repo / BACKLOG_RELPATH).read_text())
        log = self._last_commit()
        self.assertIn(f"factory: backlog {self.project['id']}", log)
        self.assertIn(BACKLOG_RELPATH, log)
        with db.get_db(self.db_path) as conn:
            turns = conn.execute("SELECT prompt, output_text FROM interview_turns").fetchall()
        self.assertEqual([(t["prompt"], t["output_text"]) for t in turns],
                         [(prompt, _proposal("skeleton", "login").output)])

    def test_rejected_proposal_writes_nothing(self) -> None:
        self._agent(_proposal("skeleton"))
        outcome = self._propose(False)
        self.assertFalse(outcome.approved)
        self.assertEqual(self._rows(), [])
        self.assertFalse((self.repo / BACKLOG_RELPATH).exists())

    def test_feedback_regenerates_with_the_feedback_in_the_prompt(self) -> None:
        agent = self._agent(_proposal("big"), _proposal("small", "smaller"))
        outcome = self._propose("split it up", True)
        self.assertTrue(outcome.approved)
        self.assertEqual(self.reviewed, [["big"], ["small", "smaller"]])
        self.assertNotIn("split it up", agent.call_args_list[0].args[1])
        self.assertIn("split it up", agent.call_args_list[1].args[1])
        self.assertEqual([r["title"] for r in self._rows()], ["small", "smaller"])

    def test_gives_up_unapproved_after_the_revision_budget(self) -> None:
        calls = MAX_BACKLOG_REVISIONS + 1
        agent = self._agent(*[_proposal("x") for _ in range(calls)])
        outcome = self._propose(*["more"] * calls)
        self.assertFalse(outcome.approved)
        self.assertEqual(agent.call_count, calls)
        self.assertEqual(self._rows(), [])

    def test_bad_or_failed_agent_output_is_a_run_error_but_still_logged(self) -> None:
        bad = AgentResult(agent="backlog-agent", output="no json", duration_secs=0.1, returncode=0)
        for result in (bad, _proposal("x", ok=False)):
            self._agent(result)
            with self.assertRaises(RunError):
                self._propose(True)
        with db.get_db(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM interview_turns").fetchone()[0], 2)

    def test_started_stories_are_fixed_and_next_story_walks_the_list(self) -> None:
        self._agent(_proposal("a", "b"))
        self._propose(True)
        row = runs.next_story("shop", db_path=self.db_path)
        self.assertEqual(row["title"], "a")
        runs.mark_started(row["id"], story_id="STORY-001", run_id=7, db_path=self.db_path)
        self.assertIn("1. **a** — started (STORY-001)", (self.repo / BACKLOG_RELPATH).read_text())
        self.assertIn(BACKLOG_RELPATH, self._last_commit())

        with patch("factory.runs.backlog.run_agent", side_effect=[_proposal("c")]) as agent:
            self._propose(True)
        prompt = agent.call_args.args[1]
        self.assertIn("STORY-001", prompt)  # the started story is shown as fixed
        self.assertEqual([(r["title"], r["status"]) for r in self._rows()],
                         [("a", "started"), ("c", "approved")])
        self.assertEqual(runs.next_story("shop", db_path=self.db_path)["title"], "c")

    def _started_run(self, status: str) -> tuple[dict, int]:
        self._agent(_proposal("a", "b"))
        self._propose(True)
        row = runs.next_story("shop", db_path=self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "STORY-001", "a", "Build a.")
            rid = db.start_run(conn, "STORY-001")
            db.finish_run(conn, rid, status)
        runs.mark_started(row["id"], story_id="STORY-001", run_id=rid, db_path=self.db_path)
        return row, rid

    def _assert_dismiss_returns_the_story(self, status: str) -> None:
        row, rid = self._started_run(status)
        self.assertEqual(runs.next_story("shop", db_path=self.db_path)["title"], "b")
        self.assertTrue(runs.dismiss_run(rid, db_path=self.db_path))  # said so to the operator
        again = runs.next_story("shop", db_path=self.db_path)
        self.assertEqual((again["id"], again["run_id"], again["story_id"]),
                         (row["id"], None, None))
        self.assertIn("1. **a** — approved", (self.repo / BACKLOG_RELPATH).read_text())

    def test_dismissing_a_failed_run_returns_its_story_to_the_backlog(self) -> None:
        """A run that failed at gate-build has no checkpoint to retry from; without
        this its story stayed 'started' and `factory next` skipped it forever."""
        self._assert_dismiss_returns_the_story("failed")

    def test_dismissing_a_blocked_run_returns_its_story_to_the_backlog(self) -> None:
        self._assert_dismiss_returns_the_story("blocked")

    def test_dismissing_a_completed_run_keeps_its_story_done(self) -> None:
        _row, rid = self._started_run("completed")
        self.assertFalse(runs.dismiss_run(rid, db_path=self.db_path))
        self.assertEqual(runs.next_story("shop", db_path=self.db_path)["title"], "b")

    def test_next_story_is_none_on_an_empty_backlog(self) -> None:
        self.assertIsNone(runs.next_story("shop", db_path=self.db_path))
