"""Factory plumbing must never be committed into the product's repository.

Found by a live validation run. `projects._link_opencode_agents` puts a
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

from factory import verify


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
        verify.git_init(self.repo)
        (self.repo / ".opencode").symlink_to(self.agents, target_is_directory=True)
        (self.repo / "main.go").write_text("package main\n")
        self.assertTrue(verify.git_commit_all(self.repo, "factory: T-0001 init"))
        self.assertIn("main.go", _tracked(self.repo))
        self.assertNotIn(".opencode", _tracked(self.repo),
                         "factory plumbing leaked into the product's git history")

    def test_exclusion_also_covers_a_repo_created_before_this_fix(self) -> None:
        """git_init is a no-op on an existing repo, so the exclusion must be
        (re)asserted where the commit happens, not only at creation."""
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / ".opencode").symlink_to(self.agents, target_is_directory=True)
        (self.repo / "a.go").write_text("package a\n")
        verify.git_commit_all(self.repo, "factory: T-0001")
        self.assertNotIn(".opencode", _tracked(self.repo))

    def test_the_products_own_gitignore_is_left_alone(self) -> None:
        verify.git_init(self.repo)
        (self.repo / "a.go").write_text("package a\n")
        verify.git_commit_all(self.repo, "factory: T-0001")
        self.assertFalse((self.repo / ".gitignore").exists(),
                         "the factory must not edit the product's files to hide its own")

    def test_exclusion_is_idempotent(self) -> None:
        verify.git_init(self.repo)
        for i in range(3):
            (self.repo / f"f{i}.go").write_text("package f\n")
            verify.git_commit_all(self.repo, f"factory: T-000{i}")
        exclude = (self.repo / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count("/.opencode"), 1)


if __name__ == "__main__":
    unittest.main()
