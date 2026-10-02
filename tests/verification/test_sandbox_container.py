"""Acceptance proofs against a REAL container runtime (review task T03).

Opt-in — FACTORY_SANDBOX_TESTS=1 — because they pull images and install pytest over
the network. They prove what the mocked tests can only assert about command lines:
a generated test cannot see a planted host secret (file or environment variable),
cannot write into the host repository, and a hung test is killed with no container
left behind. Run them after changing anything in `verification/sandbox.py`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.verification import sandbox
from factory.verification.python import run_tests

ENABLED = os.environ.get("FACTORY_SANDBOX_TESTS") == "1" and sandbox.container_runtime()


@unittest.skipUnless(ENABLED, "set FACTORY_SANDBOX_TESTS=1 with a running container runtime")
class RealContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.secret = self.root / "host-secret.txt"
        self.secret.write_text("do not leak")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _suite(self, body: str):
        (self.repo / "test_probe.py").write_text(body)
        with patch.dict(os.environ, {"FACTORY_RUN_TESTS": "1",
                                     "FACTORY_PLANTED_SECRET": "s3cr3t"}):
            return run_tests(self.repo)

    def test_a_passing_and_a_failing_suite(self) -> None:
        self.assertEqual(self._suite("def test_ok():\n    assert 1 + 1 == 2\n").status, "pass")
        self.assertEqual(self._suite("def test_bad():\n    assert 1 == 2\n").status, "fail")

    def test_host_files_and_environment_are_invisible(self) -> None:
        check = self._suite(
            "import os\n\n"
            "def test_no_host_access():\n"
            f"    assert not os.path.exists({str(self.secret)!r})\n"
            "    assert os.environ.get('FACTORY_PLANTED_SECRET') is None\n"
            f"    assert not os.path.exists({str(Path.home() / '.ssh')!r})\n"
        )
        self.assertEqual(check.status, "pass", check.detail)

    def test_the_host_repository_cannot_be_written(self) -> None:
        check = self._suite(
            "def test_write():\n"
            "    open('written-by-the-test.txt', 'w').write('x')\n"
        )
        self.assertEqual(check.status, "pass", check.detail)
        self.assertFalse((self.repo / "written-by-the-test.txt").exists())

    def test_a_go_module_in_a_subdirectory_is_tested_in_the_go_image(self) -> None:
        from factory.verification.go import run_go_tests

        module = self.repo / "backend"
        (module / "internal").mkdir(parents=True)
        (module / "go.mod").write_text("module example.com/backend\n\ngo 1.22\n")
        (module / "internal" / "v.go").write_text("package internal\n\nfunc V() int { return 2 }\n")
        test = module / "internal" / "v_test.go"
        with patch.dict(os.environ, {"FACTORY_RUN_TESTS": "1"}):
            test.write_text('package internal\n\nimport "testing"\n\n'
                            "func TestV(t *testing.T) { if V() != 2 { t.Fatal(V()) } }\n")
            self.assertEqual(run_go_tests([module], self.repo).status, "pass")
            test.write_text('package internal\n\nimport "testing"\n\n'
                            "func TestV(t *testing.T) { if V() != 1 { t.Fatal(V()) } }\n")
            self.assertEqual(run_go_tests([module], self.repo).status, "fail")

    def test_a_hung_test_is_killed_and_nothing_is_left_running(self) -> None:
        runtime = sandbox.container_runtime()
        with patch("factory.verification.python.TEST_TIMEOUT", 20):
            check = self._suite("import time\n\ndef test_hang():\n    time.sleep(600)\n")
        self.assertEqual(check.status, "fail")
        self.assertIn("timed out", check.detail)
        left = subprocess.run([runtime, "ps", "-q", "--filter", "name=factory-test-"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(left, "")


if __name__ == "__main__":
    unittest.main()
