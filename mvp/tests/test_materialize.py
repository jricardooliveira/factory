"""Tests for writing coder-agent code blocks to disk."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.materialize import materialize_code_blocks, normalize_block_path
from factory.opencode_client import AgentResult
from factory.pipeline import node_coder_agent
from factory.state.db import create_story, get_db, init_db, start_run


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


class CoderAgentMaterializationTests(unittest.TestCase):
    """Coder node writes completed code blocks into the project repo."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.db_path = self.root / "factory.db"
        self.repo_path = self.root / "repo"
        self.repo_path.mkdir()
        init_db(self.db_path)
        with get_db(self.db_path) as conn:
            create_story(conn, "US-0001", "Pending", "Create app")
            self.run_id = start_run(conn, "US-0001")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_coder_agent_materializes_code_blocks_on_complete_verdict(self) -> None:
        output = {
            "verdict": "complete",
            "files_created": ["README.md"],
            "files_modified": [],
            "tests_added": [],
            "implementation_summary": "Created app skeleton",
            "code_blocks": [
                {"path": "README.md", "content": "# App\n", "action": "create"},
            ],
            "test_coverage": {"happy_path": True, "edge_case": False, "error_handling": False},
            "assumptions": [],
            "follow_ups": [],
        }
        state = {
            "request": "Create app",
            "story_id": "US-0001",
            "run_id": self.run_id,
            "db_path": str(self.db_path),
            "opencode_cwd": str(self.repo_path),
            "spec": {
                "title": "Create app",
                "problem": "no app exists",
                "why": "need a skeleton",
                "acceptance_criteria": ["readme exists", "documents the app"],
                "tasks": [
                    {
                        "id": "T-0001",
                        "title": "skeleton",
                        "purpose": "create README",
                        "scope": ["README.md"],
                        "completion_evidence": "README exists",
                    }
                ],
            },
            "architect": {"architecture_notes": "Use a README"},
        }

        with patch(
            "factory.pipeline.run_agent",
            return_value=AgentResult("coder-agent", json.dumps(output), 0.1, 0),
        ):
            result = node_coder_agent(state)

        # Coder no longer self-finalizes; it hands the completed story to the tester.
        self.assertEqual(result["next_action"], "complete")
        self.assertEqual((self.repo_path / "README.md").read_text(), "# App\n")
