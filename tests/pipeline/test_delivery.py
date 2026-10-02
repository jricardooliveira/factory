"""Release = merged PR (operator decision, 2026-10-02).

At Checkpoint 3 the story branch is offered as a pull request — a real one on
GitHub when the product repo's `origin` is there, else the branch itself, merged
locally. Approval MERGES it; a story is released only once the merge landed. A
replay never pushes or opens anything: it works in a scratch clone.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.pipeline import delivery
from factory.state import db
from factory.workspace import git

GITHUB = "https://github.com/me/shop.git"
PR = "https://github.com/me/shop/pull/7"


class DeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "f.db"
        db.init_db(self.db_path)
        self.repo = root / "shop"
        self.repo.mkdir()
        git.git_init(self.repo)
        (self.repo / "README.md").write_text("# shop\n")
        git.git_commit_all(self.repo, "factory: scaffold")
        self.main = git.current_branch(self.repo)
        self.branch = git.start_story_branch(self.repo, "US-0001")
        (self.repo / "cart.py").write_text("CART = []\n")
        git.git_commit_all(self.repo, "factory: US-0001 T-1")
        notes = self.repo / "docs" / "work" / "US-0001"
        notes.mkdir(parents=True)
        (notes / "RELEASE.md").write_text("# Release notes — US-0001\n\nAdds a cart.\n")
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Cart", "add a cart")
            self.run_id = db.start_run(conn, "US-0001")
            db.set_story_branch(conn, self.run_id, self.branch.name, self.branch.target)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, **extra) -> dict:
        state = {"run_id": self.run_id, "db_path": str(self.db_path), "story_id": "US-0001",
                 "opencode_cwd": str(self.repo), "project_dir": str(self.repo),
                 "spec": {"title": "Cart"}}
        state.update(extra)
        return state

    def _pr_url(self) -> str | None:
        with db.get_db(self.db_path) as conn:
            return db.get_run(conn, self.run_id)["pr_url"]

    # ── Checkpoint 3: the pull request ─────────────────────────────────

    def test_without_a_github_remote_the_branch_itself_is_offered(self) -> None:
        note, gap = delivery.open_release_pr(self._state())
        self.assertIsNone(gap)
        self.assertIn("factory/US-0001", note)
        self.assertIn(self.main, note)
        self.assertIsNone(self._pr_url())

    def test_with_a_github_remote_a_pull_request_is_opened(self) -> None:
        with patch.object(delivery, "github_remote", return_value=GITHUB), \
                patch.object(delivery, "push_branch", return_value=(True, "")) as push, \
                patch.object(delivery.github, "open_pull_request",
                             return_value=(PR, None)) as open_pr:
            note, gap = delivery.open_release_pr(self._state())
        self.assertIsNone(gap)
        self.assertIn(PR, note)
        push.assert_called_once_with(self.repo, "factory/US-0001")
        _repo, branch, target, title, body = open_pr.call_args.args
        self.assertEqual((branch, target), ("factory/US-0001", self.main))
        self.assertIn("US-0001", title)
        self.assertIn("Adds a cart.", body)  # the release notes are the PR description
        self.assertEqual(self._pr_url(), PR)

    def test_a_pull_request_that_cannot_be_opened_is_a_named_gap(self) -> None:
        with patch.object(delivery, "github_remote", return_value=GITHUB), \
                patch.object(delivery, "push_branch", return_value=(False, "denied")):
            _note, gap = delivery.open_release_pr(self._state())
        self.assertIn("denied", gap)

    def test_a_replay_never_pushes_or_opens_anything(self) -> None:
        with patch.object(delivery, "github_remote", return_value=GITHUB), \
                patch.object(delivery, "push_branch", side_effect=AssertionError("pushed")):
            _note, gap = delivery.open_release_pr(self._state(replay_run_id=1))
        self.assertIsNone(gap)

    # ── approval: the merge ────────────────────────────────────────────

    def test_approval_merges_the_branch_into_the_main_line(self) -> None:
        ok, detail = delivery.merge_release(self._state())
        self.assertTrue(ok, detail)
        self.assertEqual(git.current_branch(self.repo), self.main)
        self.assertTrue((self.repo / "cart.py").exists())

    def test_approval_merges_the_github_pull_request(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.set_pr_url(conn, self.run_id, PR)
        with patch.object(delivery.github, "merge_pull_request",
                          return_value=(True, "merged")) as merge:
            ok, _ = delivery.merge_release(self._state())
        self.assertTrue(ok)
        merge.assert_called_once_with(self.repo, PR)

    def test_a_failed_merge_is_reported(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.set_pr_url(conn, self.run_id, PR)
        with patch.object(delivery.github, "merge_pull_request",
                          return_value=(False, "Required status check failing")):
            ok, detail = delivery.merge_release(self._state())
        self.assertFalse(ok)
        self.assertIn("status check", detail)

    def test_a_run_without_a_story_branch_has_nothing_to_merge(self) -> None:
        with db.get_db(self.db_path) as conn:
            other = db.start_run(conn, "US-0001")
        ok, _ = delivery.merge_release(self._state(run_id=other))
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
