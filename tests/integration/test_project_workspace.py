"""A project run with the product's evidence INSIDE its own git repository.

The project directory is the repository: code at its root, the factory's
evidence under docs/ (INTENT/SPEC/PLAN, ADRs, trust packages), PROJECT_RULES.md
and project-spec.json beside the code. That move is only safe if the factory:

  1. commits each piece of evidence as it is produced (``factory:`` prefix), so
     the coder's out-of-band-write governance check never sees it as an
     undeclared agent write;
  2. leaves evidence out of every CODE measurement — the coder's scope check,
     the trust package's change set + scope_violations, the tester's diff;
  3. refuses a coder code_block that targets a factory-owned path (gate-1 reads
     PROJECT_RULES.md as operator-authored: a coder able to rewrite it could
     settle its own ambiguity questions);
  4. still lands an agent's legacy ``repo/``-prefixed path at the repo root.

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
from factory.evidence import trust_package as tp
from factory.state import db
from factory.workspace import git, layout
from factory.workspace.projects import create_project


def _blocked(*_a, **_k):
    raise RuntimeError("LIVE CALL BLOCKED")


def _git_out(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
    ).stdout


SPEC = {
    "title": "Bookmark search", "problem": "cannot find bookmarks",
    "why": "the list is unusable", "non_goals": ["full-text search"],
    "acceptance_criteria": ["title search works", "results paginate"],
    "tasks": [{"id": "T-1", "title": "search", "purpose": "query",
               "scope": ["src/"], "completion_evidence": "test passes"}],
    "verdict": "pass", "questions": [],
}
ARCH = {
    "verdict": "pass", "architecture_notes": "extend the repository",
    "modules_affected": ["src/search.py"], "risks": ["unindexed LIKE"],
}
TESTER = {
    "overall": "pass", "qa_verdict": "pass",
    "ac_coverage": ["title search works", "results paginate"],
    "security_verdict": "pass", "highest_severity": "none",
    "performance_verdict": "pass", "summary": "ok",
}


def _coder(*blocks: tuple[str, str]) -> str:
    return json.dumps({"verdict": "complete", "code_blocks": [
        {"path": path, "content": content, "action": "create"} for path, content in blocks
    ]})


class ProjectWorkspaceRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="bookmarks",
                                      stack="fastapi")
        self.repo = Path(self.project["repo_path"])
        guard = patch("factory.pipeline.agent_calls.run_agent", _blocked)
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, coder_out: str, arch: dict | None = None) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Pending", "let me search my bookmarks",
                            project_id=self.project["id"])
            orig = db.start_run(conn, "US-0001", project_id=self.project["id"])
            db.log_agent(conn, orig, "spec-agent", "in", json.dumps(SPEC), verdict="pass")
            db.log_agent(conn, orig, "architect-agent", "in", json.dumps(arch or ARCH),
                         verdict="pass")
            db.log_agent(conn, orig, "coder-agent", "in", coder_out,
                         verdict="complete", stage_type="T-1")
            db.log_agent(conn, orig, "tester-agent", "in", json.dumps(TESTER), verdict="pass")
        return orig

    def _replay(self, coder_out: str, arch: dict | None = None) -> runs.RunOutcome:
        orig = self._seed(coder_out, arch)
        return runs.replay_run(orig, db_path=self.db_path)

    # ── 1. the happy path: evidence committed, code measured alone ───────

    def test_run_completes_with_all_evidence_committed_in_the_product_repo(self) -> None:
        outcome = self._replay(_coder(("src/search.py", "def s():\n    return []\n")))
        self.assertEqual(outcome.status, "completed", outcome.error)

        tracked = set(_git_out(self.repo, "ls-files").split())
        work = artifacts.work_dir_for(self.repo, "US-0001")
        for name in ("INTENT.md", "SPEC.md", "PLAN.md"):
            self.assertIn(f"docs/work/US-0001/{name}", tracked)
            self.assertTrue((work / name).is_file())
        self.assertTrue(any(t.startswith("docs/architecture/adr/ADR-US-0001-") for t in tracked))
        self.assertIn(f"docs/releases/run-{outcome.run_id}-trust-package.json", tracked)
        self.assertIn("src/search.py", tracked)
        # Nothing the factory wrote was left uncommitted.
        self.assertEqual(git.git_changed_paths(self.repo), [])
        subjects = _git_out(self.repo, "log", "--format=%s").splitlines()
        self.assertTrue(all(s.startswith("factory:") for s in subjects), subjects)

    def test_trust_package_measures_only_code_changes(self) -> None:
        outcome = self._replay(_coder(("src/search.py", "def s():\n    return []\n")))
        pkg = tp.assemble(self.db_path, outcome.run_id)
        self.assertEqual(pkg["diff"]["source"], "git")
        self.assertEqual(pkg["diff"]["files"], [{"path": "src/search.py", "change": "added"}])
        self.assertEqual(pkg["diff"]["scope_violations"], [])
        self.assertTrue(pkg["adr"]["path"].startswith(str(self.repo / "docs" / "architecture")))
        # The copy written to docs/releases/ says the same thing.
        saved = json.loads(
            (self.repo / "docs" / "releases" / f"run-{outcome.run_id}-trust-package.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(saved["diff"]["files"], [{"path": "src/search.py", "change": "added"}])

    def test_tester_reviews_a_diff_without_the_factorys_paperwork(self) -> None:
        outcome = self._replay(_coder(("src/search.py", "def s():\n    return []\n")))
        with db.get_db(self.db_path) as conn:
            prompt = db.get_agent_log(conn, outcome.run_id, "tester-agent")["input_text"]
        self.assertIn("src/search.py", prompt)
        diff = prompt.split("real git diff", 1)[1].split("## Build gate result", 1)[0]
        for owned in ("docs/work/", "PROJECT_RULES.md", "project-spec.json", "ADR-US-0001"):
            self.assertNotIn(owned, diff)

    def test_run_state_uses_the_repo_as_the_project_dir(self) -> None:
        outcome = self._replay(_coder(("src/search.py", "X = 1\n")))
        self.assertEqual(outcome.final_state["project_dir"], str(self.repo))
        self.assertEqual(outcome.final_state["opencode_cwd"], str(self.repo))

    def test_a_resumed_run_also_uses_the_repo_as_the_project_dir(self) -> None:
        # Checkpoint 2 parks on a breaking change; resuming re-enters at the coder.
        parked = self._replay(_coder(("src/search.py", "X = 1\n")),
                              {**ARCH, "breaking_changes": ["drops the v1 endpoint"]})
        self.assertEqual(parked.status, "waiting_human", parked.error)
        # Resume has no replay mode: the coder hits the blocked live boundary, which
        # is fine — what matters is the state the resumed graph was handed.
        resumed = runs.resume_run(parked.run_id, "approve", db_path=self.db_path)
        self.assertEqual(resumed.final_state["project_dir"], str(self.repo))
        self.assertEqual(resumed.final_state["opencode_cwd"], str(self.repo))

    # ── 2. evidence never trips the coder's governance check ─────────────

    def test_uncommitted_evidence_is_not_an_out_of_band_write(self) -> None:
        # Evidence written but (say) not yet committed — e.g. an operator note in
        # the work folder — must not block the coder as an undeclared write.
        notes = self.repo / "docs" / "work" / "US-0000" / "NOTES.md"
        notes.parent.mkdir(parents=True)
        notes.write_text("operator notes\n", encoding="utf-8")
        outcome = self._replay(_coder(("src/search.py", "X = 1\n")))
        self.assertEqual(outcome.status, "completed", outcome.error)

    def test_a_real_undeclared_write_still_blocks(self) -> None:
        (self.repo / "rogue.py").write_text("print('out of band')\n", encoding="utf-8")
        outcome = self._replay(_coder(("src/search.py", "X = 1\n")))
        self.assertEqual(outcome.status, "blocked")
        self.assertIn("GOVERNANCE", outcome.error or "")
        self.assertIn("rogue.py", outcome.error or "")

    # ── 3. the coder may not author factory-owned evidence ───────────────

    def test_coder_may_not_rewrite_project_rules(self) -> None:
        rules_before = (self.repo / "PROJECT_RULES.md").read_text(encoding="utf-8")
        outcome = self._replay(_coder(
            ("src/search.py", "X = 1\n"),
            ("PROJECT_RULES.md", "- overdue means 1 day\n"),
        ))
        self.assertIn(outcome.status, ("failed", "blocked"))
        self.assertIn("factory-owned", outcome.error or "")
        self.assertEqual((self.repo / "PROJECT_RULES.md").read_text(encoding="utf-8"),
                         rules_before)
        self.assertFalse((self.repo / "src" / "search.py").exists(), "all-or-nothing")

    def test_coder_may_not_write_into_the_work_folder(self) -> None:
        outcome = self._replay(_coder(("docs/work/US-0001/SPEC.md", "forged\n")))
        self.assertIn(outcome.status, ("failed", "blocked"))
        self.assertIn("factory-owned", outcome.error or "")

    # ── 4. legacy repo/-prefixed agent paths still land at the root ──────

    def test_repo_prefixed_paths_land_at_the_repo_root(self) -> None:
        outcome = self._replay(_coder(("repo/src/search.py", "X = 1\n")))
        self.assertEqual(outcome.status, "completed", outcome.error)
        self.assertTrue((self.repo / "src" / "search.py").is_file())
        self.assertFalse((self.repo / "repo").exists())


class EvidenceCommitMessagesTests(unittest.TestCase):
    """Each evidence commit names what it is, so `git log` reads as an audit trail."""

    def test_every_evidence_kind_has_its_own_factory_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            db_path = home / "factory.db"
            db.init_db(db_path)
            project = create_project(db_path, home=home, slug="audit")
            repo = Path(project["repo_path"])
            case = ProjectWorkspaceRunTests()
            case.db_path, case.project, case.repo = db_path, project, repo
            with patch("factory.pipeline.agent_calls.run_agent", _blocked):
                orig = case._seed(_coder(("src/a.py", "A = 1\n")))
                outcome = runs.replay_run(orig, db_path=db_path)
            self.assertEqual(outcome.status, "completed", outcome.error)
            log = _git_out(repo, "log", "--reverse", "--format=%s").splitlines()
            joined = "\n".join(log)
            for needle in ("scaffold", "INTENT", "SPEC", "PLAN", "ADR", "trust package"):
                self.assertIn(needle, joined)
            self.assertTrue(log[0].startswith("factory: scaffold"), log)
            self.assertEqual(layout.EVIDENCE_PATHS[0], "docs/work/")


if __name__ == "__main__":
    unittest.main()
