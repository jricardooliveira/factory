"""PIPELINE.md — the boss's committed record of a run (the brief's "local pipeline file").

The DB is the live record and it is gitignored; INTENT/SPEC/PLAN say what was
asked and planned, the trust package exists only when a run COMPLETES. A run
that failed, was refused or is waiting for the operator left nothing in the
product repo saying why. This file is that record: every authorization, agent
verdict and gate verdict in order, the blockers, and the next authorized step.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.evidence.pipeline_record import render_pipeline_record, write_pipeline_record
from factory.state import db


class PipelineRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Overdue tickets", "flag overdue tickets")
            self.run_id = db.start_run(conn, "US-0001")
            db.log_agent(conn, self.run_id, "spec-agent", "in", "{}", verdict="pass")
            db.log_gate(conn, self.run_id, "gate-1-spec", True, "Story has 3 AC | 2 tasks")
            db.log_authorization(conn, self.run_id, "architect-agent", True,
                                 granted_by="gate-1-spec passed")
            db.log_agent(conn, self.run_id, "architect-agent", "in", "{}", verdict="pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _render(self) -> str:
        text = render_pipeline_record(self.db_path, self.run_id)
        assert text is not None
        return text

    def test_a_parked_run_names_the_question_and_the_two_commands(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.log_gate(conn, self.run_id, "gate-2-architect", True, "needs sign-off",
                        needs_human=True, human_questions="🔗 EXTERNAL DEPENDENCIES: SMTP")
            db.finish_run(conn, self.run_id, "waiting_human")
        text = self._render()
        self.assertIn("US-0001", text)
        self.assertIn("Overdue tickets", text)
        self.assertIn("waiting_human", text)
        self.assertIn("EXTERNAL DEPENDENCIES: SMTP", text)
        self.assertIn(f"factory approve {self.run_id}", text)
        self.assertIn(f"factory reject {self.run_id}", text)

    def test_the_trail_shows_authorizations_agents_and_gates_in_order(self) -> None:
        text = self._render()
        trail = text[text.index("## Trail"):]
        order = [trail.index(s) for s in
                 ("| agent | spec-agent |", "| gate | gate-1-spec |",
                  "| boss | authorize architect-agent |", "| agent | architect-agent |")]
        self.assertEqual(order, sorted(order))
        self.assertIn("gate-1-spec passed", trail)

    def test_a_refusal_is_a_blocker_and_nothing_is_authorized_next(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.log_authorization(conn, self.run_id, "coder-agent:T-2", False,
                                 missing=["T-1 (a dependency of T-2) to be implemented first"])
            db.finish_run(conn, self.run_id, "blocked", error="Boss: coder-agent:T-2 refused")
        text = self._render()
        blockers = text[text.index("## Blockers"):text.index("## Trail")]
        self.assertIn("T-1 (a dependency of T-2) to be implemented first", blockers)
        self.assertIn("Boss: coder-agent:T-2 refused", blockers)
        self.assertIn("None", text[text.index("## Next authorized step"):])

    def test_a_completed_run_points_at_its_release_evidence(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.finish_run(conn, self.run_id, "completed")
        text = self._render()
        self.assertIn(f"docs/releases/run-{self.run_id}-trust-package.json", text)

    def test_warnings_are_listed_but_are_not_blockers(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.log_authorization(conn, self.run_id, "coder-agent:T-1", True,
                                 granted_by="gate-2-architect passed",
                                 warnings=["T-1 declares no allowed scope"])
            db.finish_run(conn, self.run_id, "completed")
        text = self._render()
        self.assertIn("T-1 declares no allowed scope", text[text.index("## Warnings"):])
        self.assertNotIn("T-1 declares no allowed scope",
                         text[text.index("## Blockers"):text.index("## Trail")])

    def test_table_cells_cannot_break_the_table(self) -> None:
        trail = self._render().split("## Trail", 1)[1]
        self.assertIn("3 AC \\| 2 tasks", trail)

    def test_it_is_written_into_the_story_work_folder(self) -> None:
        path = write_pipeline_record(self.db_path, self.run_id, self.root / "repo")
        self.assertEqual(path, self.root / "repo" / "docs" / "work" / "US-0001" / "PIPELINE.md")
        self.assertIn("## Trail", path.read_text(encoding="utf-8"))

    def test_an_unknown_run_writes_nothing(self) -> None:
        self.assertIsNone(write_pipeline_record(self.db_path, 999, self.root / "repo"))


if __name__ == "__main__":
    unittest.main()
