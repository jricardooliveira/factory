"""`factory project create` / `factory run --project`: the CLI side of the project registry.

The registry itself is pinned in tests/workspace/test_factory_projects.py; these
tests only check that the console entry point lands everything in $FACTORY_HOME
and dispatches to the run service.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from factory.state.db import get_db
from factory.workspace import layout


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

    def test_project_create_command_registers_project_in_factory_home(self) -> None:
        from factory.interfaces.cli.main import main

        with patch("sys.argv", ["factory", "project", "create", "cli-app", "--name", "CLI App"]):
            main()

        self.assertFalse(Path("factory.db").exists(), "the DB must never land in the cwd")
        self.assertFalse(Path("projects").exists(), "projects must never land in the cwd")
        self.assertTrue((layout.projects_dir() / "cli-app" / ".git").exists())
        with get_db(layout.db_path()) as conn:
            row = conn.execute("SELECT id, slug, name FROM projects").fetchone()

        self.assertEqual(dict(row), {"id": "PROJ-001", "slug": "cli-app", "name": "CLI App"})

    def test_project_create_command_accepts_stack_and_writes_spec_path(self) -> None:
        from factory.interfaces.cli.main import main

        with patch(
            "sys.argv",
            ["factory", "project", "create", "cli-app", "--name", "CLI App", "--stack", "fastapi"],
        ):
            main()

        spec_path = layout.projects_dir() / "cli-app" / "project-spec.json"
        with get_db(layout.db_path()) as conn:
            row = conn.execute("SELECT spec_path FROM projects WHERE id = 'PROJ-001'").fetchone()

        # Stored relative to the home (self-contained), resolved on the way out.
        self.assertEqual(row["spec_path"], "projects/cli-app/project-spec.json")
        resolved = layout.resolve_location(row["spec_path"], layout.db_path())
        self.assertEqual(resolved.resolve(), spec_path.resolve())
        self.assertEqual(json.loads(spec_path.read_text())["framework"], "FastAPI")

    def test_run_command_dispatches_to_project_pipeline(self) -> None:
        from factory.interfaces.cli.main import main

        with patch("factory.runs.run_project_pipeline") as run_project_pipeline:
            with patch(
                "sys.argv",
                # --no-interview: PROJ-001 has no approved brief (not even a row);
                # the brief requirement is pinned in test_interview_command.py.
                ["factory", "run", "--project", "PROJ-001", "--no-interview",
                 "Add a health endpoint"],
            ):
                main()

        run_project_pipeline.assert_called_once_with(
            "PROJ-001", "Add a health endpoint", db_path=layout.db_path(), on_event=ANY
        )

    def test_help_flag_prints_usage_without_creating_pipeline_run(self) -> None:
        from factory.interfaces.cli.main import main

        with patch("sys.argv", ["factory", "-h"]):
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(raised.exception.code, 0)
        self.assertFalse(Path("factory.db").exists())
        self.assertFalse(layout.db_path().exists())


if __name__ == "__main__":
    unittest.main()
