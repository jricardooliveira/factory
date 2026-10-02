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


class TestExecutionScopeTests(unittest.TestCase):
    """Tests must run after EVERY task that touches Python, not only after a task
    that happened to write a test file.

    `verify_changes` gated `run_tests` on `any(_is_py_test(p) for p in py_files)`,
    so a task that modified source without adding a test never executed the
    project's existing suite — the exact case where a regression is invisible.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._prev = os.environ.get("FACTORY_RUN_TESTS")
        os.environ["FACTORY_RUN_TESTS"] = "1"

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("FACTORY_RUN_TESTS", None)
        else:
            os.environ["FACTORY_RUN_TESTS"] = self._prev
        self._tmp.cleanup()

    def _write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_a_source_only_change_still_runs_the_existing_suite(self) -> None:
        # A pre-existing test the task did NOT touch, which the change breaks.
        self._write("mod.py", "def value():\n    return 2\n")
        self._write("test_mod.py", "from mod import value\n\n\n"
                                   "def test_value():\n    assert value() == 1\n")
        result = verify_changes([self.root / "mod.py"], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertIn("pytest_run", names,
                      "a source-only task must still execute the project's tests")
        self.assertEqual(names["pytest_run"], "fail")
        self.assertFalse(result.passed)

    def test_a_passing_suite_still_passes_on_a_source_only_change(self) -> None:
        self._write("mod.py", "def value():\n    return 1\n")
        self._write("test_mod.py", "from mod import value\n\n\n"
                                   "def test_value():\n    assert value() == 1\n")
        result = verify_changes([self.root / "mod.py"], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertEqual(names.get("pytest_run"), "pass")
        self.assertTrue(result.passed)

    def test_a_repo_with_no_tests_at_all_does_not_fail_the_gate(self) -> None:
        """Nothing to run is not a failure — a greenfield first task has no suite."""
        self._write("mod.py", "def value():\n    return 1\n")
        result = verify_changes([self.root / "mod.py"], root=self.root)
        self.assertTrue(result.passed, result.summary)

    def test_execution_stays_opt_in(self) -> None:
        os.environ.pop("FACTORY_RUN_TESTS", None)
        self._write("mod.py", "def value():\n    return 2\n")
        self._write("test_mod.py", "from mod import value\n\n\n"
                                   "def test_value():\n    assert value() == 1\n")
        result = verify_changes([self.root / "mod.py"], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertNotEqual(names.get("pytest_run"), "fail",
                            "executing agent-generated code must remain opt-in")


class GoVerificationTests(unittest.TestCase):
    """gate-build must be able to see Go.

    `verify_changes` inferred its toolchain from file extensions and knew only
    .py / .js / .ts — so on a Go backend it produced "no verifiable files", the
    gate PASSED, and the factory could report a story complete having never
    established that the code compiles. For a challenge whose backend is entirely
    Go that makes the single most important gate inert.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "go.mod").write_text("module supportflow\n\ngo 1.26\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_valid_go_compiles_and_passes(self) -> None:
        p = self._write("internal/domain/ticket.go", (
            "package domain\n\n"
            "// Status is a ticket's workflow state.\n"
            "type Status string\n\n"
            "const (\n"
            "\tStatusOpen Status = \"OPEN\"\n"
            ")\n"
        ))
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertIn("go_build", names, f"Go was not verified at all: {result.summary}")
        self.assertEqual(names["go_build"], "pass", result.summary)
        self.assertTrue(result.passed)

    def test_go_that_does_not_compile_FAILS_the_gate(self) -> None:
        """The whole point: a syntax error must block, not skip."""
        p = self._write("internal/domain/broken.go", (
            "package domain\n\n"
            "func Broken( {\n"
            "\treturn\n"
            "}\n"
        ))
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertEqual(names.get("go_build"), "fail", result.summary)
        self.assertFalse(result.passed, "broken Go must fail gate-build")

    def test_a_type_error_fails_too_not_just_a_syntax_error(self) -> None:
        """`gofmt -l` would accept this; only a real build catches it."""
        p = self._write("internal/service/bad.go", (
            "package service\n\n"
            "// Count returns a count.\n"
            "func Count() int {\n"
            "\treturn \"not an int\"\n"
            "}\n"
        ))
        result = verify_changes([p], root=self.root)
        self.assertEqual({c.name: c.status for c in result.checks}.get("go_build"), "fail")
        self.assertFalse(result.passed)

    def test_a_reference_to_a_package_the_coder_never_wrote_fails(self) -> None:
        """The Go equivalent of `_classify_collect_failure`: the coder's own code
        not hanging together is a real bug, not an environment problem."""
        p = self._write("internal/http/handler.go", (
            "package http\n\n"
            "import \"supportflow/internal/nonexistent\"\n\n"
            "// Handle does nothing.\n"
            "func Handle() { _ = nonexistent.Thing }\n"
        ))
        result = verify_changes([p], root=self.root)
        self.assertEqual({c.name: c.status for c in result.checks}.get("go_build"), "fail")

    def test_go_vet_catches_what_the_compiler_allows(self) -> None:
        """The challenge's quality gates name `go vet` explicitly."""
        p = self._write("internal/log/bad.go", (
            "package log\n\n"
            "import \"fmt\"\n\n"
            "// Report prints a report.\n"
            "func Report() { fmt.Printf(\"%d\\n\", \"a string\") }\n"
        ))
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertIn("go_vet", names)
        self.assertEqual(names["go_vet"], "fail", result.summary)
        self.assertFalse(result.passed)

    def test_go_tests_run_when_opted_in_and_a_failure_blocks(self) -> None:
        prev = os.environ.get("FACTORY_RUN_TESTS")
        os.environ["FACTORY_RUN_TESTS"] = "1"
        try:
            self._write("internal/domain/ticket.go",
                        "package domain\n\n// Value returns 2.\nfunc Value() int { return 2 }\n")
            p = self._write("internal/domain/ticket_test.go", (
                "package domain\n\n"
                "import \"testing\"\n\n"
                "func TestValue(t *testing.T) {\n"
                "\tif Value() != 1 {\n\t\tt.Fatalf(\"want 1, got %d\", Value())\n\t}\n"
                "}\n"
            ))
            result = verify_changes([p], root=self.root)
            names = {c.name: c.status for c in result.checks}
            self.assertEqual(names.get("go_test"), "fail", result.summary)
            self.assertFalse(result.passed)
        finally:
            if prev is None:
                os.environ.pop("FACTORY_RUN_TESTS", None)
            else:
                os.environ["FACTORY_RUN_TESTS"] = prev

    def test_no_go_module_warns_rather_than_blocking(self) -> None:
        """A first task that writes a .go file before go.mod exists is not proof
        the code is broken — mirrors the missing-dependency WARN policy."""
        (self.root / "go.mod").unlink()
        p = self._write("main.go", "package main\n\nfunc main() {}\n")
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertEqual(names.get("go_build"), "warn", result.summary)
        self.assertTrue(result.passed)


class TypeScriptProjectTests(unittest.TestCase):
    """`tsc --noEmit` must be run against a real TS project, not the bare cwd.

    Found by driving the SupportFlow challenge through the pipeline: `_tsc_check`
    ran `tsc --noEmit` from the repo root, and with no tsconfig.json there tsc
    prints its HELP TEXT and exits 1. The factory read that as `tsc:fail` and
    blocked gate-build for a reason having nothing to do with the code — so in a
    monorepo whose tsconfig lives under frontend/ (i.e. every Nuxt project) every
    frontend story was unbuildable. A false block is worse than a false pass:
    it burns the remediation budget re-fixing code that was never broken.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    _TSCONFIG = (
        '{"compilerOptions":{"strict":true,"noEmit":true,"target":"es2022",'
        '"module":"esnext","moduleResolution":"bundler","skipLibCheck":true},'
        '"include":["**/*.ts"]}\n'
    )

    def test_loose_ts_file_with_no_tsconfig_anywhere_warns(self) -> None:
        """This is the regression: it used to FAIL on tsc's help text."""
        p = self._write("frontend/composables/useTickets.ts",
                        "export const useTickets = () => ({ tickets: [] })\n")
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertEqual(names.get("tsc"), "warn", result.summary)
        self.assertTrue(result.passed, "a missing tsconfig must not block the gate")

    def test_valid_ts_in_a_real_project_passes(self) -> None:
        self._write("frontend/tsconfig.json", self._TSCONFIG)
        p = self._write("frontend/composables/useTickets.ts", (
            "export interface Ticket { id: string; title: string }\n"
            "export const titles = (t: Ticket[]): string[] => t.map((x) => x.title)\n"
        ))
        result = verify_changes([p], root=self.root)
        self.assertEqual({c.name: c.status for c in result.checks}.get("tsc"), "pass",
                         result.summary)
        self.assertTrue(result.passed)

    def test_a_real_type_error_in_a_real_project_still_fails(self) -> None:
        """The check must keep its teeth — this is the reason it exists."""
        self._write("frontend/tsconfig.json", self._TSCONFIG)
        p = self._write("frontend/composables/bad.ts", (
            "export interface Ticket { id: string; title: string }\n"
            "export const broken = (t: Ticket): number => t.title\n"
        ))
        result = verify_changes([p], root=self.root)
        self.assertEqual({c.name: c.status for c in result.checks}.get("tsc"), "fail",
                         result.summary)
        self.assertFalse(result.passed)

    def test_the_tsconfig_nearest_the_changed_file_is_used(self) -> None:
        """A monorepo root tsconfig must not be preferred over frontend/'s."""
        from factory.verify import _ts_project_dirs

        self._write("tsconfig.json", self._TSCONFIG)
        self._write("frontend/tsconfig.json", self._TSCONFIG)
        p = self._write("frontend/app.ts", "export const a = 1\n")
        dirs = _ts_project_dirs(self.root, [p])
        self.assertEqual(dirs, [(self.root / "frontend").resolve()])


class GoWithoutModuleTests(unittest.TestCase):
    """A .go file with no go.mod must still be parse-checked, not waved through.

    Found by a LIVE run of the challenge: after a re-architecture the coder wrote
    `backend/internal/service/overdue.go` and its test but never a `go.mod`. The
    "no module yet, so warn" rule — reasonable in the abstract — meant gate-build
    PASSED having verified nothing at all. `go build` needs a module; `gofmt -e`
    does not, so there is no excuse for skipping the syntax check.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_broken_go_without_a_module_still_FAILS(self) -> None:
        p = self._write("backend/internal/service/overdue.go",
                        "package service\n\nfunc Overdue( {\n}\n")
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertEqual(names.get("go_parse"), "fail", result.summary)
        self.assertFalse(result.passed,
                         "a syntax error must block even with no go.mod present")

    def test_valid_go_without_a_module_passes_the_parse_check(self) -> None:
        p = self._write("backend/internal/service/overdue.go",
                        "package service\n\n// Overdue reports nothing yet.\n"
                        "func Overdue() bool { return false }\n")
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertEqual(names.get("go_parse"), "pass", result.summary)
        self.assertTrue(result.passed)

    def test_the_missing_module_is_still_surfaced_as_a_warning(self) -> None:
        """Passing the parse check is not the same as being buildable — the
        operator must still be told the module manifest is absent."""
        p = self._write("backend/internal/service/overdue.go",
                        "package service\n\n// Overdue reports nothing.\n"
                        "func Overdue() bool { return false }\n")
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertEqual(names.get("go_build"), "warn")
        self.assertIn("go.mod", next(
            c.detail for c in result.checks if c.name == "go_build"))

    def test_a_real_module_uses_the_full_build_not_just_the_parse_check(self) -> None:
        self._write("backend/go.mod", "module supportflow\n\ngo 1.26\n")
        p = self._write("backend/internal/service/overdue.go",
                        "package service\n\n// Overdue reports nothing.\n"
                        "func Overdue() bool { return false }\n")
        result = verify_changes([p], root=self.root)
        names = {c.name: c.status for c in result.checks}
        self.assertEqual(names.get("go_build"), "pass", result.summary)
        self.assertNotIn("go_parse", names,
                         "the parse fallback is only for the module-less case")
