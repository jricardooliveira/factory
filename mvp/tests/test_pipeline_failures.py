"""Tests for pipeline failure bookkeeping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.opencode_client import AgentResult
from factory.pipeline import node_spec_agent
from factory.state.db import create_story, get_db, init_db, start_run


class PipelineFailureTests(unittest.TestCase):
    """Failed early agent output should finalize the pipeline run."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.db_path = self.root / "factory.db"
        init_db(self.db_path)
        with get_db(self.db_path) as conn:
            create_story(conn, "US-0001", "Pending", "-h")
            self.run_id = start_run(conn, "US-0001")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_spec_agent_invalid_json_marks_run_blocked_not_running(self) -> None:
        state = {
            "request": "-h",
            "story_id": "US-0001",
            "run_id": self.run_id,
            "db_path": str(self.db_path),
            "opencode_cwd": str(self.root),
        }

        with patch(
            "factory.pipeline.run_agent",
            return_value=AgentResult("spec-agent", "ERROR: opencode help", 0.7, 0),
        ):
            result = node_spec_agent(state)

        with get_db(self.db_path) as conn:
            run = dict(conn.execute("SELECT status, finished_at, error FROM pipeline_runs").fetchone())

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(run["status"], "blocked")
        self.assertIsNotNone(run["finished_at"])
        self.assertIn("spec-agent did not return JSON", run["error"])
