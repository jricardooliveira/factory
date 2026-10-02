"""`factory workspace`: where the factory's state lives, at a glance."""

from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from factory.state import db
from factory.workspace import layout
from factory.workspace.projects import create_project


def _run(*argv: str) -> str:
    from factory.interfaces.cli.main import main

    buf = io.StringIO()
    with patch("factory.interfaces.render.output.console", Console(file=buf, width=200)):
        with patch("sys.argv", ["factory", *argv]):
            main()
    return buf.getvalue()


class WorkspaceCommandTests(unittest.TestCase):
    def test_prints_home_db_and_projects(self) -> None:
        create_project(layout.db_path(), slug="bookmarks")
        out = _run("workspace")
        self.assertIn(str(layout.home()), out)
        self.assertIn(str(layout.db_path()), out)
        self.assertIn("bookmarks", out)
        self.assertIn("PROJ-001", out)
        self.assertIn(str(layout.projects_dir() / "bookmarks"), out)

    def test_a_fresh_home_is_reported_without_creating_the_db(self) -> None:
        out = _run("workspace")
        self.assertIn(str(layout.home()), out)
        self.assertIn("not created yet", out)
        self.assertFalse(layout.db_path().exists())

    def test_flags_a_registered_project_whose_directory_is_missing(self) -> None:
        project = create_project(layout.db_path(), slug="vanished")
        with db.get_db(layout.db_path()) as conn:
            conn.execute("UPDATE projects SET repo_path = ? WHERE id = ?",
                         ("/nowhere/vanished", project["id"]))
        out = _run("workspace")
        self.assertIn("missing", out)

    def test_names_the_env_var_that_moves_it(self) -> None:
        self.assertIn("FACTORY_HOME", _run("workspace"))

    def test_unknown_subcommand_fails(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            _run("workspace", "frobnicate")
        self.assertEqual(raised.exception.code, 1)

    def test_listed_in_help(self) -> None:
        from factory.interfaces.cli.main import main

        buf = io.StringIO()
        with patch("factory.interfaces.render.output.console", Console(file=buf, width=200)):
            with patch("sys.argv", ["factory", "--help"]):
                with self.assertRaises(SystemExit):
                    main()
        self.assertIn("workspace import-legacy", buf.getvalue())
        self.assertTrue(Path(layout.home()).is_absolute())


if __name__ == "__main__":
    unittest.main()
