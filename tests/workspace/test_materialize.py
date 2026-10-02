"""Tests for writing coder-agent code blocks to disk."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.workspace.materialize import materialize_code_blocks, normalize_block_path


class MaterializeCodeBlocksTests(unittest.TestCase):
    """Code block path validation and file writing behavior."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_materialize_code_blocks_writes_nested_relative_files(self) -> None:
        written = materialize_code_blocks(
            [
                {
                    "path": "src/app.py",
                    "content": "def hello():\n    return 'world'\n",
                    "action": "create",
                }
            ],
            root=self.root,
        )

        self.assertEqual(written, [(self.root / "src" / "app.py").resolve()])
        self.assertEqual((self.root / "src" / "app.py").read_text(), "def hello():\n    return 'world'\n")

    def test_materialize_code_blocks_rejects_paths_outside_root(self) -> None:
        for unsafe_path in ("../outside.py", "/tmp/outside.py"):
            with self.subTest(path=unsafe_path):
                with self.assertRaisesRegex(ValueError, "outside project root"):
                    materialize_code_blocks(
                        [{"path": unsafe_path, "content": "bad", "action": "create"}],
                        root=self.root,
                    )


class NormalizeBlockPathTests(unittest.TestCase):
    def test_strips_leading_repo_when_root_is_repo(self) -> None:
        root = Path("/tmp/PROJ-001/repo")
        self.assertEqual(normalize_block_path(root, "repo/app/main.py"), "app/main.py")
        self.assertEqual(normalize_block_path(root, "./repo/x.py"), "x.py")

    def test_no_change_when_root_not_repo(self) -> None:
        root = Path("/tmp/somewhere")
        self.assertEqual(normalize_block_path(root, "repo/app/main.py"), "repo/app/main.py")

    def test_no_change_for_normal_paths(self) -> None:
        root = Path("/tmp/PROJ-001/repo")
        self.assertEqual(normalize_block_path(root, "app/main.py"), "app/main.py")
        self.assertEqual(normalize_block_path(root, "main.py"), "main.py")

    def test_strips_leading_repo_at_a_project_repo_root_of_any_name(self) -> None:
        # A project directory IS its repo now ($FACTORY_HOME/projects/<slug>/), so
        # its name is the slug — but agents trained on "Source root: repo/" still
        # emit repo/-prefixed paths, which must land at the root.
        root = Path("/tmp/factory-home/projects/bookmarks")
        self.assertEqual(
            normalize_block_path(root, "repo/app/main.py", repo_root=True), "app/main.py"
        )
        self.assertEqual(normalize_block_path(root, "app/main.py", repo_root=True), "app/main.py")


class ReservedPathTests(unittest.TestCase):
    """The coder may not author the factory's evidence inside a product repo."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_reserved_paths_are_refused_and_nothing_is_written(self) -> None:
        from factory.workspace.layout import EVIDENCE_PATHS

        for owned in ("PROJECT_RULES.md", "project-spec.json", "docs/work/US-1/SPEC.md",
                      "docs/architecture/adr/ADR-x.md", "docs/releases/run-1.json",
                      "./PROJECT_RULES.md"):
            with self.subTest(path=owned):
                with self.assertRaisesRegex(ValueError, "factory-owned"):
                    materialize_code_blocks(
                        [{"path": "ok.py", "content": "X = 1\n", "action": "create"},
                         {"path": owned, "content": "forged", "action": "create"}],
                        root=self.root, reserved=EVIDENCE_PATHS,
                    )
                self.assertFalse((self.root / "ok.py").exists(), "all-or-nothing")

    def test_without_reservations_the_same_paths_are_ordinary_files(self) -> None:
        written = materialize_code_blocks(
            [{"path": "docs/work/notes.md", "content": "n", "action": "create"}], root=self.root
        )
        self.assertEqual(len(written), 1)
