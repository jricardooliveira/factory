"""The run service: run / replay / resume / retry with NO printing.

The CLI and the TUI both drive runs. Before `factory.runs` existed the TUI
imported the CLI module and swapped its rich console for a throwaway buffer to
keep pipeline output from corrupting the screen. The service reports progress
through an event callback instead, so each interface decides what (if anything)
to render — and a caller that passes no callback gets silence by construction.

Every graph here is driven offline: fresh runs replay frozen agent outputs
(`replay_run_id`), and resume paths — which have no replay mode — run with
`run_agent` patched to raise, so a live opencode call is impossible.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.state import db

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "factory"


def _spec(title: str, questions: list[str] | None = None) -> str:
    return json.dumps({
        "title": title, "problem": "p", "why": "w",
        "acceptance_criteria": ["ac one", "ac two"],
        "tasks": [{"id": "T-1", "title": "do", "purpose": "x", "scope": [],
                   "completion_evidence": "done", "depends_on": []}],
        "verdict": "pass", "questions": questions or [],
    })


def _blocked(*_a, **_k):
    raise RuntimeError("LIVE CALL BLOCKED")


class _ServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "factory.db"
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        db.init_db(self.db_path)
        self.events: list = []
        # Safety net for the whole class: no test may reach a model.
        guard = patch("factory.pipeline.agent_calls.run_agent", _blocked)
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _status(self, run_id: int) -> str:
        with db.get_db(self.db_path) as conn:
            return conn.execute(
                "SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)
            ).fetchone()["status"]

    def _parked_at_gate1(self, story: str = "US-0001") -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, story, "Parked", "make it fast")
            rid = db.start_run(conn, story)
            db.log_agent(conn, rid, "spec-agent", "in", _spec("Parked", ["how fast?"]),
                         verdict="pass")
            db.log_gate(conn, rid, "gate-1-spec", True, "open questions",
                        needs_human=True, human_questions="how fast?")
            db.finish_run(conn, rid, "waiting_human")
        return rid


class RunPipelineTests(_ServiceCase):
    def test_a_replayed_run_reports_each_node_through_events_and_prints_nothing(self) -> None:
        from factory import runs

        source = self._parked_at_gate1()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            outcome = runs.run_pipeline(
                "make it fast", opencode_cwd=str(self.cwd), db_path=self.db_path,
                replay_run_id=source, on_event=self.events.append,
            )

        self.assertEqual(out.getvalue(), "", "the service must never print")
        kinds = [type(e).__name__ for e in self.events]
        self.assertEqual(kinds[0], "RunStarted")
        self.assertEqual(kinds[-1], "RunFinished")
        nodes = [e.node for e in self.events if isinstance(e, runs.NodeCompleted)]
        self.assertEqual(nodes, ["spec-agent", "gate-1"])
        started = self.events[0]
        self.assertEqual(started.replay_of, source)
        self.assertEqual(started.request, "make it fast")
        self.assertEqual(outcome.status, "waiting_human")
        self.assertEqual(outcome.run_id, started.run_id)
        self.assertIs(self.events[-1].outcome, outcome)
        self.assertEqual(self._status(outcome.run_id), "waiting_human")

    def test_no_callback_is_silent_and_still_returns_the_outcome(self) -> None:
        from factory import runs

        source = self._parked_at_gate1()
        outcome = runs.run_pipeline("make it fast", opencode_cwd=str(self.cwd),
                                    db_path=self.db_path, replay_run_id=source)
        self.assertEqual(outcome.status, "waiting_human")

    def test_replaying_an_unknown_run_is_refused(self) -> None:
        from factory import runs

        with self.assertRaises(runs.RunError) as raised:
            runs.replay_run(99, db_path=self.db_path)
        self.assertEqual(str(raised.exception), "No run found with id #99")


class ResumeRunTests(_ServiceCase):
    def test_a_run_not_awaiting_review_is_refused_and_left_untouched(self) -> None:
        from factory import runs

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Done", "ship")
            rid = db.start_run(conn, "US-0001")
            db.finish_run(conn, rid, "completed")

        with self.assertRaises(runs.RunError) as raised:
            runs.resume_run(rid, "approve", db_path=self.db_path)
        self.assertEqual(
            str(raised.exception),
            f"Run #{rid} is not waiting for human approval (status: completed)",
        )
        self.assertEqual(self._status(rid), "completed")

    def test_a_missing_spec_log_parks_the_run_instead_of_stranding_it(self) -> None:
        from factory import runs

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "req")
            rid = db.start_run(conn, "US-0001")
            db.log_gate(conn, rid, "gate-1-spec", True, "parked",
                        needs_human=True, human_questions="q?")
            db.finish_run(conn, rid, "waiting_human")

        with self.assertRaises(runs.RunError) as raised:
            runs.resume_run(rid, "approve", db_path=self.db_path)
        self.assertEqual(str(raised.exception), "Cannot resume: missing spec log")
        self.assertEqual(self._status(rid), "blocked")

    def test_resume_announces_its_entry_then_streams_nodes_silently(self) -> None:
        from factory import runs

        rid = self._parked_at_gate1()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            outcome = runs.resume_run(rid, "approve", db_path=self.db_path,
                                      on_event=self.events.append)

        self.assertEqual(out.getvalue(), "")
        entered = self.events[0]
        self.assertIsInstance(entered, runs.ResumeEntered)
        self.assertEqual((entered.run_id, entered.entry, entered.action),
                         (rid, "architect", "approve"))
        self.assertEqual(entered.decision, "Approved by human reviewer")
        nodes = [e.node for e in self.events if isinstance(e, runs.NodeCompleted)]
        self.assertEqual(nodes[0], "architect-agent")
        self.assertIsInstance(self.events[-1], runs.RunFinished)
        self.assertIn(outcome.status, ("failed", "blocked"))  # the blocked live call
        with db.get_db(self.db_path) as conn:
            gate = db.get_answered_human_gate(conn, rid)
        self.assertEqual(gate["human_response"], "APPROVED: Approved by human reviewer")


class RetryRunTests(_ServiceCase):
    def test_retry_replays_the_recorded_decision(self) -> None:
        from factory import runs

        rid = self._parked_at_gate1()
        with db.get_db(self.db_path) as conn:
            gate = db.get_pending_human_gate(conn, rid)
            db.respond_to_gate(conn, gate["id"], "REJECTED: under 100ms")
            db.finish_run(conn, rid, "blocked", error="provider blew up")

        runs.retry_run(rid, db_path=self.db_path, on_event=self.events.append)

        retry, entered = self.events[0], self.events[1]
        self.assertIsInstance(retry, runs.RetryStarted)
        self.assertEqual((retry.gate_name, retry.action, retry.feedback),
                         ("gate-1-spec", "reject", "under 100ms"))
        self.assertIsInstance(entered, runs.ResumeEntered)
        self.assertEqual((entered.entry, entered.decision), ("spec", "under 100ms"))

    def test_retry_refuses_a_run_that_is_not_blocked_or_failed(self) -> None:
        from factory import runs

        rid = self._parked_at_gate1()
        with self.assertRaises(runs.RunError) as raised:
            runs.retry_run(rid, db_path=self.db_path)
        self.assertIn("is 'waiting_human', not blocked/failed", str(raised.exception))
        self.assertEqual(self._status(rid), "waiting_human")


class ServiceIsHeadlessTests(unittest.TestCase):
    """`runs` is shared by every interface, so it may not render anything itself."""

    def test_runs_imports_no_rendering_library_and_never_prints(self) -> None:
        for path in sorted((PACKAGE / "runs").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                with self.subTest(file=path.name, line=getattr(node, "lineno", 0)):
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        names = []
                    for name in names:
                        self.assertFalse(name.split(".")[0] in ("rich", "textual"), name)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotEqual(node.func.id, "print")


if __name__ == "__main__":
    unittest.main()
