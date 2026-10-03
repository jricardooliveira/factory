"""The playbook's SDLC indicators, computed from state the factory already keeps.

Every fact needed to judge the factory — verdicts, gate outcomes, retries, cost,
model provenance — is captured per run in `agent_logs` / `gate_results`, and
NOTHING aggregated across runs. The playbook's metrics table is the missing
consumer.

The other half of this module's job is to say what it CANNOT measure. Reporting a
cost of $0.00 when 47 of 47 agent-log rows carry a NULL cost is worse than
reporting nothing: it makes the `$1` remediation budget in `gates.py` look
enforced when it has never once bound.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.evidence import metrics
from factory.state import db


class MetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, story: str, status: str, *, gates=(), coder_attempts=1,
             cost: float | None = None) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, story, "T", "x")
            rid = db.start_run(conn, story)
            db.log_agent(conn, rid, "spec-agent", "in", json.dumps({"title": "T"}),
                         verdict="pass", cost_usd=cost)
            for attempt in range(coder_attempts):
                db.log_agent(conn, rid, "coder-agent", "in", "{}", verdict="complete",
                             stage_type="T-1", cost_usd=cost)
            for name, passed in gates:
                db.log_gate(conn, rid, name, passed, "r")
            db.finish_run(conn, rid, status)
        return rid

    def test_throughput_counts_runs_by_terminal_state(self) -> None:
        self._run("US-1", "completed", gates=[("gate-1-spec", True)])
        self._run("US-2", "completed", gates=[("gate-1-spec", True)])
        self._run("US-3", "failed", gates=[("gate-1-spec", False)])
        m = metrics.compute(self.db_path)
        self.assertEqual(m.total_runs, 3)
        self.assertEqual(m.by_status["completed"], 2)
        self.assertEqual(m.by_status["failed"], 1)
        self.assertAlmostEqual(m.completion_rate, 2 / 3)

    def test_gate_pass_rate_is_reported_per_gate(self) -> None:
        self._run("US-1", "completed", gates=[("gate-1-spec", True), ("gate-build", True)])
        self._run("US-2", "failed", gates=[("gate-1-spec", True), ("gate-build", False)])
        m = metrics.compute(self.db_path)
        self.assertAlmostEqual(m.gate_pass_rate["gate-1-spec"], 1.0)
        self.assertAlmostEqual(m.gate_pass_rate["gate-build"], 0.5)

    def test_first_pass_rate_counts_stories_needing_no_coder_retry(self) -> None:
        """The playbook's Stage-3 leading indicator: share of changes merging from
        the FIRST implementation pass."""
        self._run("US-1", "completed", coder_attempts=1)
        self._run("US-2", "completed", coder_attempts=3)  # two retries
        m = metrics.compute(self.db_path)
        self.assertAlmostEqual(m.first_pass_rate, 0.5)
        self.assertEqual(m.total_coder_retries, 2)

    def test_cost_is_reported_as_unmeasurable_when_no_row_carries_one(self) -> None:
        self._run("US-1", "completed", cost=None)
        m = metrics.compute(self.db_path)
        self.assertFalse(m.cost_measurable)
        self.assertIn("cost", " ".join(m.not_measurable).lower())
        self.assertEqual(m.cost_coverage, 0.0)

    def test_cost_is_reported_when_rows_carry_one(self) -> None:
        self._run("US-1", "completed", cost=0.25)
        m = metrics.compute(self.db_path)
        self.assertTrue(m.cost_measurable)
        self.assertGreater(m.total_cost_usd, 0)
        self.assertAlmostEqual(m.cost_coverage, 1.0)

    def test_a_zero_dollar_subscription_is_not_reported_as_a_working_budget(self) -> None:
        """Observed live: a ChatGPT login reports cost 0 while tokens are spent. "$0.00"
        is then a true reading of currency, and a false one about the $ budget."""
        rid = self._run("US-1", "completed", cost=0.0)
        with db.get_db(self.db_path) as conn:
            db.log_agent(conn, rid, "tester-agent", "in", "{}", verdict="pass",
                         tokens_in=12000, tokens_out=300, cost_usd=0.0)
        m = metrics.compute(self.db_path)
        self.assertTrue(m.cost_measurable)
        self.assertEqual(m.total_tokens_in, 12000)
        text = " ".join(m.not_measurable).lower()
        self.assertIn("$0", text)
        self.assertIn("tokens", text)

    def test_replays_are_not_counted_as_delivered_work(self) -> None:
        """Review task T12: a replay re-drives frozen outputs — and logs their usage
        again — so counting it inflated throughput, gate rates and token totals."""
        live = self._run("US-1", "completed", gates=[("gate-test", True)], cost=0.1)
        with db.get_db(self.db_path) as conn:
            replay = db.start_run(conn, "US-1", replay_of=live)
            db.log_agent(conn, replay, "spec-agent", "in", "{}", verdict="pass", cost_usd=0.1,
                         tokens_in=500)
            db.log_gate(conn, replay, "gate-test", False, "r")
            db.finish_run(conn, replay, "failed")
        m = metrics.compute(self.db_path)
        self.assertEqual(m.total_runs, 1)
        self.assertEqual(m.by_status.get("failed", 0), 0)
        self.assertEqual(m.gate_counts["gate-test"], 1)
        self.assertAlmostEqual(m.total_cost_usd, 0.2)  # spec + coder of the LIVE run only
        self.assertEqual(m.replay_runs, 1)

    def test_human_checkpoint_count_and_pending_are_reported(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-9", "T", "x")
            rid = db.start_run(conn, "US-9")
            gid = db.log_gate(conn, rid, "gate-2-architect", True, "r",
                              needs_human=True, human_questions="approve?")
            db.finish_run(conn, rid, "waiting_human")
            # a second, already-answered checkpoint
            rid2 = db.start_run(conn, "US-9")
            gid2 = db.log_gate(conn, rid2, "gate-1-spec", True, "r",
                               needs_human=True, human_questions="which auth?")
            db.respond_to_gate(conn, gid2, "APPROVED: fine")
            db.finish_run(conn, rid2, "completed")
        m = metrics.compute(self.db_path)
        self.assertEqual(m.checkpoints_reached, 2)
        self.assertEqual(m.checkpoints_pending, 1)
        self.assertEqual(m.checkpoints_answered, 1)

    def test_empty_database_does_not_divide_by_zero(self) -> None:
        m = metrics.compute(self.db_path)
        self.assertEqual(m.total_runs, 0)
        # Nothing to divide by is "not applicable", not 0% (or 100%) of nothing.
        self.assertIsNone(m.completion_rate)
        self.assertIsNone(m.first_pass_rate)
        self.assertIsNone(m.runs_per_checkpoint)
        self.assertIn("n/a completed", metrics.render_markdown(m))

    def test_the_terminal_view_says_n_a_for_an_empty_rate(self) -> None:
        from factory.interfaces.render import output
        from factory.interfaces.render.selftest import print_metrics

        with output.console.capture() as captured:
            print_metrics(metrics.compute(self.db_path), self.db_path)
        text = " ".join(captured.get().split())
        self.assertIn("Completion rate: n/a", text)
        self.assertIn("First-pass rate (no coder retry): n/a", text)
        self.assertNotIn("0%", text)

    def test_render_names_the_unmeasurable_indicators_explicitly(self) -> None:
        self._run("US-1", "completed", cost=None)
        text = metrics.render_markdown(metrics.compute(self.db_path))
        self.assertIn("NOT MEASURABLE", text.upper())
        self.assertIn("cost", text.lower())


if __name__ == "__main__":
    unittest.main()
