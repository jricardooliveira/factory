"""$FACTORY_HOME: ONE place for the factory's state, never the working directory.

The DB path used to be `Path("factory.db")`, resolved against whatever directory
`factory` happened to be run from — four stray factory.db files accumulated on one
machine, each holding a slice of the run history. Products lived in a `projects/`
folder beside it, split in two (code in `repo/`, evidence outside it).

Now: one resolver. `$FACTORY_HOME` (default `~/.factory`) holds `factory.db` and
`projects/<slug>/`, and every reader and writer asks the resolver.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import workspace
from factory.workspace import layout

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "factory"


class HomeResolverTests(unittest.TestCase):
    def test_factory_home_env_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"FACTORY_HOME": tmp}):
                self.assertEqual(layout.home(), Path(tmp).resolve())
                self.assertEqual(layout.db_path(), Path(tmp).resolve() / "factory.db")
                self.assertEqual(layout.projects_dir(), Path(tmp).resolve() / "projects")

    def test_default_is_dot_factory_in_the_user_home(self) -> None:
        with tempfile.TemporaryDirectory() as fake_user_home:
            env = {k: v for k, v in os.environ.items() if k != "FACTORY_HOME"}
            env["HOME"] = fake_user_home
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(layout.home(), Path(fake_user_home).resolve() / ".factory")

    def test_blank_factory_home_falls_back_to_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as fake_user_home:
            with patch.dict(os.environ, {"FACTORY_HOME": "  ", "HOME": fake_user_home}):
                self.assertEqual(layout.home(), Path(fake_user_home).resolve() / ".factory")

    def test_tilde_is_expanded_and_relative_paths_are_made_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as fake_user_home:
            with patch.dict(os.environ, {"FACTORY_HOME": "~/fh", "HOME": fake_user_home}):
                self.assertEqual(layout.home(), Path(fake_user_home).resolve() / "fh")
            with patch.dict(os.environ, {"FACTORY_HOME": "rel/home"}):
                self.assertTrue(layout.home().is_absolute())

    def test_the_resolver_is_reexported_from_the_workspace_package(self) -> None:
        self.assertIs(workspace.home, layout.home)
        self.assertIs(workspace.db_path, layout.db_path)
        self.assertIs(workspace.projects_dir, layout.projects_dir)

    def test_project_dir_is_home_projects_slug(self) -> None:
        self.assertEqual(layout.project_dir_for("bookmarks"), layout.projects_dir() / "bookmarks")


class TestsNeverTouchTheRealHomeTests(unittest.TestCase):
    def test_conftest_isolates_factory_home(self) -> None:
        real = Path.home() / ".factory"
        self.assertNotEqual(layout.home(), real.resolve())
        self.assertIn("FACTORY_HOME", os.environ)


class NoCwdRelativeDbTests(unittest.TestCase):
    """No module may fall back to a working-directory-relative DB."""

    def test_no_source_file_names_a_bare_factory_db_path(self) -> None:
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"""Path\(\s*["']factory\.db["']\s*\)""", text):
                offenders.append(str(path.relative_to(PACKAGE)))
        self.assertEqual(offenders, [])

    def test_state_db_has_no_default_path(self) -> None:
        import inspect

        from factory.state import db

        self.assertFalse(hasattr(db, "DEFAULT_DB_PATH"))
        for fn in (db.get_db, db.init_db):
            param = inspect.signature(fn).parameters["path"]
            self.assertIs(param.default, inspect.Parameter.empty, fn.__name__)

    def test_get_db_creates_the_home_directory_on_first_use(self) -> None:
        from factory.state import db

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "fresh-home" / "factory.db"
            db.init_db(target)
            self.assertTrue(target.is_file())

    def test_cli_commands_use_factory_home_whatever_the_cwd(self) -> None:
        from factory.interfaces.cli.main import main

        with tempfile.TemporaryDirectory() as cwd:
            old = Path.cwd()
            os.chdir(cwd)
            try:
                with patch("sys.argv", ["factory", "list"]):
                    main()
            finally:
                os.chdir(old)
            self.assertFalse((Path(cwd) / "factory.db").exists())
        self.assertTrue(layout.db_path().is_file())


if __name__ == "__main__":
    unittest.main()
