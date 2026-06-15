"""Tests for deterministic post-materialization verification."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import os

from factory.verify import (
    git_changed_paths,
    git_init,
    is_git_repo,
    run_tests,
    verify_changes,
)


class CollectFailureClassificationTests(unittest.TestCase):
    """A collection failure must distinguish a missing third-party dep (warn)
    from the coder's own code not hanging together (fail)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_third_party_dep_is_warn(self) -> None:
        from factory.verify import _classify_collect_failure

        out = "E   ModuleNotFoundError: No module named 'fastapi'"
        self.assertEqual(_classify_collect_failure(out, self.root).status, "warn")

    def test_missing_local_module_is_fail(self) -> None:
        from factory.verify import _classify_collect_failure

        # 'app' is a package that exists in the repo, so a missing submodule means
        # the coder's own code references something it never wrote → real bug.
        (self.root / "app").mkdir()
        (self.root / "app" / "__init__.py").write_text("")
        out = "E   ModuleNotFoundError: No module named 'app.models'"
        self.assertEqual(_classify_collect_failure(out, self.root).status, "fail")

    def test_cannot_import_name_is_fail(self) -> None:
        from factory.verify import _classify_collect_failure

        out = "E   ImportError: cannot import name 'Widget' from 'app.models'"
        self.assertEqual(_classify_collect_failure(out, self.root).status, "fail")

    def test_name_error_at_import_is_fail(self) -> None:
        from factory.verify import _classify_collect_failure

        out = "E   NameError: name 'undefined_thing' is not defined"
        self.assertEqual(_classify_collect_failure(out, self.root).status, "fail")

    def test_unrecognized_collection_noise_stays_warn(self) -> None:
        from factory.verify import _classify_collect_failure

        out = "some unrelated collection warning we can't classify"
        self.assertEqual(_classify_collect_failure(out, self.root).status, "warn")


class CollectRepoDiffTests(unittest.TestCase):
    """The cumulative real diff (for the tester) is measured from git, baselined
    at the parent of the first `factory:` commit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_none_when_not_a_git_repo(self) -> None:
        from factory.verify import collect_repo_diff

        self.assertIsNone(collect_repo_diff(self.root))

    def test_diff_spans_all_factory_commits_not_just_the_last(self) -> None:
        from factory.verify import collect_repo_diff, git_commit_all, git_init

        git_init(self.root)
        # Pre-factory baseline content (must NOT appear in the diff).
        (self.root / "README.md").write_text("preexisting\n")
        git_commit_all(self.root, "initial (not factory)")
        # Two factory tasks, each committed separately.
        (self.root / "a.py").write_text("def a():\n    return 1\n")
        git_commit_all(self.root, "factory: T-0001 add a")
        (self.root / "b.py").write_text("def b():\n    return 2\n")
        git_commit_all(self.root, "factory: T-0002 add b")

        diff = collect_repo_diff(self.root)
        assert diff is not None
        self.assertIn("diff --git", diff)
        self.assertIn("a.py", diff)   # first task's change is present...
        self.assertIn("b.py", diff)   # ...as well as the last task's
        self.assertNotIn("preexisting", diff)  # pre-factory baseline excluded

    def test_diff_truncated_to_max_chars(self) -> None:
        from factory.verify import collect_repo_diff, git_commit_all, git_init

        git_init(self.root)
        git_commit_all(self.root, "initial")
        (self.root / "big.py").write_text("x = 1  # " + "y" * 5000 + "\n")
        git_commit_all(self.root, "factory: T-0001 big")
        diff = collect_repo_diff(self.root, max_chars=200)
        assert diff is not None
        self.assertLessEqual(len(diff), 200 + 60)
        self.assertIn("truncated", diff)


class RunTestsTests(unittest.TestCase):
    """Opt-in test execution: passing -> pass, failing -> fail, off -> skip."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._orig = os.environ.get("FACTORY_RUN_TESTS")

    def tearDown(self) -> None:
        if self._orig is None:
            os.environ.pop("FACTORY_RUN_TESTS", None)
        else:
            os.environ["FACTORY_RUN_TESTS"] = self._orig
        self._tmp.cleanup()

    def test_disabled_by_default_skips(self) -> None:
        os.environ.pop("FACTORY_RUN_TESTS", None)
        (self.root / "test_x.py").write_text("def test_ok():\n    assert True\n")
        check = run_tests(self.root)
        self.assertEqual(check.status, "skip")

    def test_passing_tests_pass_when_enabled(self) -> None:
        os.environ["FACTORY_RUN_TESTS"] = "1"
        (self.root / "test_x.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
        check = run_tests(self.root)
        self.assertEqual(check.status, "pass")

    def test_failing_tests_fail_when_enabled(self) -> None:
        os.environ["FACTORY_RUN_TESTS"] = "1"
        (self.root / "test_x.py").write_text("def test_bad():\n    assert 1 == 2\n")
        check = run_tests(self.root)
        self.assertEqual(check.status, "fail")

    def test_verify_changes_hard_fails_on_failing_tests_when_enabled(self) -> None:
        os.environ["FACTORY_RUN_TESTS"] = "1"
        f = self.root / "test_x.py"
        f.write_text("def test_bad():\n    assert False\n")
        result = verify_changes([f], root=self.root)
        self.assertFalse(result.passed)  # gate-build blocks on failing tests


class VerifyChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_valid_python_passes(self) -> None:
        f = self.root / "ok.py"
        f.write_text("def f():\n    return 1\n")
        result = verify_changes([f], root=self.root)
        self.assertTrue(result.passed)
        self.assertEqual(result.verdict, "pass")

    def test_broken_python_fails(self) -> None:
        f = self.root / "bad.py"
        f.write_text("def f(:\n    return\n")
        result = verify_changes([f], root=self.root)
        self.assertFalse(result.passed)
        self.assertEqual(result.verdict, "fail")
        self.assertTrue(any(c.name == "py_compile" and c.status == "fail" for c in result.checks))

    def test_non_code_files_skip_and_pass(self) -> None:
        f = self.root / "README.md"
        f.write_text("# hi")
        result = verify_changes([f], root=self.root)
        self.assertTrue(result.passed)
        self.assertEqual(result.verdict, "pass")


class GitBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_non_repo_returns_none(self) -> None:
        self.assertFalse(is_git_repo(self.root))
        self.assertIsNone(git_changed_paths(self.root))

    def test_init_then_lists_individual_files_without_bytecode(self) -> None:
        git_init(self.root)
        self.assertTrue(is_git_repo(self.root))
        (self.root / "app").mkdir()
        f = self.root / "app" / "main.py"
        f.write_text("x = 1\n")
        verify_changes([f], root=self.root)  # creates __pycache__ via py_compile
        changed = git_changed_paths(self.root)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertIn("app/main.py", changed)
        self.assertFalse(any("__pycache__" in p or p.endswith(".pyc") for p in changed))


if __name__ == "__main__":
    unittest.main()
