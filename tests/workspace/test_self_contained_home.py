"""A factory home is self-contained: copy or move it and it governs ITS products.

Found by review: `projects.repo_path` / `spec_path` were stored as absolute
paths. With FACTORY_HOME pointed at a copy of a home, reads came from the copy's
DB but every run, replay and resume wrote into the ORIGINAL product repositories —
backing up or test-copying a home silently mutated the live products.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory.state import db
from factory.workspace import layout
from factory.workspace.projects import create_project, get_project, list_projects


class SelfContainedHomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.home = self.root / "home"
        self.db = self.home / "factory.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _stored(self, db_path: Path, slug: str) -> tuple[str, str | None]:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute(
                "SELECT repo_path, spec_path FROM projects WHERE slug = ?", (slug,)
            ).fetchone()
        finally:
            conn.close()

    def test_locations_inside_the_home_are_stored_relative_to_it(self) -> None:
        create_project(self.db, home=self.home, slug="bookmarks", stack="fastapi")
        repo, spec = self._stored(self.db, "bookmarks")
        self.assertEqual(repo, "projects/bookmarks")
        self.assertEqual(spec, "projects/bookmarks/project-spec.json")

    def test_reads_resolve_to_absolute_paths_in_this_home(self) -> None:
        create_project(self.db, home=self.home, slug="bookmarks", stack="fastapi")
        project = get_project(self.db, "bookmarks")
        self.assertEqual(Path(project["repo_path"]), self.home / "projects" / "bookmarks")
        self.assertEqual(
            Path(project["spec_path"]), self.home / "projects" / "bookmarks" / "project-spec.json"
        )
        self.assertEqual(Path(list_projects(self.db)[0]["repo_path"]), self.home / "projects" / "bookmarks")

    def test_a_copied_home_governs_its_own_products(self) -> None:
        create_project(self.db, home=self.home, slug="bookmarks")
        copy = self.root / "copy"
        shutil.copytree(self.home, copy, symlinks=True)
        project = get_project(copy / "factory.db", "bookmarks")
        self.assertEqual(Path(project["repo_path"]), copy / "projects" / "bookmarks")

    def test_an_absolute_row_from_before_the_change_follows_a_copied_home(self) -> None:
        """Rows written before relative storage hold the ORIGINAL home's absolute path."""
        create_project(self.db, home=self.home, slug="bookmarks")
        with db.get_db(self.db) as conn:
            conn.execute(
                "UPDATE projects SET repo_path = ? WHERE slug = 'bookmarks'",
                (str(self.home / "projects" / "bookmarks"),),
            )
        copy = self.root / "copy"
        shutil.copytree(self.home, copy, symlinks=True)
        project = get_project(copy / "factory.db", "bookmarks")
        self.assertEqual(Path(project["repo_path"]), copy / "projects" / "bookmarks")
        # ...and the original home still resolves to its own copy.
        self.assertEqual(
            Path(get_project(self.db, "bookmarks")["repo_path"]), self.home / "projects" / "bookmarks"
        )

    def test_a_path_outside_the_home_is_kept_absolute(self) -> None:
        elsewhere = self.root / "elsewhere" / "repo"
        self.assertEqual(layout.store_location(elsewhere, self.db), str(elsewhere))
        self.assertEqual(layout.resolve_location(str(elsewhere), self.db), elsewhere)


if __name__ == "__main__":
    unittest.main()
