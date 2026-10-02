"""Resume must rebuild state from the LATEST agent output, and a failed architect
must not orphan its run.

Two defects, both about the operator being told the truth:

1. `resume_run` rebuilt its state with `next((l for l in logs if ...))` over
   `get_run_logs`, which is `ORDER BY id` ASCENDING — so it picked the OLDEST log.
   On the reject → re-architect → park-again → approve path, `factory review`
   shows the operator the newest design while the coder is handed the ORIGINAL,
   rejected one. The human approves one artifact and a different one proceeds,
   which is exactly the separation-of-duties guarantee inverted.

2. `node_architect_agent` returns `{"status": "failed"}` from its off-script-JSON
   branch and its exception handler without calling `finish_run` — the same
   orphaned-'running' bug that was fixed at gate-1. The run matches neither
   `factory queue` filter and is swept up an hour later by `reconcile_stale_runs`
   as "process likely died", misreporting a decision as a crash.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.state import db


class LatestAgentLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resume_state_uses_the_newest_architect_output(self) -> None:
        from factory.runs import build_resume_context

        rejected = json.dumps({"verdict": "pass", "architecture_notes": "THE REJECTED DESIGN",
                               "modules_affected": ["old.py"]})
        revised = json.dumps({"verdict": "pass", "architecture_notes": "THE REVISED DESIGN",
                              "modules_affected": ["new.py"]})
        spec_v1 = json.dumps({"title": "V1", "problem": "p", "why": "w",
                              "acceptance_criteria": ["a", "b"], "tasks": []})
        spec_v2 = json.dumps({"title": "V2", "problem": "p", "why": "w",
                              "acceptance_criteria": ["a", "b"], "tasks": []})
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "do it")
            rid = db.start_run(conn, "US-0001")
            db.log_agent(conn, rid, "spec-agent", "in", spec_v1, verdict="pass")
            db.log_agent(conn, rid, "architect-agent", "in", rejected, verdict="pass")
            # the operator rejected, the architect ran again:
            db.log_agent(conn, rid, "spec-agent", "in", spec_v2, verdict="pass")
            db.log_agent(conn, rid, "architect-agent", "in", revised, verdict="pass")

        with db.get_db(self.db_path) as conn:
            spec, arch = build_resume_context(conn, rid)

        self.assertEqual(arch["architecture_notes"], "THE REVISED DESIGN",
                         "the coder must build the design the operator just approved")
        self.assertEqual(spec["title"], "V2")

    def test_resume_context_is_none_when_a_stage_never_ran(self) -> None:
        from factory.runs import build_resume_context

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0002", "S", "do it")
            rid = db.start_run(conn, "US-0002")
            db.log_agent(conn, rid, "spec-agent", "in",
                         json.dumps({"title": "T", "problem": "p", "why": "w"}), verdict="pass")
            spec, arch = build_resume_context(conn, rid)
        self.assertIsNotNone(spec)
        self.assertIsNone(arch, "a checkpoint-1 park has no architecture yet")


class ArchitectFailurePersistenceTests(unittest.TestCase):
    """An architect failure must be recorded, findable, and attributed correctly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "f.db"
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _drive(self, architect_output: str) -> tuple[int, dict, str]:
        from factory.pipeline import compile_pipeline

        spec = json.dumps({
            "title": "Fine story", "problem": "p", "why": "w",
            "acceptance_criteria": ["ac one", "ac two"],
            "tasks": [{"id": "T-1", "title": "t", "purpose": "p"}],
            "verdict": "pass", "questions": [],
        })
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "do it")
            orig = db.start_run(conn, "US-0001")
            db.log_agent(conn, orig, "spec-agent", "in", spec, verdict="pass")
            db.log_agent(conn, orig, "architect-agent", "in", architect_output, verdict="pass")
            new = db.start_run(conn, "US-0001")
        state = {"request": "do it", "story_id": "US-0001", "run_id": new,
                 "db_path": str(self.db_path), "opencode_cwd": str(self.cwd),
                 "replay_run_id": orig}
        for _event in compile_pipeline().stream(state):
            pass
        with db.get_db(self.db_path) as conn:
            run = dict(conn.execute(
                "SELECT status, finished_at, error FROM pipeline_runs WHERE id = ?",
                (new,)).fetchone())
            story = conn.execute(
                "SELECT status FROM stories WHERE id = 'US-0001'").fetchone()["status"]
        return new, run, story

    def test_off_script_architect_finishes_the_run(self) -> None:
        rid, run, story = self._drive("I'm afraid I can't design that. Here is some prose.")
        self.assertEqual(run["status"], "failed",
                         "an off-script architect must not leave the run stuck 'running'")
        self.assertIsNotNone(run["finished_at"])
        self.assertTrue(run["error"])
        self.assertEqual(story, "failed")

    def test_an_orphaned_architect_failure_is_findable_by_the_operator(self) -> None:
        rid, _run, _story = self._drive("not json at all")
        with db.get_db(self.db_path) as conn:
            attention = [r["id"] for r in db.get_runs_by_status(conn, ["failed", "blocked"])]
        self.assertIn(rid, attention,
                      "the operator must see this in `factory queue`, not an hour later "
                      "via reconcile as a 'dead process'")

    def test_a_spec_failure_is_not_double_finished_by_the_architect(self) -> None:
        """The architect's early-return guard must stay a pass-through."""
        spec_bad = json.dumps({
            "title": "Thin", "problem": "p", "why": "w",
            "acceptance_criteria": ["only one"],
            "tasks": [{"id": "T-1", "title": "t", "purpose": "p"}],
            "verdict": "pass", "questions": [],
        })
        from factory.pipeline import compile_pipeline

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0003", "S", "do it")
            orig = db.start_run(conn, "US-0003")
            db.log_agent(conn, orig, "spec-agent", "in", spec_bad, verdict="pass")
            new = db.start_run(conn, "US-0003")
        state = {"request": "do it", "story_id": "US-0003", "run_id": new,
                 "db_path": str(self.db_path), "opencode_cwd": str(self.cwd),
                 "replay_run_id": orig}
        for _event in compile_pipeline().stream(state):
            pass
        with db.get_db(self.db_path) as conn:
            run = dict(conn.execute(
                "SELECT status, error FROM pipeline_runs WHERE id = ?", (new,)).fetchone())
        self.assertEqual(run["status"], "failed")
        self.assertIn("acceptance criteria", run["error"],
                      "the gate-1 reason must survive, not be overwritten by the architect")


if __name__ == "__main__":
    unittest.main()


class UsableLogRecoveryTests(unittest.TestCase):
    """Resume must take the newest log that PARSES, not the newest row.

    "Read the LATEST agent log" is right when every row is a real artifact. But a
    failed call is also a row: when a provider returned "Unexpected server error"
    the spec-agent's newest log held that error text, `build_resume_context`
    parsed it to None, and the retry aborted with "Cannot resume: missing spec
    log" — stranding the operator's answer a SECOND time. A failed attempt is not
    an artifact; the last usable one is.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_an_error_row_is_skipped_in_favour_of_the_last_good_one(self) -> None:
        from factory.runs import build_resume_context

        good = json.dumps({"title": "Overdue", "problem": "p", "why": "w",
                           "acceptance_criteria": ["a", "b"], "tasks": []})
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0020", "S", "x")
            rid = db.start_run(conn, "US-0020")
            db.log_agent(conn, rid, "spec-agent", "in", good, verdict="pass")
            db.log_agent(conn, rid, "spec-agent", "in",
                         "ERROR: Unexpected server error. Check server logs.",
                         verdict="blocked")
            spec, _arch = build_resume_context(conn, rid)
        self.assertIsNotNone(spec, "a provider error must not erase the real story")
        self.assertEqual(spec["title"], "Overdue")

    def test_a_newer_GOOD_log_still_wins(self) -> None:
        """Skipping errors must not resurrect the old 'oldest log' bug."""
        from factory.runs import build_resume_context

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0021", "S", "x")
            rid = db.start_run(conn, "US-0021")
            for title in ("FIRST", "SECOND"):
                db.log_agent(conn, rid, "spec-agent", "in",
                             json.dumps({"title": title, "problem": "p", "why": "w",
                                         "acceptance_criteria": ["a", "b"], "tasks": []}),
                             verdict="pass")
            spec, _arch = build_resume_context(conn, rid)
        self.assertEqual(spec["title"], "SECOND")

    def test_all_rows_unusable_yields_none(self) -> None:
        from factory.runs import build_resume_context

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0022", "S", "x")
            rid = db.start_run(conn, "US-0022")
            db.log_agent(conn, rid, "spec-agent", "in", "ERROR: boom", verdict="error")
            spec, _arch = build_resume_context(conn, rid)
        self.assertIsNone(spec)


class ResumeBailoutTests(unittest.TestCase):
    """A resume that cannot proceed must not leave the run 'running'.

    `resume_run` flips the status to 'running' before rebuilding context, then
    returns early on a missing artifact — leaving the run stuck 'running' and
    invisible to `factory queue`, the same orphan class fixed at gate-1 and the
    architect. Observed live on a retry.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_an_unresumable_run_is_parked_not_left_running(self) -> None:
        from factory.runs import park_unresumable

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0023", "S", "x")
            rid = db.start_run(conn, "US-0023")
            conn.execute("UPDATE pipeline_runs SET status='running' WHERE id=?", (rid,))
            conn.commit()
            park_unresumable(conn, rid, "missing spec log")
            row = dict(conn.execute(
                "SELECT status, error, finished_at FROM pipeline_runs WHERE id=?",
                (rid,)).fetchone())
        self.assertEqual(row["status"], "blocked")
        self.assertIn("missing spec log", row["error"])
        self.assertIsNotNone(row["finished_at"])
