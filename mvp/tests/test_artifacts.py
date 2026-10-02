"""The committed artifact chain: INTENT -> SPEC -> PLAN -> ADR -> diff.

The playbook's central governance claim is that each stage commits an artifact the
next stage reads, and that the chain IS the audit trail: "evidence is
version-controlled and timestamped".

This factory committed exactly one link — the ADR. Everything else lived only in
`agent_logs.output_text` inside a `*.db` file that `.gitignore` excludes, so the
requirements a design answered were unrecoverable once the DB was gone, and nothing
carried an author or a timestamp. These tests pin the missing links.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory import artifacts
from factory.models import ArchitectOutput, SpecOutput


def _spec() -> SpecOutput:
    return SpecOutput.model_validate({
        "story_id": "US-0007",
        "title": "Bookmark search",
        "type": "feature",
        "problem": "there is no way to find a bookmark once the list passes 50 entries",
        "why": "the list becomes unusable and users abandon the tool",
        "acceptance_criteria": [
            "searching by title returns matching bookmarks",
            "results are paginated at 20 per page",
        ],
        "non_goals": ["full-text search of page contents"],
        "tasks": [
            {"id": "T-1", "title": "search repository method", "purpose": "query layer",
             "scope": ["src/bookmarks/repositories.py"], "completion_evidence": "unit test passes",
             "depends_on": []},
            {"id": "T-2", "title": "search endpoint", "purpose": "expose it",
             "scope": ["src/bookmarks/routes.py"], "completion_evidence": "API test passes",
             "depends_on": ["T-1"]},
        ],
        "verdict": "pass",
        "questions": [],
    })


def _architect() -> ArchitectOutput:
    return ArchitectOutput.model_validate({
        "verdict": "pass",
        "architecture_notes": "extend the existing repository; no new module",
        "modules_affected": ["src/bookmarks/repositories.py", "src/bookmarks/routes.py"],
        "implementation_constraints": ["reuse the existing session dependency"],
        "risks": ["an unindexed LIKE query degrades past 10k rows"],
        "db_impact": "no", "api_impact": "yes", "migration_needed": "no",
        "breaking_changes": [],
    })


class IntentTests(unittest.TestCase):
    def test_intent_records_the_raw_ask_with_an_author_and_a_date(self) -> None:
        text = artifacts.render_intent(
            "US-0007", "let me search my bookmarks", author="Jo", created_at="2026-08-27",
            project_id="PROJ-003",
        )
        self.assertIn("let me search my bookmarks", text)
        self.assertIn("Jo", text)
        self.assertIn("2026-08-27", text)
        self.assertIn("PROJ-003", text)

    def test_intent_carries_the_playbook_headings(self) -> None:
        text = artifacts.render_intent("US-0007", "x", author="Jo", created_at="2026-08-27")
        for heading in ("## Problem", "## Proposed outcome", "## Affected users and systems",
                        "## Constraints", "## Open questions"):
            self.assertIn(heading, text)

    def test_write_intent_is_deterministic_and_replay_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            first = artifacts.write_intent(proj, "US-0007", "search my bookmarks")
            second = artifacts.write_intent(proj, "US-0007", "search my bookmarks")
            self.assertEqual(first, second, "a replay must overwrite, not accumulate files")
            self.assertIn("search my bookmarks", first.read_text(encoding="utf-8"))


class SpecArtifactTests(unittest.TestCase):
    def test_spec_artifact_records_what_the_design_must_answer(self) -> None:
        text = artifacts.render_spec("US-0007", _spec())
        self.assertIn("Bookmark search", text)
        self.assertIn("there is no way to find a bookmark", text)
        self.assertIn("searching by title returns matching bookmarks", text)
        self.assertIn("full-text search of page contents", text)  # non-goal
        self.assertIn("T-1", text)
        self.assertIn("T-2", text)

    def test_open_questions_are_recorded_not_dropped(self) -> None:
        spec = _spec()
        spec.questions = ["Which auth model?"]
        self.assertIn("Which auth model?", artifacts.render_spec("US-0007", spec))

    def test_write_spec_lands_next_to_the_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            intent = artifacts.write_intent(proj, "US-0007", "search")
            spec_path = artifacts.write_spec(proj, "US-0007", _spec())
            self.assertEqual(spec_path.parent, intent.parent)
            self.assertTrue(spec_path.is_file())


class PlanTests(unittest.TestCase):
    """plan.md's four headings, filled from data the factory ALREADY computes:
    `order_tasks` gives the order of work, `TaskDef.scope` the files that change,
    `ArchitectOutput.risks` the risks, `completion_evidence` the proof."""

    def test_plan_has_the_playbook_headings(self) -> None:
        text = artifacts.render_plan("US-0007", _spec(), _architect())
        for heading in ("## Files that change", "## Order of work", "## Risks", "## Proof"):
            self.assertIn(heading, text)

    def test_order_of_work_respects_task_dependencies(self) -> None:
        text = artifacts.render_plan("US-0007", _spec(), _architect())
        order_section = text.split("## Order of work", 1)[1].split("##", 1)[0]
        self.assertLess(
            order_section.index("T-1"), order_section.index("T-2"),
            "T-2 depends on T-1, so the committed plan must order them that way",
        )

    def test_files_that_change_come_from_declared_task_scope(self) -> None:
        text = artifacts.render_plan("US-0007", _spec(), _architect())
        files_section = text.split("## Files that change", 1)[1].split("##", 1)[0]
        self.assertIn("src/bookmarks/repositories.py", files_section)
        self.assertIn("src/bookmarks/routes.py", files_section)

    def test_proof_lists_the_completion_evidence_per_task(self) -> None:
        proof = artifacts.render_plan("US-0007", _spec(), _architect()).split("## Proof", 1)[1]
        self.assertIn("unit test passes", proof)
        self.assertIn("API test passes", proof)

    def test_risks_come_from_the_architecture(self) -> None:
        text = artifacts.render_plan("US-0007", _spec(), _architect())
        self.assertIn("unindexed LIKE query", text)

    def test_plan_declares_the_scope_the_diff_is_checked_against(self) -> None:
        """The plan is only binding if something compares it to what landed —
        `trust_package` checks the git diff against this same declared scope."""
        self.assertEqual(
            artifacts.planned_scope(_spec()),
            ["src/bookmarks/repositories.py", "src/bookmarks/routes.py"],
        )


class ChainTests(unittest.TestCase):
    def test_the_whole_chain_lands_in_one_discoverable_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            artifacts.write_intent(proj, "US-0007", "search my bookmarks")
            artifacts.write_spec(proj, "US-0007", _spec())
            artifacts.write_plan(proj, "US-0007", _spec(), _architect())
            work = artifacts.work_dir_for(proj, "US-0007")
            self.assertEqual(
                sorted(p.name for p in work.glob("*.md")),
                ["INTENT.md", "PLAN.md", "SPEC.md"],
            )

    def test_chain_is_not_written_without_a_project_dir(self) -> None:
        """Ad-hoc runs with no project have nowhere durable to write; they must
        no-op rather than scatter files into the cwd."""
        self.assertIsNone(artifacts.write_intent(None, "US-0007", "x"))
        self.assertIsNone(artifacts.write_spec(None, "US-0007", _spec()))
        self.assertIsNone(artifacts.write_plan(None, "US-0007", _spec(), _architect()))


if __name__ == "__main__":
    unittest.main()


class ChainWiringTests(unittest.TestCase):
    """The chain must actually be produced by a run, not merely be producible."""

    def test_a_project_run_leaves_the_whole_chain_on_disk(self) -> None:
        import json as _json

        from factory.pipeline import compile_pipeline
        from factory.state import db

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "PROJ-001-demo"
            repo = project_dir / "repo"
            repo.mkdir(parents=True)
            db_path = root / "f.db"
            db.init_db(db_path)

            spec_out = _json.dumps({
                "title": "Bookmark search", "problem": "cannot find bookmarks",
                "why": "the list is unusable", "non_goals": ["full-text search"],
                "acceptance_criteria": ["title search works", "results paginate"],
                "tasks": [{"id": "T-1", "title": "search", "purpose": "query",
                           "scope": ["src/search.py"], "completion_evidence": "test passes"}],
                "verdict": "pass", "questions": [],
            })
            arch_out = _json.dumps({
                "verdict": "pass", "architecture_notes": "extend the repository",
                "modules_affected": ["src/search.py"], "risks": ["unindexed LIKE"],
            })
            coder_out = _json.dumps({"verdict": "complete", "code_blocks": [
                {"path": "src/search.py", "content": "def s():\n    return []\n",
                 "action": "create"}]})
            tester_out = _json.dumps({
                "overall": "pass", "qa_verdict": "pass",
                "ac_coverage": ["title search works", "results paginate"],
                "security_verdict": "pass", "highest_severity": "none",
                "performance_verdict": "pass", "summary": "ok"})

            with db.get_db(db_path) as conn:
                db.create_story(conn, "US-0001", "Pending", "let me search my bookmarks")
                orig = db.start_run(conn, "US-0001")
                db.log_agent(conn, orig, "spec-agent", "in", spec_out, verdict="pass")
                db.log_agent(conn, orig, "architect-agent", "in", arch_out, verdict="pass")
                db.log_agent(conn, orig, "coder-agent", "in", coder_out,
                             verdict="complete", stage_type="T-1")
                db.log_agent(conn, orig, "tester-agent", "in", tester_out, verdict="pass")
                new = db.start_run(conn, "US-0001")

            state = {"request": "let me search my bookmarks", "story_id": "US-0001",
                     "run_id": new, "db_path": str(db_path), "opencode_cwd": str(repo),
                     "project_dir": str(project_dir), "replay_run_id": orig}
            for _event in compile_pipeline().stream(state):
                pass

            work = artifacts.work_dir_for(project_dir, "US-0001")
            self.assertEqual(sorted(p.name for p in work.glob("*.md")),
                             ["INTENT.md", "PLAN.md", "SPEC.md"])
            # The chain must be readable end to end by someone with no DB.
            self.assertIn("let me search my bookmarks",
                          (work / "INTENT.md").read_text(encoding="utf-8"))
            self.assertIn("title search works", (work / "SPEC.md").read_text(encoding="utf-8"))
            plan = (work / "PLAN.md").read_text(encoding="utf-8")
            self.assertIn("src/search.py", plan)
            self.assertIn("unindexed LIKE", plan)
            # And the ADR, the pre-existing link, still lands beside it.
            adrs = list((project_dir / "docs" / "architecture" / "adr").glob("ADR-*.md"))
            self.assertEqual(len(adrs), 1)
