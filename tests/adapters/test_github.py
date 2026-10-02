"""The GitHub adapter: open and merge a story's pull request through the `gh` CLI.

Only command construction and result handling are tested — never the network.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters import github

REPO = Path("/tmp/shop")


def _done(rc: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out, err)


class OpenPullRequestTests(unittest.TestCase):
    def test_a_pull_request_is_created_against_the_target(self) -> None:
        with patch("factory.adapters.github.subprocess.run",
                   side_effect=[_done(1), _done(0, "https://github.com/me/shop/pull/7\n")]) as run:
            url, error = github.open_pull_request(REPO, "factory/US-0001", "main",
                                                  "US-0001: search", "notes")
        self.assertEqual(url, "https://github.com/me/shop/pull/7")
        self.assertIsNone(error)
        create = run.call_args_list[-1].args[0]
        self.assertEqual(create[:3], ["gh", "pr", "create"])
        for flag, value in (("--head", "factory/US-0001"), ("--base", "main"),
                            ("--title", "US-0001: search"), ("--body", "notes")):
            self.assertEqual(create[create.index(flag) + 1], value)
        for call in run.call_args_list:
            self.assertIs(call.kwargs.get("stdin"), subprocess.DEVNULL)
            self.assertEqual(call.kwargs.get("cwd"), str(REPO))

    def test_an_existing_pull_request_is_reused(self) -> None:
        # A rejected release goes back to the coder; the next Checkpoint 3 reuses its PR.
        with patch("factory.adapters.github.subprocess.run",
                   return_value=_done(0, "https://github.com/me/shop/pull/7\n")) as run:
            url, error = github.open_pull_request(REPO, "factory/US-0001", "main", "t", "b")
        self.assertEqual(url, "https://github.com/me/shop/pull/7")
        self.assertEqual(run.call_args.args[0][:3], ["gh", "pr", "view"])

    def test_a_failure_is_reported_not_raised(self) -> None:
        with patch("factory.adapters.github.subprocess.run",
                   side_effect=[_done(1), _done(1, err="GraphQL: no permission")]):
            url, error = github.open_pull_request(REPO, "factory/US-0001", "main", "t", "b")
        self.assertIsNone(url)
        self.assertIn("no permission", error)


class MergePullRequestTests(unittest.TestCase):
    def test_merging_uses_a_merge_commit_and_reports_failure(self) -> None:
        with patch("factory.adapters.github.subprocess.run", return_value=_done(0)) as run:
            ok, _ = github.merge_pull_request(REPO, "https://github.com/me/shop/pull/7")
        self.assertTrue(ok)
        merge = run.call_args.args[0]
        self.assertEqual(merge[:3], ["gh", "pr", "merge"])
        self.assertIn("--merge", merge)
        with patch("factory.adapters.github.subprocess.run",
                   return_value=_done(1, err="Pull request is not mergeable")):
            ok, detail = github.merge_pull_request(REPO, "https://github.com/me/shop/pull/7")
        self.assertFalse(ok)
        self.assertIn("not mergeable", detail)


if __name__ == "__main__":
    unittest.main()
