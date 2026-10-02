"""The boss in the graph: every agent stage is authorized before it starts.

The rules are pure (tests/domain/test_authorization.py); this is the wiring —
read the recorded gate verdicts, store the decision, and on a refusal BLOCK the
run (persisted, never left 'running') without calling the agent. Verdicts come
from the DB, not graph state, because a resumed run rebuilds its state from logs
and carries no gate dicts at all.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.pipeline.boss import authorized
from factory.pipeline.graph import compile_architect_resume_pipeline
from factory.state import db

SPEC = {
    "title": "Search", "problem": "p", "why": "w",
    "acceptance_criteria": ["finds by title", "paginates"],
    "tasks": [
        {"id": "T-1", "title": "model", "purpose": "the model", "scope": ["src/"],
         "completion_evidence": "test"},
        {"id": "T-2", "title": "api", "purpose": "the api", "scope": ["src/"],
         "completion_evidence": "test", "depends_on": ["T-1"]},
    ],
}
ARCH = {"verdict": "pass", "architecture_notes": "layered", "modules_affected": ["src/"]}


class BossTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Story", "req")
            self.run_id = db.start_run(conn, "US-0001")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, **extra) -> dict:
        state = {"run_id": self.run_id, "db_path": str(self.db_path), "story_id": "US-0001",
                 "request": "req", "spec": SPEC, "architect": ARCH}
        state.update(extra)
        return state

    def _gate(self, name: str, passed: bool, *, needs_human: bool = False,
              response: str | None = None) -> None:
        with db.get_db(self.db_path) as conn:
            gid = db.log_gate(conn, self.run_id, name, passed, "r", needs_human=needs_human)
            if response:
                db.respond_to_gate(conn, gid, response)

    def _authorizations(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return db.get_run_authorizations(conn, self.run_id)

    def _run(self) -> dict:
        with db.get_db(self.db_path) as conn:
            return db.get_run(conn, self.run_id)

    # ── allowed ──────────────────────────────────────────────────────

    def test_an_authorized_stage_runs_and_the_grant_is_recorded(self) -> None:
        self._gate("gate-1-spec", True)
        node = MagicMock(return_value={"architect": ARCH})
        out = authorized("architect-agent", node)(self._state())
        node.assert_called_once()
        self.assertEqual(out, {"architect": ARCH})
        [auth] = self._authorizations()
        self.assertTrue(auth["allowed"])
        self.assertEqual(auth["granted_by"], "gate-1-spec passed")

    def test_an_operator_approval_found_only_in_the_db_authorizes_a_resume(self) -> None:
        # A coder-only resume carries spec + architect but NO gate dicts.
        self._gate("gate-2-architect", True, needs_human=True, response="APPROVED: ok")
        node = MagicMock(return_value={})
        authorized("coder-agent", node)(self._state(task_index=0, tasks_completed=[]))
        node.assert_called_once()
        self.assertIn("operator", self._authorizations()[0]["granted_by"])

    def test_a_remediation_pass_is_authorized_by_the_failed_test_gate(self) -> None:
        self._gate("gate-test", False)
        node = MagicMock(return_value={})
        authorized("coder-agent", node)(
            self._state(remediation=True, prior_findings=["missing negative test"])
        )
        node.assert_called_once()
        self.assertEqual(self._authorizations()[0]["stage"], "coder-agent:remediation")

    def test_the_tester_is_authorized_once_every_task_is_built(self) -> None:
        self._gate("gate-build", True)
        node = MagicMock(return_value={})
        authorized("tester-agent", node)(self._state(tasks_completed=["T-1", "T-2"]))
        node.assert_called_once()

    # ── refused ──────────────────────────────────────────────────────

    def test_a_refusal_blocks_the_run_without_calling_the_agent(self) -> None:
        node = MagicMock()
        out = authorized("architect-agent", node)(self._state())  # gate-1 never ran
        node.assert_not_called()
        self.assertEqual(out["status"], "blocked")
        self.assertIn("gate-1-spec", out["error"])
        run = self._run()
        self.assertEqual(run["status"], "blocked")  # persisted, not left 'running'
        self.assertIn("gate-1-spec", run["error"])
        [auth] = self._authorizations()
        self.assertFalse(auth["allowed"])

    def test_a_task_whose_dependency_is_not_built_is_refused(self) -> None:
        self._gate("gate-2-architect", True)
        node = MagicMock()
        # Index 1 in dependency order is T-2, and T-1 was never implemented.
        out = authorized("coder-agent", node)(self._state(task_index=1, tasks_completed=[]))
        node.assert_not_called()
        self.assertEqual(out["next_action"], "give_up")
        self.assertIn("T-1", self._authorizations()[0]["missing"][0])

    def test_the_tester_is_refused_while_a_task_is_unbuilt(self) -> None:
        self._gate("gate-build", True)
        node = MagicMock()
        authorized("tester-agent", node)(self._state(tasks_completed=["T-1"]))
        node.assert_not_called()

    # ── already stopped ──────────────────────────────────────────────

    def test_a_stopped_run_is_not_re_judged(self) -> None:
        node = MagicMock(return_value={"status": "failed"})
        authorized("architect-agent", node)(self._state(status="failed"))
        node.assert_called_once()  # the node's own guard handles a stopped run
        self.assertEqual(self._authorizations(), [])


class BossInTheGraphTests(BossTests):
    def test_a_refused_architect_stops_the_line_cleanly(self) -> None:
        # No gate-1 verdict on record: the boss refuses the architect, and the
        # unconditional architect -> gate-2 edge must not crash on the missing design.
        with patch("factory.pipeline.agent_calls.run_agent",
                   side_effect=AssertionError("no agent may be called")):
            final: dict = {}
            state = self._state()
            state.pop("architect")
            for event in compile_architect_resume_pipeline().stream(state, stream_mode="updates"):
                for update in event.values():
                    final.update(update or {})
        self.assertEqual(final["status"], "blocked")
        self.assertEqual(self._run()["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
