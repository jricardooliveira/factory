"""Tests for factory-level project registry behavior."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.interfaces.cli import run_project_pipeline
from factory.workspace.projects import create_project, get_project, list_projects
from factory.state.db import get_db, init_db


class FactoryProjectTests(unittest.TestCase):
    """Project registry and project-scoped run behavior."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.db_path = self.root / "factory.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_create_project_scaffolds_project_and_persists_registry_row(self) -> None:
        project = create_project(
            self.db_path,
            factory_root=self.root,
            slug="My App",
            name="My Application",
        )

        self.assertEqual(project["id"], "PROJ-001")
        self.assertEqual(project["slug"], "my-app")
        self.assertEqual(project["name"], "My Application")
        self.assertEqual(project["status"], "active")

        project_dir = self.root / "projects" / "PROJ-001-my-app"
        self.assertEqual(Path(project["repo_path"]), project_dir / "repo")
        self.assertTrue((project_dir / "PROJECT_RULES.md").exists())
        self.assertTrue((project_dir / "state").is_dir())
        self.assertTrue((project_dir / "docs" / "work" / "tasks").is_dir())
        self.assertTrue((project_dir / "docs" / "pipeline").is_dir())
        self.assertTrue((project_dir / "docs" / "context").is_dir())
        self.assertTrue((project_dir / "docs" / "architecture" / "adr").is_dir())
        self.assertTrue((project_dir / "docs" / "releases").is_dir())
        self.assertTrue((project_dir / "repo").is_dir())

        persisted = get_project(self.db_path, "PROJ-001")
        self.assertEqual(persisted, project)

    def test_create_project_with_stack_writes_default_project_spec(self) -> None:
        project = create_project(
            self.db_path,
            factory_root=self.root,
            slug="orders-api",
            name="Orders API",
            stack="fastapi",
        )

        spec_path = self.root / "projects" / "PROJ-001-orders-api" / "project-spec.json"
        data = json.loads(spec_path.read_text())
        self.assertEqual(data["name"], "Orders API")
        self.assertEqual(data["framework"], "FastAPI")
        self.assertEqual(project["spec_path"], str(spec_path))

    def test_create_project_allocates_incrementing_ids_and_lookup_accepts_slug(self) -> None:
        create_project(self.db_path, factory_root=self.root, slug="first", name="First")
        second = create_project(self.db_path, factory_root=self.root, slug="second", name="Second")

        self.assertEqual(second["id"], "PROJ-002")
        self.assertEqual(get_project(self.db_path, "second")["id"], "PROJ-002")
        self.assertEqual([p["id"] for p in list_projects(self.db_path)], ["PROJ-001", "PROJ-002"])

    def test_run_project_pipeline_loads_project_spec_and_uses_project_repo_as_cwd(self) -> None:
        spec_path = self.root / "spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "name": "Spec App",
                    "description": "A test app",
                    "language": "Python 3.12",
                    "framework": "FastAPI",
                    "database": "SQLite",
                }
            )
        )
        project = create_project(
            self.db_path,
            factory_root=self.root,
            slug="spec-app",
            name="Spec App",
            spec_path=spec_path,
        )

        with patch("factory.interfaces.cli.run_pipeline") as run_pipeline:
            run_project_pipeline(
                "PROJ-001",
                "Create the initial API",
                db_path=self.db_path,
            )

        run_pipeline.assert_called_once()
        args, kwargs = run_pipeline.call_args
        self.assertEqual(args, ("Create the initial API",))
        self.assertEqual(kwargs["opencode_cwd"], project["repo_path"])
        self.assertIn("**Project:** Spec App", kwargs["project_spec_text"])

    def test_run_project_pipeline_fails_for_unknown_project(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown project"):
            run_project_pipeline("missing", "Do work", db_path=self.db_path)


class FactoryProjectCliTests(unittest.TestCase):
    """CLI entrypoint behavior for project commands."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.old_cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self._tmpdir.cleanup()

    def test_project_create_command_registers_project_in_current_factory_root(self) -> None:
        from factory.interfaces.cli import main

        with patch("sys.argv", ["factory", "project", "create", "cli-app", "--name", "CLI App"]):
            main()

        with get_db(Path("factory.db")) as conn:
            row = conn.execute("SELECT id, slug, name FROM projects").fetchone()

        self.assertEqual(dict(row), {"id": "PROJ-001", "slug": "cli-app", "name": "CLI App"})

    def test_project_create_command_accepts_stack_and_writes_spec_path(self) -> None:
        from factory.interfaces.cli import main

        with patch(
            "sys.argv",
            ["factory", "project", "create", "cli-app", "--name", "CLI App", "--stack", "fastapi"],
        ):
            main()

        spec_path = self.root / "projects" / "PROJ-001-cli-app" / "project-spec.json"
        with get_db(Path("factory.db")) as conn:
            row = conn.execute("SELECT spec_path FROM projects WHERE id = 'PROJ-001'").fetchone()

        self.assertEqual(Path(row["spec_path"]).resolve(), spec_path.resolve())
        self.assertEqual(json.loads(spec_path.read_text())["framework"], "FastAPI")

    def test_run_command_dispatches_to_project_pipeline(self) -> None:
        from factory.interfaces.cli import main

        with patch("factory.interfaces.cli.run_project_pipeline") as run_project_pipeline:
            with patch(
                "sys.argv",
                ["factory", "run", "--project", "PROJ-001", "Add a health endpoint"],
            ):
                main()

        run_project_pipeline.assert_called_once_with("PROJ-001", "Add a health endpoint")

    def test_help_flag_prints_usage_without_creating_pipeline_run(self) -> None:
        from factory.interfaces.cli import main

        with patch("sys.argv", ["factory", "-h"]):
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(raised.exception.code, 0)
        self.assertFalse(Path("factory.db").exists())
