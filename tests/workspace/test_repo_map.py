"""Tests for the deterministic repo interface inventory (brownfield awareness)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.workspace.repo_map import build_repo_inventory


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


class GoInventoryTests(unittest.TestCase):
    """Review task T05: Go files were invisible to the architect and the coder, so
    every later SupportFlow story would be designed blind to the existing backend."""

    def test_exported_go_functions_types_and_methods_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal" / "http").mkdir(parents=True)
            (root / "internal" / "http" / "router.go").write_text(
                "package http\n\n"
                "type Server struct {\n\tlog *slog.Logger\n}\n\n"
                "type Store interface {\n\tGet(id string) error\n}\n\n"
                "func NewRouter(s *Server) http.Handler {\n\treturn nil\n}\n\n"
                "func (s *Server) Health(w http.ResponseWriter, r *http.Request) {\n}\n\n"
                "func helper() {}\n"
            )
            (root / "vendor" / "x").mkdir(parents=True)
            (root / "vendor" / "x" / "x.go").write_text("package x\n\nfunc Vendored() {}\n")
            inventory = build_repo_inventory(root)
        self.assertIn("internal/http/router.go", inventory)
        self.assertIn("type Server struct", inventory)
        self.assertIn("type Store interface", inventory)
        self.assertIn("func NewRouter(s *Server) http.Handler", inventory)
        self.assertIn("func (s *Server) Health(w http.ResponseWriter, r *http.Request)", inventory)
        self.assertNotIn("helper", inventory)  # unexported
        self.assertNotIn("Vendored", inventory)  # vendor/ is not the project's code

if __name__ == "__main__":
    unittest.main()
