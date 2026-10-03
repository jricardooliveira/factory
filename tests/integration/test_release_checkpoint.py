"""Checkpoint 3: a reviewed run waits for the operator to release it.

EFFECTIVENESS §2 parks the line at THREE checkpoints. Until now a run that
passed gate-test marked itself `completed` — the factory approving its own work.
Now: gate-test → release-agent (writes RELEASE.md, decides nothing) → gate-release
(names every evidence gap) → parks. Approve releases (the boss requires the
operator's approval on record); reject sends the operator's words to the coder
as findings, then the tester, then back to this checkpoint.

Driven offline through the real run service on frozen agent outputs.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import runs
from factory.evidence import artifacts
from factory.state import db
from factory.workspace.projects import create_project
from factory.workspace.sandbox import replay_sandbox

SPEC = {
    "title": "Bookmark search", "problem": "cannot find bookmarks", "why": "unusable list",
    "acceptance_criteria": ["title search works", "results paginate"],
    "tasks": [{"id": "T-1", "title": "search", "purpose": "query", "scope": ["src/"],
               "completion_evidence": "test passes"}],
    "verdict": "pass", "questions": [],
}
ARCH = {"verdict": "pass", "architecture_notes": "extend the repository",
        "modules_affected": ["src/search.py"]}
CODER = json.dumps({"verdict": "complete", "code_blocks": [
    {"path": "src/search.py", "content": "def s():\n    return []\n", "action": "create"}]})
FIX = json.dumps({"verdict": "complete", "code_blocks": [
    {"path": "src/search.py", "content": "def s():\n    return ['ok']\n", "action": "create"}]})
TESTER = {"overall": "pass", "qa_verdict": "pass",
          "ac_coverage": ["title search works", "results paginate"],
          "security_verdict": "pass", "highest_severity": "none",
          "performance_verdict": "pass", "summary": "ok"}
RELEASE = {"verdict": "pass", "summary": "Bookmarks can be searched by title.",
           "changes": ["src/search.py: title search"],
           "how_to_verify": ["call s() and expect a list"],
           "migration_notes": "none", "rollback_notes": "revert the commit"}


def _blocked(*_a, **_k):
    raise RuntimeError("LIVE CALL BLOCKED")


def _git_log(root: Path) -> list[str]:
    return subprocess.run(["git", "log", "--format=%s"], cwd=root, capture_output=True,
                          text=True, check=False).stdout.splitlines()


class ReleaseCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="bookmarks",
                                      stack="fastapi")
        guard = patch("factory.pipeline.agent_calls.run_agent", _blocked)
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *, release: dict | None = RELEASE, spec: dict = SPEC,
             arch: dict = ARCH) -> runs.RunOutcome:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Pending", "search my bookmarks",
                            project_id=self.project["id"])
            orig = db.start_run(conn, "US-0001", project_id=self.project["id"])
            db.log_agent(conn, orig, "spec-agent", "in", json.dumps(spec), verdict="pass")
            db.log_agent(conn, orig, "architect-agent", "in", json.dumps(arch), verdict="pass")
            db.log_agent(conn, orig, "coder-agent", "in", CODER, verdict="complete",
                         stage_type="T-1")
            db.log_agent(conn, orig, "coder-agent", "in", FIX, verdict="complete",
                         stage_type="remediation")
            db.log_agent(conn, orig, "tester-agent", "in", json.dumps(TESTER), verdict="pass")
            if release is not None:
                db.log_agent(conn, orig, "release-agent", "in", json.dumps(release),
                             verdict="pass")
        outcome = runs.run_pipeline("search my bookmarks", opencode_cwd=self.project["repo_path"],
                                    db_path=self.db_path, project_id=self.project["id"],
                                    replay_run_id=orig)
        self.work = replay_sandbox(outcome.run_id, self.db_path)
        return outcome

    def _gates(self, run_id: int) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return db.get_run_gates(conn, run_id)

    def _story_status(self) -> str:
        with db.get_db(self.db_path) as conn:
            return db.get_run(conn, self.new_run)["status"]

    # ── the park ───────────────────────────────────────────────────────

    def test_a_reviewed_run_parks_at_checkpoint_3_instead_of_completing(self) -> None:
        outcome = self._run()
        self.assertEqual(outcome.status, "waiting_human", outcome.error)
        release = [g for g in self._gates(outcome.run_id) if g["gate_name"] == "gate-release"]
        self.assertEqual(len(release), 1)
        self.assertTrue(release[0]["needs_human"])
        self.assertIn("Checkpoint 3", release[0]["human_questions"])
        with db.get_db(self.db_path) as conn:
            run = db.get_run(conn, outcome.run_id)
        self.assertEqual(run["current_stage"], "gate-release-human")
        self.assertIn("Checkpoint 3", "\n".join(outcome.human_questions or []))

    def test_release_notes_are_written_and_committed_with_the_evidence(self) -> None:
        outcome = self._run()
        notes = artifacts.work_dir_for(self.work, "US-0001") / "RELEASE.md"
        self.assertTrue(notes.is_file())
        text = notes.read_text(encoding="utf-8")
        self.assertIn("Bookmarks can be searched by title.", text)
        self.assertIn("revert the commit", text)
        self.assertTrue(any("RELEASE" in s for s in _git_log(self.work)))
        # The trust package is in front of the operator AT the checkpoint.
        self.assertTrue(
            (self.work / "docs" / "releases" / f"run-{outcome.run_id}-trust-package.json").is_file()
        )

    def test_intent_names_the_project(self) -> None:
        self._run()
        intent = (artifacts.work_dir_for(self.work, "US-0001") / "INTENT.md").read_text()
        self.assertIn(f"- Project: {self.project['id']}", intent)

    def test_a_run_recorded_before_the_release_agent_still_replays(self) -> None:
        outcome = self._run(release=None)
        self.assertEqual(outcome.status, "waiting_human", outcome.error)
        [release] = [g for g in self._gates(outcome.run_id) if g["gate_name"] == "gate-release"]
        self.assertFalse(release["passed"])  # no notes: a named gap, not a crash
        self.assertIn("Release notes were not written", release["human_questions"])
        with db.get_db(self.db_path) as conn:
            log = db.get_agent_log(conn, outcome.run_id, "release-agent")
        self.assertEqual(log["verdict"], "skipped")

    # ── the decision ───────────────────────────────────────────────────

    def test_approve_releases_the_story(self) -> None:
        parked = self._run()
        outcome = runs.resume_run(parked.run_id, "approve", "ship it", db_path=self.db_path)
        self.assertEqual(outcome.status, "completed", outcome.error)
        with db.get_db(self.db_path) as conn:
            auths = db.get_run_authorizations(conn, parked.run_id)
            run = db.get_run(conn, parked.run_id)
        release = [a for a in auths if a["stage"] == "release"]
        self.assertEqual(len(release), 1)
        self.assertTrue(release[0]["allowed"])
        self.assertIn("operator", release[0]["granted_by"])
        self.assertEqual(run["status"], "completed")
        record = (artifacts.work_dir_for(self.work, "US-0001") / "PIPELINE.md").read_text()
        self.assertIn("the story is complete", record)

    # ── the approval is stamped into what it approved ────────────────────

    def _status_line(self, path: Path) -> str:
        return next(ln for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("- Status:"))

    def test_release_approval_is_stamped_into_the_release_notes(self) -> None:
        parked = self._run()
        notes = artifacts.work_dir_for(self.work, "US-0001") / "RELEASE.md"
        self.assertIn("pending Checkpoint 3", self._status_line(notes))
        runs.resume_run(parked.run_id, "approve", "ship it", db_path=self.db_path)
        line = self._status_line(notes)
        self.assertIn(f"approved by the operator at Checkpoint 3 (run #{parked.run_id}, ", line)
        self.assertIn("UTC", line)
        self.assertTrue(any(s.startswith("factory: US-0001 approved at Checkpoint 3")
                            for s in _git_log(self.work)), _git_log(self.work))
        dirty = subprocess.run(["git", "status", "--porcelain", "docs/"], cwd=self.work,
                               capture_output=True, text=True).stdout
        self.assertEqual(dirty.strip(), "", "the stamp must be committed")

    def test_design_approval_is_stamped_into_the_adr_and_plan(self) -> None:
        breaking = {**ARCH, "breaking_changes": ["search() now returns a list"]}
        parked = self._run(arch=breaking)
        self.assertEqual(parked.status, "waiting_human", parked.error)
        self.assertTrue(any(g["gate_name"] == "gate-2-architect" and g["needs_human"]
                            for g in self._gates(parked.run_id)))
        runs.resume_run(parked.run_id, "approve", "fine", db_path=self.db_path)
        plan = artifacts.work_dir_for(self.work, "US-0001") / "PLAN.md"
        [adr_file] = (self.work / "docs" / "architecture" / "adr").glob("ADR-US-0001-*.md")
        for path in (plan, adr_file):
            self.assertIn("approved by the operator at Checkpoint 2", self._status_line(path))
        self.assertTrue(any("approved at Checkpoint 2" in s for s in _git_log(self.work)))

    def test_story_approval_is_stamped_into_the_spec(self) -> None:
        parked = self._run(spec={**SPEC, "questions": ["Search by tag too?"]})
        self.assertEqual(parked.status, "waiting_human", parked.error)
        runs.resume_run(parked.run_id, "approve", "title only", db_path=self.db_path)
        spec = artifacts.work_dir_for(self.work, "US-0001") / "SPEC.md"
        self.assertIn("approved by the operator at Checkpoint 1", self._status_line(spec))

    def test_a_rejection_stamps_nothing(self) -> None:
        parked = self._run()
        notes = artifacts.work_dir_for(self.work, "US-0001") / "RELEASE.md"
        runs.resume_run(parked.run_id, "reject", "add pagination", db_path=self.db_path)
        self.assertNotIn("approved", self._status_line(notes))

    def test_approval_refuses_code_that_changed_after_the_review(self) -> None:
        parked = self._run()
        (self.work / "src" / "search.py").write_text("def s():\n    return ['unreviewed']\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam",
                        "manual edit"], cwd=self.work, check=True)
        outcome = runs.resume_run(parked.run_id, "approve", "ship it", db_path=self.db_path)
        self.assertEqual(outcome.status, "blocked")
        self.assertIn("changed", outcome.error or "")
        with db.get_db(self.db_path) as conn:
            self.assertNotEqual(db.get_run(conn, parked.run_id)["status"], "completed")

    def test_the_package_names_the_candidate_it_judged(self) -> None:
        parked = self._run()
        pkg = json.loads((self.work / "docs" / "releases"
                          / f"run-{parked.run_id}-trust-package.json").read_text())
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.work, capture_output=True,
                              text=True).stdout.strip()
        self.assertTrue(pkg["candidate"]["commit"])
        self.assertEqual(len(pkg["candidate"]["commit"]), len(head))

    def test_reject_sends_the_operators_words_to_the_coder_then_parks_again(self) -> None:
        parked = self._run()
        outcome = runs.resume_run(parked.run_id, "reject", "results must not be empty",
                                  db_path=self.db_path)
        self.assertEqual(outcome.status, "waiting_human", outcome.error)
        with db.get_db(self.db_path) as conn:
            remediation = db.get_agent_log_by_stage(conn, parked.run_id, "coder-agent",
                                                    "remediation")
            auths = db.get_run_authorizations(conn, parked.run_id)
        self.assertIsNotNone(remediation)
        self.assertIn("results must not be empty", remediation["input_text"])
        granted = [a for a in auths if a["stage"] == "coder-agent:remediation"]
        self.assertIn("Checkpoint 3", granted[0]["granted_by"])
        releases = [g for g in self._gates(parked.run_id) if g["gate_name"] == "gate-release"]
        self.assertEqual(len(releases), 2)  # parked again for a fresh decision
        self.assertIn("['ok']", (self.work / "src" / "search.py").read_text())


if __name__ == "__main__":
    unittest.main()
