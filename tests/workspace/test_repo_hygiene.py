"""Factory plumbing must never be committed into the product's repository.

Found by a live validation run. `projects.link_opencode_agents` puts a
`.opencode` symlink inside each project repo so opencode can find the agent
definitions. `git_commit_all` runs `git add -A`, so the very first task commit
captured that symlink — an absolute path to the operator's machine — into the
GENERATED PRODUCT's history. It then appeared in the cumulative diff the tester
and the remediation coder review; the coder echoed it back as a code block,
materialization (correctly) refused a path resolving outside the repo, and the
whole run failed.

Excluded via `.git/info/exclude`, which keeps it out of `git add -A` without
touching the product's own `.gitignore` (the factory has no business editing the
product's files to hide its own).
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.workspace import git


def _tracked(repo: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True)
    return out.stdout.split()


class InfraExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.agents = Path(self._tmp.name) / "agents"
        self.agents.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_opencode_symlink_is_never_committed(self) -> None:
        git.git_init(self.repo)
        (self.repo / ".opencode").symlink_to(self.agents, target_is_directory=True)
        (self.repo / "main.go").write_text("package main\n")
        self.assertTrue(git.git_commit_all(self.repo, "factory: T-0001 init"))
        self.assertIn("main.go", _tracked(self.repo))
        self.assertNotIn(".opencode", _tracked(self.repo),
                         "factory plumbing leaked into the product's git history")

    def test_exclusion_also_covers_a_repo_created_before_this_fix(self) -> None:
        """git_init is a no-op on an existing repo, so the exclusion must be
        (re)asserted where the commit happens, not only at creation."""
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / ".opencode").symlink_to(self.agents, target_is_directory=True)
        (self.repo / "a.go").write_text("package a\n")
        git.git_commit_all(self.repo, "factory: T-0001")
        self.assertNotIn(".opencode", _tracked(self.repo))

    def test_the_products_own_gitignore_is_left_alone(self) -> None:
        git.git_init(self.repo)
        (self.repo / "a.go").write_text("package a\n")
        git.git_commit_all(self.repo, "factory: T-0001")
        self.assertFalse((self.repo / ".gitignore").exists(),
                         "the factory must not edit the product's files to hide its own")

    def test_exclusion_is_idempotent(self) -> None:
        git.git_init(self.repo)
        for i in range(3):
            (self.repo / f"f{i}.go").write_text("package f\n")
            git.git_commit_all(self.repo, f"factory: T-000{i}")
        exclude = (self.repo / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count("/.opencode"), 1)

    def test_bytecode_test_caches_and_the_product_venv_are_never_committed(self) -> None:
        """Verification compiles the coder's Python, leaving __pycache__; a test run
        leaves .pytest_cache; the operator may create a product .venv for test runs."""
        git.git_init(self.repo)
        for rel in ("app.py", "__pycache__/x.cpython-312.pyc", "pkg/__pycache__/y.pyc",
                    "stray.pyc", ".pytest_cache/v/cache/nodeids", ".venv/bin/python"):
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text("x\n")
        self.assertTrue(git.git_commit_all(self.repo, "factory: T-0001"))
        self.assertEqual(_tracked(self.repo), ["app.py"])


def _status(repo: Path) -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                         cwd=repo, capture_output=True, text=True)
    return sorted(line[3:] for line in out.stdout.splitlines())


class DiscardPathsTests(unittest.TestCase):
    """`git_discard_paths` undoes the factory's own uncommitted writes — and only those."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        git.git_init(self.repo)
        (self.repo / "kept.py").write_text("orig\n")
        git.git_commit_all(self.repo, "factory: seed")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: str, text: str = "x\n") -> None:
        (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / rel).write_text(text)

    def test_restores_tracked_deletes_untracked_and_its_empty_dirs(self) -> None:
        self._write("kept.py", "changed\n")
        self._write("pkg/sub/new.py")
        self._write("pkg/sub/__pycache__/new.cpython-312.pyc")  # verification's bytecode
        (self.repo / "pkg" / "other").mkdir()
        self._write("notes.txt", "operator\n")
        git.git_discard_paths(self.repo, ["kept.py", str(self.repo / "pkg/sub/new.py")])
        self.assertEqual((self.repo / "kept.py").read_text(), "orig\n")
        self.assertFalse((self.repo / "pkg" / "sub").exists())
        self.assertTrue((self.repo / "pkg" / "other").is_dir(), "not its dir to remove")
        self.assertEqual(_status(self.repo), ["notes.txt"])

    def test_never_touches_evidence_git_or_paths_outside_the_repo(self) -> None:
        outside = Path(self._tmp.name) / "outside.py"
        outside.write_text("x\n")
        self._write("docs/work/US-0001/SPEC.md")
        self._write("PROJECT_RULES.md")
        git.git_discard_paths(self.repo, [
            "docs/work/US-0001/SPEC.md", "PROJECT_RULES.md", "../outside.py", str(outside),
            ".git/HEAD", ".", ""])
        self.assertTrue(outside.is_file())
        self.assertTrue((self.repo / "docs/work/US-0001/SPEC.md").is_file())
        self.assertTrue((self.repo / "PROJECT_RULES.md").is_file())
        self.assertTrue((self.repo / ".git" / "HEAD").is_file())

    def test_off_git_is_a_noop(self) -> None:
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        (plain / "a.py").write_text("x\n")
        git.git_discard_paths(plain, ["a.py"])
        self.assertTrue((plain / "a.py").is_file())


if __name__ == "__main__":
    unittest.main()
