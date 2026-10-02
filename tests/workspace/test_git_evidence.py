"""Factory-owned evidence inside a product repo is committed, and never measured as code.

Once a product's audit trail (INTENT/SPEC/PLAN, ADRs, trust packages, rules,
spec) lives in the same git repository as its code, every CODE measurement must
look past it: the coder's out-of-band-write check, the trust package's change
set and the tester's diff. Otherwise the factory's own paperwork would block the
coder as an "undeclared write", inflate the change set, and pad the tester's
review with markdown.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.workspace import git, layout

EVIDENCE = layout.EVIDENCE_PATHS


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(root), capture_output=True, text=True, check=False,
    ).stdout


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class IsEvidencePathTests(unittest.TestCase):
    def test_owned_paths(self) -> None:
        for rel in (
            "docs/work/US-0001/SPEC.md",
            "docs/architecture/adr/ADR-US-0001-x.md",
            "docs/releases/run-3-trust-package.json",
            "PROJECT_RULES.md",
            "project-spec.json",
            "./PROJECT_RULES.md",
        ):
            self.assertTrue(layout.is_evidence_path(rel), rel)

    def test_code_paths_are_not_evidence(self) -> None:
        for rel in (
            "docs/README.md",
            "docs/architecture/overview.md",
            "src/PROJECT_RULES.md",
            "app/project-spec.json.py",
            "docs/workshop/x.md",
            "main.py",
        ):
            self.assertFalse(layout.is_evidence_path(rel), rel)


class EvidenceExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "proj"
        self.repo.mkdir()
        git.git_init(self.repo)
        _write(self.repo, "PROJECT_RULES.md", "# rules\n")
        git.git_commit_paths(self.repo, ["PROJECT_RULES.md"], "factory: scaffold")
        self.base = git.git_head(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_commit_paths_commits_only_the_named_paths(self) -> None:
        _write(self.repo, "docs/work/US-0001/SPEC.md")
        _write(self.repo, "rogue.py", "print('out of band')\n")
        self.assertTrue(
            git.git_commit_paths(self.repo, ["docs/work/US-0001/SPEC.md"], "factory: SPEC")
        )
        # The undeclared file must stay visible to the governance check — sweeping
        # it into an evidence commit would launder an out-of-band write.
        self.assertEqual(git.git_changed_paths(self.repo), ["rogue.py"])
        self.assertEqual(_git(self.repo, "log", "-1", "--format=%s").strip(), "factory: SPEC")

    def test_commit_paths_with_nothing_to_commit_is_a_noop(self) -> None:
        self.assertFalse(git.git_commit_paths(self.repo, ["PROJECT_RULES.md"], "factory: again"))
        self.assertFalse(git.git_commit_paths(self.repo, [], "factory: none"))

    def test_commit_paths_off_git_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "PROJECT_RULES.md")
            self.assertFalse(git.git_commit_paths(Path(tmp), ["PROJECT_RULES.md"], "factory: x"))

    def test_changed_paths_excludes_evidence_on_request(self) -> None:
        _write(self.repo, "docs/work/US-0001/INTENT.md")
        _write(self.repo, "docs/releases/run-1-trust-package.json", "{}")
        _write(self.repo, "app.py")
        self.assertEqual(
            sorted(git.git_changed_paths(self.repo) or []),
            ["app.py", "docs/releases/run-1-trust-package.json", "docs/work/US-0001/INTENT.md"],
        )
        self.assertEqual(git.git_changed_paths(self.repo, exclude=EVIDENCE), ["app.py"])

    def test_changed_files_excludes_evidence_on_request(self) -> None:
        _write(self.repo, "docs/architecture/adr/ADR-US-0001-x.md")
        git.git_commit_paths(self.repo, ["docs/architecture/adr"], "factory: ADR")
        _write(self.repo, "app.py")
        git.git_commit_all(self.repo, "factory: T-1")
        _write(self.repo, "docs/work/US-0001/PLAN.md")  # untracked evidence
        files = git.git_changed_files(self.repo, self.base, exclude=EVIDENCE)
        self.assertEqual(files, [{"path": "app.py", "change": "added"}])

    def test_repo_diff_excludes_evidence_on_request(self) -> None:
        _write(self.repo, "docs/work/US-0001/SPEC.md", "SPEC BODY\n")
        git.git_commit_paths(self.repo, ["docs/work"], "factory: SPEC")
        _write(self.repo, "app.py", "CODE = 1\n")
        git.git_commit_all(self.repo, "factory: T-1")
        _write(self.repo, "PROJECT_RULES.md", "# rules, edited\n")  # working-tree change
        full = git.collect_repo_diff(self.repo) or ""
        self.assertIn("SPEC BODY", full)
        code_only = git.collect_repo_diff(self.repo, exclude=EVIDENCE) or ""
        self.assertIn("CODE = 1", code_only)
        self.assertNotIn("SPEC BODY", code_only)
        self.assertNotIn("PROJECT_RULES.md", code_only)

    def test_repo_diff_from_a_runs_base_shows_only_that_run(self) -> None:
        """Review task T05: the tester's diff started at the FIRST factory commit, so
        story 10's review carried stories 1-9 and they ate its 16k-char budget."""
        _write(self.repo, "story_one.py", "ONE = 1\n")
        git.git_commit_all(self.repo, "factory: US-0001 T-1")
        story_two_base = git.git_head(self.repo)
        _write(self.repo, "story_two.py", "TWO = 2\n")
        git.git_commit_all(self.repo, "factory: US-0002 T-1")
        everything = git.collect_repo_diff(self.repo) or ""
        self.assertIn("ONE = 1", everything)
        this_run = git.collect_repo_diff(self.repo, base=story_two_base) or ""
        self.assertIn("TWO = 2", this_run)
        self.assertNotIn("ONE = 1", this_run)

    def test_an_unknown_base_falls_back_to_the_factory_baseline(self) -> None:
        _write(self.repo, "app.py", "CODE = 1\n")
        git.git_commit_all(self.repo, "factory: T-1")
        diff = git.collect_repo_diff(self.repo, base="0" * 40) or ""
        self.assertIn("CODE = 1", diff)

    def test_code_changed_since_ignores_the_factorys_own_evidence(self) -> None:
        """Review task T07: an approval must release exactly the code reviewed. The
        factory keeps committing evidence after the checkpoint; that is not a change."""
        _write(self.repo, "app.py", "CODE = 1\n")
        git.git_commit_all(self.repo, "factory: T-1")
        candidate = git.git_head(self.repo)
        _write(self.repo, "docs/work/US-0001/PIPELINE.md", "record\n")
        git.git_commit_paths(self.repo, ["docs/work"], "factory: PIPELINE")
        self.assertFalse(git.code_changed_since(self.repo, candidate, exclude=EVIDENCE))
        _write(self.repo, "app.py", "CODE = 2\n")  # an edit after the review
        self.assertTrue(git.code_changed_since(self.repo, candidate, exclude=EVIDENCE))

    def test_an_untracked_code_file_is_a_change(self) -> None:
        candidate = git.git_head(self.repo)
        _write(self.repo, "sneaky.py", "x = 1\n")
        self.assertTrue(git.code_changed_since(self.repo, candidate, exclude=EVIDENCE))

    def test_the_change_set_can_end_at_a_pinned_candidate(self) -> None:
        _write(self.repo, "app.py", "CODE = 1\n")
        git.git_commit_all(self.repo, "factory: US-0001 T-1")
        candidate = git.git_head(self.repo)
        _write(self.repo, "later.py", "LATER = 1\n")  # a later story's work
        git.git_commit_all(self.repo, "factory: US-0002 T-1")
        files = git.git_changed_files(self.repo, self.base, exclude=EVIDENCE, end=candidate)
        self.assertEqual([f["path"] for f in files], ["app.py"])

    def test_only_evidence_changes_is_a_clean_code_diff(self) -> None:
        _write(self.repo, "docs/work/US-0001/SPEC.md")
        git.git_commit_paths(self.repo, ["docs/work"], "factory: SPEC")
        self.assertEqual(git.collect_repo_diff(self.repo, exclude=EVIDENCE), "")

    def test_evidence_commits_do_not_move_the_factory_baseline(self) -> None:
        # The scaffold commit is the oldest factory: commit and the repo root, so
        # the baseline is the empty tree — exactly what the first task commit
        # produced before evidence moved into the repo.
        self.assertEqual(git._factory_baseline(self.repo), git._EMPTY_TREE)
        _write(self.repo, "docs/work/US-0001/SPEC.md")
        git.git_commit_paths(self.repo, ["docs/work"], "factory: SPEC")
        self.assertEqual(git._factory_baseline(self.repo), git._EMPTY_TREE)

    def test_pre_factory_history_is_still_the_baseline_of_an_adopted_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git.git_init(repo)
            _write(repo, "human.py", "H = 1\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "human work", "--no-gpg-sign")
            human = git.git_head(repo)
            _write(repo, "PROJECT_RULES.md")
            git.git_commit_paths(repo, ["PROJECT_RULES.md"], "factory: import legacy evidence")
            self.assertEqual(git._factory_baseline(repo), human)


if __name__ == "__main__":
    unittest.main()
