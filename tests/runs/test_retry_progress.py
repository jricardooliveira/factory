"""A resume into the coder continues at the first task that is not yet built.

Live (habits runs 1 and 5): `factory retry` re-entered the coder with no
`task_index`, so every task already committed was re-run and re-paid ($1.27), and
with tests on a later task's committed test failed an earlier task that was fine.
What is built is read from the run's own record: its gate-build rows and the
coder's verdicts.
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
from factory.runs.context import built_tasks
from factory.state import db
from factory.workspace.projects import create_project

TASK_IDS = [f"T-{n}" for n in range(1, 6)]
SPEC = {
    "title": "Bookmark search", "problem": "cannot find bookmarks", "why": "unusable list",
    "acceptance_criteria": ["title search works", "results paginate"],
    "tasks": [{"id": tid, "title": f"part {tid}", "purpose": "query", "scope": ["src/"],
               "completion_evidence": "test passes"} for tid in TASK_IDS],
    "verdict": "pass", "questions": [],
}
ARCH = {"verdict": "pass", "architecture_notes": "extend the repository",
        "modules_affected": ["src/search.py"]}
TESTER = {"overall": "pass", "qa_verdict": "pass",
          "ac_coverage": ["title search works", "results paginate"],
          "security_verdict": "pass", "highest_severity": "none",
          "performance_verdict": "pass", "summary": "ok"}
RELEASE = {"verdict": "pass", "summary": "Search.", "changes": ["src: search"],
           "how_to_verify": ["call it"], "migration_notes": "none",
           "rollback_notes": "revert the commit"}


class BuiltTasksTests(unittest.TestCase):
    """`built_tasks`: the leading tasks whose NEWEST gate-build passed and whose coder
    said `complete` — exactly the condition under which the coder commits a task."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "x")
            self.rid = db.start_run(conn, "US-0001")

    def _built(self, rows: list[tuple[str, bool, str]]) -> list[str]:
        """rows = (task id, gate-build passed, coder verdict), in the order they ran."""
        with db.get_db(self.db_path) as conn:
            for tid, passed, verdict in rows:
                db.log_agent(conn, self.rid, "coder-agent", "in", "{}", verdict=verdict,
                             stage_type=tid)
                db.log_gate(conn, self.rid, "gate-build", passed, f"[{tid}] py_compile:x")
            return built_tasks(conn, self.rid, TASK_IDS)

    def test_nothing_recorded_means_nothing_built(self) -> None:
        self.assertEqual(self._built([]), [])

    def test_a_task_that_passed_on_its_retry_is_built(self) -> None:
        rows = [("T-1", False, "complete"), ("T-1", True, "complete"), ("T-2", True, "complete")]
        self.assertEqual(self._built(rows), ["T-1", "T-2"])

    def test_a_failed_task_and_everything_after_it_is_not_built(self) -> None:
        rows = [("T-1", True, "complete"), ("T-2", True, "complete"),
                ("T-3", False, "complete"), ("T-3", False, "complete")]
        self.assertEqual(self._built(rows), ["T-1", "T-2"])

    def test_a_green_build_the_coder_did_not_call_complete_is_not_built(self) -> None:
        # gate-build passed but the coder said `partial`: the task was never committed.
        rows = [("T-1", True, "complete"), ("T-2", True, "partial")]
        self.assertEqual(self._built(rows), ["T-1"])

    def test_another_tasks_rows_never_count(self) -> None:
        # "[T-1]" must not be read as a prefix of "[T-10]"'s row, nor a remediation row
        # as any task's.
        with db.get_db(self.db_path) as conn:
            db.log_agent(conn, self.rid, "coder-agent", "in", "{}", verdict="complete",
                         stage_type="T-10")
            db.log_gate(conn, self.rid, "gate-build", True, "[T-10] py_compile:pass")
            db.log_gate(conn, self.rid, "gate-build", True, "[remediation] py_compile:pass")
            self.assertEqual(built_tasks(conn, self.rid, ["T-1", "T-10"]), [])


class ResumeIntoCoderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="bookmarks",
                                      stack="fastapi")
        self.repo = Path(self.project["repo_path"])
        self.calls: list[tuple[str, str]] = []
        self.tester = dict(TESTER)
        patcher = patch("factory.pipeline.agent_calls.run_agent", self._agent)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events: list = []

    def _agent(self, agent: str, prompt: str, **_kw) -> AgentResult:
        """The live boundary, offline: each coder call writes its own new file."""
        self.calls.append((agent, prompt))
        if agent == "coder-agent":
            n = sum(1 for a, _ in self.calls if a == "coder-agent")
            out = {"verdict": "complete", "code_blocks": [
                {"path": f"src/retry_{n}.py", "content": f"N = {n}\n", "action": "create"}]}
        else:
            out = self.tester if agent == "tester-agent" else RELEASE
        return AgentResult(agent, json.dumps(out), 1.0, 0)

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True,
                              check=True).stdout.strip()

    def _seed(self, built: int, *, status: str, failed_at: str | None = None,
              answered: bool = True) -> int:
        """A run whose design was approved at Checkpoint 2 and whose first `built`
        tasks are committed; `failed_at` failed its build twice."""
        base = self._git("rev-parse", "HEAD")
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Pending", "search my bookmarks",
                            project_id=self.project["id"])
            rid = db.start_run(conn, "US-0001", project_id=self.project["id"],
                               base_commit=base)
            db.log_agent(conn, rid, "spec-agent", "in", json.dumps(SPEC), verdict="pass")
            db.log_gate(conn, rid, "gate-1-spec", True, "ok")
            db.log_agent(conn, rid, "architect-agent", "in", json.dumps(ARCH), verdict="pass")
            gate_2 = db.log_gate(conn, rid, "gate-2-architect", True, "parked",
                                 needs_human=True, human_questions="approve the design?")
            if answered:
                db.respond_to_gate(conn, gate_2, "APPROVED: go")
            for tid in TASK_IDS[:built]:
                (self.repo / "src").mkdir(exist_ok=True)
                (self.repo / "src" / f"built_{tid}.py").write_text("X = 1\n")
                self._git("add", "-A")
                self._git("commit", "-q", "-m", f"factory: {tid} part {tid}")
                db.log_agent(conn, rid, "coder-agent", "in", "{}", verdict="complete",
                             stage_type=tid)
                db.log_gate(conn, rid, "gate-build", True, f"[{tid}] py_compile:pass")
            if failed_at:
                for _attempt in (1, 2):
                    db.log_agent(conn, rid, "coder-agent", "in", "{}", verdict="complete",
                                 stage_type=failed_at)
                    db.log_gate(conn, rid, "gate-build", False,
                                f"[{failed_at}] py_compile:fail")
            if status == "waiting_human":
                conn.execute("UPDATE pipeline_runs SET status = 'waiting_human' WHERE id = ?",
                             (rid,))
            else:
                db.finish_run(conn, rid, status, error="it stopped")
        return rid

    def _coder_starts(self) -> list[str]:
        return [e.detail for e in self.events
                if isinstance(e, runs.NodeStarted) and e.node == "coder-agent"]

    def _agents_called(self) -> list[str]:
        return [agent for agent, _ in self.calls]

    def test_a_fresh_approval_starts_at_task_1(self) -> None:
        rid = self._seed(0, status="waiting_human", answered=False)
        outcome = runs.resume_run(rid, "approve", db_path=self.db_path,
                                  on_event=self.events.append)
        self.assertEqual(outcome.status, "waiting_human", outcome.error)  # Checkpoint 3
        self.assertIn("task 1/5 T-1", self._coder_starts()[0])
        self.assertEqual(self._agents_called().count("coder-agent"), 5)

    def test_a_retry_after_task_3_failed_continues_at_task_3(self) -> None:
        rid = self._seed(2, status="failed", failed_at="T-3")
        outcome = runs.retry_run(rid, db_path=self.db_path, on_event=self.events.append)
        self.assertEqual(outcome.status, "waiting_human", outcome.error)
        starts = self._coder_starts()
        self.assertIn("task 3/5 T-3", starts[0])
        self.assertIn("(attempt 1)", starts[0])
        self.assertEqual(self._agents_called().count("coder-agent"), 3, starts)
        # The coder is told what is already done...
        first_prompt = next(p for a, p in self.calls if a == "coder-agent")
        self.assertIn("T-1", first_prompt)
        # ...and the tester still reviews the WHOLE story, from the run's base commit.
        tester_prompt = next(p for a, p in self.calls if a == "tester-agent")
        self.assertIn("src/built_T-1.py", tester_prompt)
        self.assertIn("src/retry_1.py", tester_prompt)

    def test_a_retry_with_every_task_built_goes_straight_to_the_tester(self) -> None:
        rid = self._seed(5, status="failed")
        outcome = runs.retry_run(rid, db_path=self.db_path, on_event=self.events.append)
        self.assertEqual(outcome.status, "waiting_human", outcome.error)
        self.assertEqual(self._coder_starts(), [])
        self.assertEqual(self._agents_called(), ["tester-agent", "release-agent"])
        entered = next(e for e in self.events if isinstance(e, runs.ResumeEntered))
        self.assertEqual(entered.entry, "tester")
        tester_prompt = self.calls[0][1]
        self.assertIn("src/built_T-5.py", tester_prompt)
        self.assertIn("[T-5] py_compile:pass", tester_prompt)  # the build it reviews

    def test_a_failed_remediation_build_rebuilds_from_task_1(self) -> None:
        # Every task built, but the newest gate-build (a remediation pass) failed: the
        # boss lets the tester start only on a green build, so the tasks are rebuilt.
        rid = self._seed(5, status="failed")
        with db.get_db(self.db_path) as conn:
            db.log_gate(conn, rid, "gate-build", False, "[remediation] py_compile:fail")
        outcome = runs.retry_run(rid, db_path=self.db_path, on_event=self.events.append)
        self.assertEqual(outcome.status, "waiting_human", outcome.error)
        self.assertIn("task 1/5 T-1", self._coder_starts()[0])


if __name__ == "__main__":
    unittest.main()
