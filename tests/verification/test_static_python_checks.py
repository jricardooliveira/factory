"""Default Python verification executes NO generated code (review tasks T03/T04).

`pytest --collect-only` imports conftest.py and every test module, so it ran
agent-written top-level code on the operator's machine even with
FACTORY_RUN_TESTS off — the opt-in that exists precisely to prevent that. A
static import check keeps what collection caught (the coder importing a local
module or name it never wrote) without running anything. Collection now belongs
to the opt-in path.

Also: a toolchain that is not installed can never let its files pass unverified.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.verification import verify_changes
from factory.verification.python import static_import_check


class StaticImportCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "__init__.py").write_text("")
        (self.root / "app" / "models.py").write_text(
            "from typing import Any\n\nclass Ticket:\n    pass\n\nLIMIT = 20\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _file(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def _check(self, *files: Path):
        return static_import_check(list(files), self.root)

    def test_imports_that_resolve_pass(self) -> None:
        f = self._file("app/api.py", "import os\nfrom app.models import Ticket, LIMIT\n"
                                      "import app.models\n")
        self.assertEqual(self._check(f).status, "pass")

    def test_a_local_module_that_was_never_written_fails(self) -> None:
        f = self._file("app/api.py", "from app.repository import save\n")
        check = self._check(f)
        self.assertEqual(check.status, "fail")
        self.assertIn("app.repository", check.detail)

    def test_a_name_the_local_module_does_not_define_fails(self) -> None:
        f = self._file("app/api.py", "from app.models import Invoice\n")
        check = self._check(f)
        self.assertEqual(check.status, "fail")
        self.assertIn("Invoice", check.detail)

    def test_third_party_imports_are_not_judged(self) -> None:
        f = self._file("app/api.py", "import fastapi\nfrom sqlalchemy import select\n")
        self.assertEqual(self._check(f).status, "pass")

    def test_relative_imports_are_resolved_against_the_package(self) -> None:
        good = self._file("app/views.py", "from .models import Ticket\n")
        bad = self._file("app/other.py", "from .missing import x\n")
        self.assertEqual(self._check(good).status, "pass")
        self.assertEqual(self._check(bad).status, "fail")

    def test_a_star_import_or_a_submodule_name_is_not_a_false_failure(self) -> None:
        self._file("app/star.py", "from app.models import *\n")
        f = self._file("app/api.py", "from app.star import anything\nfrom app import models\n")
        self.assertEqual(self._check(f).status, "pass")

    def test_a_syntax_error_is_left_to_py_compile(self) -> None:
        f = self._file("app/api.py", "def broken(:\n")
        self.assertEqual(self._check(f).status, "pass")


class DefaultVerificationRunsNoGeneratedCodeTests(unittest.TestCase):
    def test_import_time_code_in_a_generated_test_is_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "EXECUTED"
            test = root / "tests" / "test_x.py"
            test.parent.mkdir()
            test.write_text(f"open({str(marker)!r}, 'w').write('ran')\n\ndef test_x():\n    pass\n")
            (root / "conftest.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
            env = {k: v for k, v in os.environ.items() if k != "FACTORY_RUN_TESTS"}
            with patch.dict(os.environ, env, clear=True):
                result = verify_changes([test, root / "conftest.py"], root=root)
            self.assertFalse(marker.exists(), "default verification executed generated code")
            names = [c.name for c in result.checks]
            self.assertIn("py_imports", names)
            self.assertNotIn("pytest_collect", names)


class MissingToolchainTests(unittest.TestCase):
    """A check that cannot run cannot earn a pass (CLAUDE.md: a verdict must be earned)."""

    def _verify(self, name: str, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / name
            path.write_text(text)
            if name.endswith(".go"):
                (root / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")
            if name.endswith(".ts"):
                (root / "tsconfig.json").write_text("{}\n")
            with patch("shutil.which", return_value=None):
                return verify_changes([path], root=root)

    def test_go_files_without_go_installed_fail_verification(self) -> None:
        result = self._verify("main.go", "package main\n\nfunc main() {}\n")
        self.assertFalse(result.passed)
        self.assertIn("not installed", " ".join(c.detail for c in result.checks))

    def test_js_files_without_node_installed_fail_verification(self) -> None:
        self.assertFalse(self._verify("app.js", "console.log(1)\n").passed)

    def test_ts_files_without_tsc_installed_fail_verification(self) -> None:
        self.assertFalse(self._verify("app.ts", "const x: number = 1\n").passed)


if __name__ == "__main__":
    unittest.main()
