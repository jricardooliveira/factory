"""Generated tests run in a disposable container — never on the host (operator decision).

Review task T03, decided 2026-10-02: a container. The code is COPIED in (`docker cp`),
nothing from the host is mounted, no host environment or credential is passed, CPU /
memory / process limits apply, and a hard timeout kills the container. With a runtime
available, tests run by default (`FACTORY_RUN_TESTS` unset = auto); `=0` turns them
off; `=1` demands them. Without a runtime they are reported as not run — never run on
the host instead. These tests mock the runtime; the real-container acceptance proofs
are in test_sandbox_container.py (opt-in: FACTORY_SANDBOX_TESTS=1).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.verification import sandbox
from factory.verification.base import test_mode
from factory.verification.python import run_tests
from factory.verification.sandbox import SandboxResult


def _done(rc: int = 0, out: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out, "")


class TestModeTests(unittest.TestCase):
    def test_unset_is_auto_zero_is_off_one_is_on(self) -> None:
        for value, mode in (("", "auto"), ("0", "off"), ("false", "off"), ("1", "on"),
                            ("yes", "on")):
            with self.subTest(value=value), patch.dict(os.environ, {"FACTORY_RUN_TESTS": value}):
                self.assertEqual(test_mode(), mode)


class ExecuteTests(unittest.TestCase):
    """The container is created with limits and no host access, filled by copy, run
    with a timeout, and always removed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        (self.repo / "app.py").write_text("x = 1\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _execute(self, run_side_effect):
        with patch.object(sandbox, "container_runtime", return_value="docker"), \
                patch.object(sandbox, "_ensure_image", return_value=None), \
                patch("factory.verification.sandbox.subprocess.run",
                      side_effect=run_side_effect) as run:
            result = sandbox.run_in_container(self.repo, "python", ".", ["python", "-m", "pytest", "-q"],
                                     timeout=30)
        return result, [c.args[0] for c in run.call_args_list], run

    def test_the_container_gets_limits_a_copy_and_no_host_access(self) -> None:
        result, calls, run = self._execute(lambda *a, **k: _done(0, "1 passed"))
        create, copy, start, remove = calls[0], calls[1], calls[2], calls[-1]
        self.assertEqual(create[:2], ["docker", "create"])
        for flag in ("--cpus", "--memory", "--pids-limit"):
            self.assertIn(flag, create)
        joined = " ".join(create)
        self.assertNotIn("-v", create)              # nothing from the host is mounted
        self.assertNotIn("--volume", create)
        self.assertNotIn(str(self.repo), joined)
        self.assertNotIn("--env-file", create)
        self.assertNotIn("--privileged", create)
        self.assertEqual(copy[:2], ["docker", "cp"])
        self.assertEqual(copy[2:], [f"{self.repo}/.", copy[3]])  # the code goes in as a copy
        self.assertTrue(copy[3].endswith(":/src"))
        self.assertEqual(start[:3], ["docker", "start", "-a"])
        self.assertEqual(remove[:3], ["docker", "rm", "-f"])
        self.assertEqual(result.returncode, 0)
        for call in run.call_args_list:              # a hung read can never block the gate
            self.assertIs(call.kwargs.get("stdin"), subprocess.DEVNULL)

    def test_a_hung_run_is_killed_and_the_container_removed(self) -> None:
        def run(argv, **kwargs):
            if argv[:2] == ["docker", "start"]:
                raise subprocess.TimeoutExpired(argv, 30)
            return _done()
        result, calls, _ = self._execute(run)
        self.assertIsNone(result.returncode)
        self.assertTrue(any(c[:2] == ["docker", "kill"] for c in calls))
        self.assertEqual(calls[-1][:3], ["docker", "rm", "-f"])

    def test_the_container_is_removed_even_when_the_copy_fails(self) -> None:
        def run(argv, **kwargs):
            if argv[:2] == ["docker", "cp"]:
                return _done(1, "no space")
            return _done()
        result, calls, _ = self._execute(run)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1][:3], ["docker", "rm", "-f"])


class RunTestsModeTests(unittest.TestCase):
    """What `pytest_run` reports in each mode — never a host execution."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "test_x.py").write_text("def test_ok():\n    assert True\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, mode: str, runtime: str | None, result: SandboxResult | None = None):
        with patch.dict(os.environ, {"FACTORY_RUN_TESTS": mode}), \
                patch.object(sandbox, "container_runtime", return_value=runtime), \
                patch.object(sandbox, "run_in_container", return_value=result) as execute, \
                patch("subprocess.run", side_effect=AssertionError("ran on the host")):
            check = run_tests(self.root)
        return check, execute

    def test_off_runs_nothing(self) -> None:
        check, execute = self._run("0", "docker")
        self.assertEqual(check.status, "skip")
        execute.assert_not_called()

    def test_auto_without_a_runtime_reports_tests_not_run(self) -> None:
        check, execute = self._run("", None)
        self.assertEqual(check.status, "skip")
        self.assertIn("container", check.detail)
        execute.assert_not_called()

    def test_demanded_without_a_runtime_fails_rather_than_running_on_the_host(self) -> None:
        check, _ = self._run("1", None)
        self.assertEqual(check.status, "fail")
        self.assertIn("container", check.detail)

    def test_auto_with_a_runtime_runs_in_the_container(self) -> None:
        check, execute = self._run("", "docker", SandboxResult(0, "1 passed"))
        self.assertEqual(check.status, "pass")
        repo, toolchain = execute.call_args.args[:2]
        self.assertEqual((repo, toolchain), (self.root, "python"))

    def test_a_failing_suite_fails(self) -> None:
        check, _ = self._run("1", "docker", SandboxResult(1, "1 failed"))
        self.assertEqual(check.status, "fail")

    def test_zero_collected_tests_prove_nothing(self) -> None:
        check, _ = self._run("1", "docker", SandboxResult(5, "no tests ran"))
        self.assertEqual(check.status, "skip")

    def test_a_timeout_fails(self) -> None:
        check, _ = self._run("1", "docker", SandboxResult(None, "timed out"))
        self.assertEqual(check.status, "fail")
        self.assertIn("timed out", check.detail)


if __name__ == "__main__":
    unittest.main()
