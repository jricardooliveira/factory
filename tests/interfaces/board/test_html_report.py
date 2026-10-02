"""Tests for factory visualization report generation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.interfaces.board.html_report import generate_factory_visualization
from factory.state.db import (
    create_story,
    finish_run,
    get_db,
    init_db,
    log_agent,
    log_gate,
    start_run,
)
from factory.workspace.projects import create_project


class FactoryVisualizationTests(unittest.TestCase):
    """Static HTML report behavior."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.db_path = self.root / "factory.db"
        init_db(self.db_path)

        project = create_project(
            self.db_path,
            factory_root=self.root,
            slug="demo-app",
            name="Demo App",
        )
        with get_db(self.db_path) as conn:
            create_story(
                conn,
                "US-0001",
                "Pending",
                "Add a health endpoint",
                project_id=project["id"],
            )
            run_id = start_run(conn, "US-0001", project_id=project["id"])
            log_agent(conn, run_id, "spec-agent", "prompt", "{}", verdict="pass", duration_secs=1.2)
            log_gate(conn, run_id, "gate-1-spec", True, "Story is scoped")
            log_agent(conn, run_id, "architect-agent", "prompt", "{}", verdict="fail", duration_secs=2.5)
            log_gate(conn, run_id, "gate-2-architect", False, "Architect verdict is 'fail'")
            finish_run(conn, run_id, "failed", error="Gate 2 failed: Architect verdict is 'fail'")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_generate_factory_visualization_writes_self_contained_html(self) -> None:
        output = generate_factory_visualization(self.db_path, self.root / "factory.html")

        html = output.read_text()
        self.assertIn("<!doctype html>", html)
        self.assertIn("Factory Flow Visualization", html)
        self.assertIn("Demo App", html)
        self.assertIn("spec-agent", html)
        self.assertIn("gate-1", html)
        self.assertIn("architect-agent", html)
        self.assertIn("gate-2", html)
        self.assertIn("coder-agent", html)
        self.assertIn("FAILED", html)
        self.assertIn("Architect verdict is &#x27;fail&#x27;", html)
        self.assertIn("factory review 1", html)


class FactoryVisualizationCliTests(unittest.TestCase):
    """CLI behavior for visualization output."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        init_db(Path("factory.db"))

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self._tmpdir.cleanup()

    def test_visualize_command_writes_default_report(self) -> None:
        from factory.interfaces.cli.main import main

        with patch("sys.argv", ["factory", "visualize"]):
            main()

        self.assertTrue(Path("factory-visualization.html").exists())

    def test_visualize_command_accepts_output_path(self) -> None:
        from factory.interfaces.cli.main import main

        with patch("sys.argv", ["factory", "visualize", "--output", "reports/factory.html"]):
            main()

        self.assertTrue(Path("reports/factory.html").exists())
