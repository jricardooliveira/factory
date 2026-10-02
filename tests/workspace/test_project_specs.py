"""Tests for project specification templates."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.domain.project_spec import ProjectSpec
from factory.workspace.templates import create_project_spec, get_stack_template, write_project_spec


class ProjectSpecTemplateTests(unittest.TestCase):
    """Stack templates produce valid project specs."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_fastapi_template_returns_valid_project_spec(self) -> None:
        spec = get_stack_template("fastapi", name="Orders API")

        self.assertIsInstance(spec, ProjectSpec)
        self.assertEqual(spec.name, "Orders API")
        self.assertEqual(spec.language, "Python 3.12")
        self.assertEqual(spec.framework, "FastAPI")
        self.assertEqual(spec.api_style, "REST with OpenAPI")
        self.assertIn("Business logic lives in services, not routes", spec.conventions)
        self.assertIn("No GraphQL", spec.forbidden)

    def test_write_project_spec_creates_json_file(self) -> None:
        spec_path = write_project_spec(
            self.root / "project-spec.json",
            create_project_spec("fastapi", name="Demo API", description="Demo service"),
        )

        data = json.loads(spec_path.read_text())
        self.assertEqual(data["name"], "Demo API")
        self.assertEqual(data["description"], "Demo service")
        self.assertEqual(ProjectSpec.model_validate(data).framework, "FastAPI")


class ProjectSpecCliTests(unittest.TestCase):
    """CLI behavior for spec initialization."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.old_cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self._tmpdir.cleanup()

    def test_spec_init_command_writes_project_spec_json(self) -> None:
        from factory.interfaces.cli.main import main

        with patch(
            "sys.argv",
            [
                "factory",
                "spec",
                "init",
                "orders-api",
                "--stack",
                "fastapi",
                "--name",
                "Orders API",
            ],
        ):
            main()

        data = json.loads(Path("project-spec.json").read_text())
        self.assertEqual(data["name"], "Orders API")
        self.assertEqual(data["framework"], "FastAPI")
