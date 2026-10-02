"""The trust package must not overstate its own evidence.

EFFECTIVENESS.md §5 makes four artifacts non-negotiable for release sign-off, and
two of them were being reported dishonestly:

  §5.1 "tests actually run and pass" — was derived from gate booleans alone, so a
       package claimed `command: "pytest -q"` and `passed: true` when no test body
       had ever been executed (FACTORY_RUN_TESTS is off by default).
  §5.2 "the actual change set measured from git, NOT the agent's self-reported
       file list" — was hardcoded `source: "materialized"` and built from the
       coder's own `code_blocks`, in direct violation of the schema's `const: git`.

An operator who signs off against inflated evidence is worse off than one with no
package at all, so these are pinned here as first-class behaviour.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.evidence import trust_package as tp
from factory.verification import scope
from factory.workspace import git
from factory.state import db


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(root), capture_output=True, text=True, check=False,
    )


class GitMeasuredDiffTests(unittest.TestCase):
    """`diff` must be measured from git against the run's own base commit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "proj"
        self.repo.mkdir(parents=True)
        self.db_path = self.root / "f.db"
        db.init_db(self.db_path)
        git.git_init(self.repo)
        (self.repo / "pre_existing.py").write_text("OLD = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "pre-factory baseline", "--no-gpg-sign")
        self.base = git.git_head(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_with_changes(self) -> int:
        with db.get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO projects (id, slug, name, repo_path, status, created_at, updated_at)"
                " VALUES ('PROJ-001','p','P',?,'active',datetime('now'),datetime('now'))",
                (str(self.repo),),
            )
            db.create_story(conn, "US-0001", "Change", "do it", project_id="PROJ-001")
            rid = db.start_run(conn, "US-0001", project_id="PROJ-001", base_commit=self.base)
            db.log_agent(
                conn, rid, "coder-agent", "p",
                json.dumps({"verdict": "complete", "code_blocks": [
                    {"path": "new_module.py", "content": "NEW = 1\n"}]}),
                verdict="complete", stage_type="T-1",
            )
            db.log_gate(conn, rid, "gate-build", True, "[T-1] py_compile:pass, pytest_run:pass")
            db.log_agent(
                conn, rid, "tester-agent", "p",
                json.dumps({"overall": "pass", "ac_coverage": ["a"],
                            "security_verdict": "pass", "highest_severity": "none"}),
                verdict="pass",
            )
            db.log_gate(conn, rid, "gate-test", True, "ok")
            db.finish_run(conn, rid, "completed")
        # The factory really changed the repo: one new file, one edited file.
        (self.repo / "new_module.py").write_text("NEW = 1\n")
        (self.repo / "pre_existing.py").write_text("OLD = 2\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "factory: T-1", "--no-gpg-sign")
        return rid

    def test_base_commit_is_recorded_on_the_run(self) -> None:
        rid = self._run_with_changes()
        with db.get_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT base_commit FROM pipeline_runs WHERE id = ?", (rid,)
            ).fetchone()
        self.assertEqual(row["base_commit"], self.base)

    def test_diff_source_is_git_and_matches_the_schema_const(self) -> None:
        pkg = tp.assemble(self.db_path, self._run_with_changes())
        self.assertEqual(pkg["diff"]["source"], "git")
        self.assertEqual(tp.validate(pkg), [])

    def test_diff_reports_real_change_types_not_self_reported_creations(self) -> None:
        """The coder declared only `new_module.py`; git also saw the edit to an
        existing file. The package must show what git saw."""
        pkg = tp.assemble(self.db_path, self._run_with_changes())
        by_path = {f["path"]: f["change"] for f in pkg["diff"]["files"]}
        self.assertEqual(by_path.get("new_module.py"), "added")
        self.assertEqual(
            by_path.get("pre_existing.py"), "modified",
            "a modification to an existing file must not be reported as 'added'",
        )

    def test_diff_is_unavailable_and_invalid_when_there_is_no_git_repo(self) -> None:
        """No git measurement means the evidence bar is not met — the package must
        say so and fail validation rather than quietly present a self-report."""
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0009", "NoGit", "x")
            rid = db.start_run(conn, "US-0009")
            db.log_agent(conn, rid, "coder-agent", "p",
                         json.dumps({"verdict": "complete", "code_blocks": [
                             {"path": "c.py", "content": "x=1\n"}]}),
                         verdict="complete", stage_type="T-1")
            db.log_gate(conn, rid, "gate-build", True, "ok")
            db.log_gate(conn, rid, "gate-test", True, "ok")
            db.finish_run(conn, rid, "completed")
        pkg = tp.assemble(self.db_path, rid)
        self.assertEqual(pkg["diff"]["source"], "unavailable")
        errors = tp.validate(pkg)
        self.assertTrue(errors, "a non-git-measured diff must not validate")
        self.assertTrue(any("git" in e for e in errors), errors)
        self.assertEqual(pkg["next_authorization"], "operator-review")


class TestsClaimTests(unittest.TestCase):
    """`tests.passed` must mean tests RAN and passed — not 'a gate returned true'."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, gate_build_reason: str) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "T", "x")
            rid = db.start_run(conn, "US-0001")
            db.log_agent(conn, rid, "coder-agent", "p",
                         json.dumps({"verdict": "complete", "code_blocks": [
                             {"path": "c.py", "content": "x=1\n"}]}),
                         verdict="complete", stage_type="T-1")
            db.log_gate(conn, rid, "gate-build", True, gate_build_reason)
            db.log_agent(conn, rid, "tester-agent", "p",
                         json.dumps({"overall": "pass", "ac_coverage": ["a"],
                                     "security_verdict": "pass", "highest_severity": "none"}),
                         verdict="pass")
            db.log_gate(conn, rid, "gate-test", True, "ok")
            db.finish_run(conn, rid, "completed")
        return rid

    def test_compile_only_run_does_not_claim_tests_passed(self) -> None:
        """The default configuration compiles but never executes a test body."""
        pkg = tp.assemble(self.db_path, self._run("[T-1] py_compile:pass, pytest_collect:pass"))
        self.assertFalse(pkg["tests"]["executed"])
        self.assertFalse(
            pkg["tests"]["passed"],
            "no test body was executed, so the package must not claim tests passed",
        )
        self.assertEqual(pkg["tests"].get("command", ""), "")

    def test_executed_and_passing_run_claims_tests_passed(self) -> None:
        pkg = tp.assemble(self.db_path, self._run("[T-1] py_compile:pass, pytest_run:pass"))
        self.assertTrue(pkg["tests"]["executed"])
        self.assertTrue(pkg["tests"]["passed"])
        self.assertTrue(pkg["tests"]["command"])

    def test_executed_and_failing_run_does_not_claim_tests_passed(self) -> None:
        pkg = tp.assemble(self.db_path, self._run("[T-1] py_compile:pass, pytest_run:fail"))
        self.assertTrue(pkg["tests"]["executed"])
        self.assertFalse(pkg["tests"]["passed"])

    def _builds(self, *reasons: str) -> int:
        """A run whose gate-build ran several times (retries / later tasks)."""
        rid = self._run(reasons[0])
        with db.get_db(self.db_path) as conn:
            for reason in reasons[1:]:
                db.log_gate(conn, rid, "gate-build", True, reason)
        return rid

    def test_a_failure_fixed_by_a_retry_counts_as_passed(self) -> None:
        """Judged on the FINAL candidate: a failed attempt that a retry fixed used
        to sink the claim forever (found by an independent assessment, F1)."""
        pkg = tp.assemble(self.db_path, self._builds("[T-1] pytest_run:fail",
                                                     "[T-1] pytest_run:pass"))
        self.assertTrue(pkg["tests"]["passed"])

    def test_a_regression_after_a_pass_is_a_failure(self) -> None:
        pkg = tp.assemble(self.db_path, self._builds("[T-1] pytest_run:pass",
                                                     "[T-2] pytest_run:fail"))
        self.assertFalse(pkg["tests"]["passed"])

    def test_each_toolchain_keeps_its_own_newest_result(self) -> None:
        # A final frontend-only task must not erase the backend's real go test result.
        pkg = tp.assemble(self.db_path, self._builds("[T-1] go_test:pass",
                                                     "[T-2] tsc:pass"))
        self.assertTrue(pkg["tests"]["executed"])
        self.assertTrue(pkg["tests"]["passed"])

    def test_untested_package_is_not_release_ready(self) -> None:
        pkg = tp.assemble(self.db_path, self._run("[T-1] py_compile:pass"))
        self.assertEqual(pkg["next_authorization"], "operator-review")
        self.assertTrue(
            any("test" in b.lower() for b in pkg["blockers"]),
            f"the missing-test-execution gap must be named as a blocker: {pkg['blockers']}",
        )


class ScopeMeasurementTests(unittest.TestCase):
    """EFFECTIVENESS.md §6 claims the real diff is checked against the task's
    allowed scope. It wasn't — `task.scope` was rendered into the prompt and never
    compared to anything."""

    def test_paths_inside_declared_scope_are_not_violations(self) -> None:
        self.assertEqual(
            scope.paths_outside_scope(["src/users/routes.py"], ["src/users/"]), []
        )
        self.assertEqual(
            scope.paths_outside_scope(["src/users/routes.py"], ["src/users/routes.py"]), []
        )
        self.assertEqual(
            scope.paths_outside_scope(["src/users/routes.py"], ["src/users/*.py"]), []
        )

    def test_paths_outside_declared_scope_are_reported(self) -> None:
        self.assertEqual(
            scope.paths_outside_scope(
                ["src/users/routes.py", "src/billing/charge.py"], ["src/users/"]
            ),
            ["src/billing/charge.py"],
        )

    def test_tests_alongside_declared_source_are_allowed(self) -> None:
        """A coder is required to add tests (mandatory happy-path coverage), so a
        test file must never count as a scope violation on its own."""
        self.assertEqual(
            scope.paths_outside_scope(
                ["src/users/routes.py", "tests/test_users.py"], ["src/users/"]
            ),
            [],
        )

    def test_empty_scope_declares_nothing_and_forbids_nothing(self) -> None:
        """An unscoped task can't produce violations — otherwise every task
        without a declared scope would look like a breach."""
        self.assertEqual(scope.paths_outside_scope(["anything.py"], []), [])


if __name__ == "__main__":
    unittest.main()


class ToolchainManifestScopeTests(unittest.TestCase):
    """A file the toolchain structurally requires is not scope creep.

    Found by driving the SupportFlow challenge through the pipeline: a task scoped
    to `backend/internal/domain/` must still create `backend/go.mod`, because Go
    resolves packages from the module root — so EVERY Go story reported a scope
    violation and withheld release sign-off for a file it had no choice about.
    `_TEST_PATH_RE` already exempts tests for the same reason (the coder is
    *required* to add them); build manifests need the same exemption, or the
    signal trains the operator to ignore it.
    """

    def test_go_module_files_are_not_violations(self) -> None:
        self.assertEqual(
            scope.paths_outside_scope(
                ["backend/internal/domain/status.go", "backend/go.mod", "backend/go.sum"],
                ["backend/internal/domain/"],
            ),
            [],
        )

    def test_node_and_ts_manifests_are_not_violations(self) -> None:
        self.assertEqual(
            scope.paths_outside_scope(
                ["frontend/pages/index.vue", "frontend/package.json",
                 "frontend/tsconfig.json", "frontend/package-lock.json",
                 "frontend/nuxt.config.ts"],
                ["frontend/pages/"],
            ),
            [],
        )

    def test_python_manifests_are_not_violations(self) -> None:
        self.assertEqual(
            scope.paths_outside_scope(
                ["src/app/routes.py", "pyproject.toml", "requirements.txt"],
                ["src/app/"],
            ),
            [],
        )

    def test_real_out_of_scope_code_is_still_reported(self) -> None:
        """The exemption must not become a hole — application code outside the
        declared scope is exactly what this check exists to surface."""
        self.assertEqual(
            scope.paths_outside_scope(
                ["backend/internal/domain/status.go", "backend/go.mod",
                 "frontend/composables/useTickets.ts"],
                ["backend/internal/domain/"],
            ),
            ["frontend/composables/useTickets.ts"],
        )


class GoTestExecutionEvidenceTests(unittest.TestCase):
    """`tests.executed` must recognise every language's test run, not only pytest.

    Teaching gate-build to run `go test` without teaching the trust package to
    read it meant a Go story with a fully executed, passing suite still reported
    `tests.executed: false` and withheld sign-off — understating its own evidence,
    which is the mirror image of the overstating bug this file was written for.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, gate_reason: str) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Go story", "x")
            rid = db.start_run(conn, "US-0001")
            db.log_agent(conn, rid, "coder-agent", "p",
                         json.dumps({"verdict": "complete", "code_blocks": [
                             {"path": "backend/internal/domain/s.go", "content": "package domain\n"}]}),
                         verdict="complete", stage_type="T-0001")
            db.log_gate(conn, rid, "gate-build", True, gate_reason)
            db.log_agent(conn, rid, "tester-agent", "p",
                         json.dumps({"overall": "pass", "ac_coverage": ["a"],
                                     "security_verdict": "pass", "highest_severity": "none"}),
                         verdict="pass")
            db.log_gate(conn, rid, "gate-test", True, "ok")
            db.finish_run(conn, rid, "completed")
        return rid

    def test_a_passing_go_suite_counts_as_executed_and_passed(self) -> None:
        pkg = tp.assemble(
            self.db_path,
            self._run("[T-0001] go_build:pass, go_vet:pass, go_test:pass"),
        )
        self.assertTrue(pkg["tests"]["executed"])
        self.assertTrue(pkg["tests"]["passed"])
        self.assertTrue(pkg["tests"]["command"])

    def test_a_failing_go_suite_does_not_claim_tests_passed(self) -> None:
        pkg = tp.assemble(
            self.db_path,
            self._run("[T-0001] go_build:pass, go_vet:pass, go_test:fail"),
        )
        self.assertTrue(pkg["tests"]["executed"])
        self.assertFalse(pkg["tests"]["passed"])

    def test_a_compile_only_go_run_still_does_not_claim_tests(self) -> None:
        pkg = tp.assemble(self.db_path, self._run("[T-0001] go_build:pass, go_vet:pass"))
        self.assertFalse(pkg["tests"]["executed"])
        self.assertFalse(pkg["tests"]["passed"])

    def test_the_command_named_is_the_one_that_actually_ran(self) -> None:
        """It hardcoded 'pytest -q', so a Go story's package named a command that
        had never been executed."""
        pkg = tp.assemble(self.db_path, self._run("[T-0001] go_build:pass, go_test:pass"))
        self.assertEqual(pkg["tests"]["command"], "go test ./...")

    def test_a_mixed_stack_names_both_commands(self) -> None:
        pkg = tp.assemble(
            self.db_path,
            self._run("[T-0001] go_test:pass, py_compile:pass, pytest_run:pass"),
        )
        self.assertIn("go test ./...", pkg["tests"]["command"])
        self.assertIn("pytest -q", pkg["tests"]["command"])


class RepoPrefixScopeTests(unittest.TestCase):
    """Declared scope and the measured diff must be compared in the same space.

    Agents emit repo-rooted paths (`repo/backend/internal/domain`) because the
    factory tells them to, and `materialize.normalize_block_path` strips the
    `repo/` prefix when the working directory IS the repo. The declared *scope*
    strings were never normalised the same way, so they could never match a
    git-measured path — making EVERY file on EVERY real project run a scope
    violation, which withheld release sign-off on all of them.

    Found by a live run: the spec-agent declared
    `['repo/backend/internal/domain', 'repo/backend/internal/service']`.
    """

    def test_repo_prefixed_scope_matches_git_measured_paths(self) -> None:
        self.assertEqual(
            scope.paths_outside_scope(
                ["backend/internal/domain/overdue.go",
                 "backend/internal/service/overdue.go"],
                ["repo/backend/internal/domain", "repo/backend/internal/service"],
            ),
            [],
        )

    def test_dot_slash_repo_prefix_is_handled_too(self) -> None:
        self.assertEqual(
            scope.paths_outside_scope(
                ["backend/internal/http/handler.go"], ["./repo/backend/internal/http"]
            ),
            [],
        )

    def test_unprefixed_scope_still_works(self) -> None:
        self.assertEqual(
            scope.paths_outside_scope(
                ["backend/internal/domain/x.go"], ["backend/internal/domain/"]
            ),
            [],
        )

    def test_a_genuine_violation_is_still_caught_through_the_prefix(self) -> None:
        """Normalising the prefix must not turn the check into a rubber stamp."""
        self.assertEqual(
            scope.paths_outside_scope(
                ["backend/internal/domain/x.go", "frontend/pages/index.vue"],
                ["repo/backend/internal/domain"],
            ),
            ["frontend/pages/index.vue"],
        )
