"""Tests for the deterministic repo interface inventory (brownfield awareness)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.repo_map import build_repo_inventory


class RepoInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_repo_returns_empty(self) -> None:
        self.assertEqual(build_repo_inventory(self.root), "")

    def test_lists_functions_and_classes_with_signatures(self) -> None:
        (self.root / "app").mkdir()
        (self.root / "app" / "__init__.py").write_text("")
        (self.root / "app" / "models.py").write_text(
            "class User:\n"
            "    def login(self, pw: str) -> bool:\n"
            "        return True\n\n"
            "def make_user(name: str) -> 'User':\n"
            "    return User()\n"
        )
        inv = build_repo_inventory(self.root)
        self.assertIn("app/models.py", inv)
        self.assertIn("def make_user(name: str)", inv)
        self.assertIn("class User", inv)
        self.assertIn("login", inv)  # public method surfaced

    def test_ignores_noise_dirs(self) -> None:
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "x.py").write_text("def hidden():\n    pass\n")
        (self.root / "real.py").write_text("def shown():\n    pass\n")
        inv = build_repo_inventory(self.root)
        self.assertIn("shown", inv)
        self.assertNotIn("hidden", inv)

    def test_unparseable_file_is_still_listed(self) -> None:
        (self.root / "broken.py").write_text("def (:\n")
        inv = build_repo_inventory(self.root)
        self.assertIn("broken.py", inv)

    def test_truncates_when_too_many_files(self) -> None:
        for i in range(80):
            (self.root / f"m{i}.py").write_text("def f():\n    pass\n")
        inv = build_repo_inventory(self.root, max_files=10)
        self.assertIn("more file", inv.lower())


if __name__ == "__main__":
    unittest.main()
