"""The coder node lands a completed task's code blocks in the project repo.

The write itself is `factory.workspace.materialize` (pinned in
tests/workspace/test_materialize.py); this drives it through `node_coder_agent`
with only the agent-call boundary patched — never a live opencode call.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.pipeline.nodes.coder import node_coder_agent
from factory.state.db import create_story, get_db, init_db, start_run


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
            "factory.pipeline.agent_calls.run_agent",
            return_value=AgentResult("coder-agent", json.dumps(output), 0.1, 0),
        ):
            result = node_coder_agent(state)

        # Coder no longer self-finalizes; it hands the completed story to the tester.
        self.assertEqual(result["next_action"], "complete")
        self.assertEqual((self.repo_path / "README.md").read_text(), "# App\n")


if __name__ == "__main__":
    unittest.main()
