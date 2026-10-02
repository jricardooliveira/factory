"""Every story is built on its own branch and released by merging it (operator decision).

"Release = merged PR" (2026-10-02): a live run works on `factory/<story>`, cut from
the product's main line; Checkpoint 3 approval merges it. A merge that cannot land
cleanly is aborted and leaves the main line exactly as it was.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.workspace import git


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=root, capture_output=True, text=True, check=True).stdout.strip()


class StoryBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "p"
        self.repo.mkdir()
        git.git_init(self.repo)
        (self.repo / "README.md").write_text("# p\n")
        git.git_commit_all(self.repo, "factory: scaffold")
        self.main = git.current_branch(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _commit(self, rel: str, text: str, message: str) -> None:
        (self.repo / rel).write_text(text)
        git.git_commit_all(self.repo, message)

    def test_a_story_branch_is_cut_from_the_main_line_and_checked_out(self) -> None:
        branch = git.start_story_branch(self.repo, "US-0001")
        self.assertEqual(branch.name, "factory/US-0001")
        self.assertEqual(branch.target, self.main)
        self.assertEqual(git.current_branch(self.repo), "factory/US-0001")

    def test_the_next_story_branches_from_the_main_line_not_a_parked_story(self) -> None:
        git.start_story_branch(self.repo, "US-0001")
        self._commit("one.py", "ONE = 1\n", "factory: US-0001 T-1")  # US-0001 is parked
        branch = git.start_story_branch(self.repo, "US-0002")
        self.assertEqual(branch.target, self.main)
        self.assertFalse((self.repo / "one.py").exists())  # not built on US-0001's work

    def test_resuming_returns_to_the_story_branch(self) -> None:
        git.start_story_branch(self.repo, "US-0001")
        self._commit("one.py", "ONE = 1\n", "factory: US-0001 T-1")
        git.start_story_branch(self.repo, "US-0002")
        git.checkout_branch(self.repo, "factory/US-0001")
        self.assertTrue((self.repo / "one.py").exists())

    def test_a_dirty_tree_refuses_to_switch_branches(self) -> None:
        (self.repo / "README.md").write_text("# edited by hand\n")
        with self.assertRaises(git.GitError):
            git.start_story_branch(self.repo, "US-0001")

    def test_approval_merges_the_story_into_the_main_line(self) -> None:
        branch = git.start_story_branch(self.repo, "US-0001")
        self._commit("one.py", "ONE = 1\n", "factory: US-0001 T-1")
        ok, detail = git.merge_story_branch(self.repo, branch.name, branch.target,
                                            "factory: merge US-0001")
        self.assertTrue(ok, detail)
        self.assertEqual(git.current_branch(self.repo), self.main)
        self.assertTrue((self.repo / "one.py").exists())
        self.assertIn("factory: merge US-0001", _git(self.repo, "log", "-1", "--format=%s"))
        self.assertNotIn("factory/US-0001", _git(self.repo, "branch"))  # merged, removed

    def test_a_conflicting_merge_is_aborted_and_leaves_the_main_line_untouched(self) -> None:
        branch = git.start_story_branch(self.repo, "US-0001")
        self._commit("README.md", "# story version\n", "factory: US-0001 T-1")
        git.checkout_branch(self.repo, self.main)
        self._commit("README.md", "# main moved on\n", "someone else")
        main_head = git.git_head(self.repo)
        git.checkout_branch(self.repo, branch.name)
        ok, detail = git.merge_story_branch(self.repo, branch.name, branch.target,
                                            "factory: merge US-0001")
        self.assertFalse(ok)
        self.assertIn("conflict", detail.lower())
        git.checkout_branch(self.repo, self.main)
        self.assertEqual(git.git_head(self.repo), main_head)
        self.assertEqual((self.repo / "README.md").read_text(), "# main moved on\n")

    def test_only_a_github_remote_is_a_pull_request_remote(self) -> None:
        self.assertIsNone(git.github_remote(self.repo))
        _git(self.repo, "remote", "add", "origin", "/some/local/path.git")
        self.assertIsNone(git.github_remote(self.repo))
        _git(self.repo, "remote", "set-url", "origin", "https://github.com/me/shop.git")
        self.assertEqual(git.github_remote(self.repo), "https://github.com/me/shop.git")


if __name__ == "__main__":
    unittest.main()
