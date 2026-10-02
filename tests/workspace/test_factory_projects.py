"""Tests for factory-level project registry behavior.

A project directory IS its git repository ($FACTORY_HOME/projects/<slug>/): code at
its root, the factory's evidence under docs/, PROJECT_RULES.md and
project-spec.json beside the code — one audit trail, in one repository.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from factory.runs import run_project_pipeline
from factory.state.db import get_db, init_db
from factory.workspace import git, layout
from factory.workspace.projects import create_project, get_project, list_projects


def _git_out(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
    ).stdout


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
            home=self.root,
            slug="My App",
            name="My Application",
        )

        self.assertEqual(project["id"], "PROJ-001")
        self.assertEqual(project["slug"], "my-app")
        self.assertEqual(project["name"], "My Application")
        self.assertEqual(project["status"], "active")

        # A project directory IS its git repository: no repo/ split.
        project_dir = self.root / "projects" / "my-app"
        self.assertEqual(Path(project["repo_path"]), project_dir)
        self.assertTrue((project_dir / ".git").exists())
        self.assertFalse((project_dir / "repo").exists())
        self.assertTrue((project_dir / "PROJECT_RULES.md").exists())

        persisted = get_project(self.db_path, "PROJ-001")
        self.assertEqual(persisted, project)

    def test_project_rules_and_spec_are_committed_by_the_factory(self) -> None:
        project = create_project(self.db_path, home=self.root, slug="orders-api", stack="fastapi")
        repo = Path(project["repo_path"])
        tracked = _git_out(repo, "ls-files").split()
        self.assertIn("PROJECT_RULES.md", tracked)
        self.assertIn("project-spec.json", tracked)
        # The infra symlink is never part of the product.
        self.assertNotIn(".opencode", tracked)
        self.assertEqual(git.git_changed_paths(repo), [], "scaffold left the repo dirty")
        subject = _git_out(repo, "log", "-1", "--format=%s").strip()
        self.assertTrue(subject.startswith("factory:"), subject)

    def test_project_rules_name_the_repo_root_as_the_source_root(self) -> None:
        project = create_project(self.db_path, home=self.root, slug="rules")
        rules = (Path(project["repo_path"]) / "PROJECT_RULES.md").read_text(encoding="utf-8")
        # Agents read PROJECT_RULES.md; "Source root: repo/" is what taught them to
        # prefix every path with repo/.
        self.assertNotIn("repo/", rules)
        for owned in layout.EVIDENCE_PATHS:
            self.assertIn(owned, rules)

    def test_scaffold_evidence_never_shows_up_in_the_code_diff(self) -> None:
        project = create_project(self.db_path, home=self.root, slug="baseline", stack="fastapi")
        repo = Path(project["repo_path"])
        (repo / "app.py").write_text("X = 1\n")
        git.git_commit_all(repo, "factory: T-1 app")
        diff = git.collect_repo_diff(repo, exclude=layout.EVIDENCE_PATHS) or ""
        self.assertIn("app.py", diff)
        self.assertNotIn("PROJECT_RULES.md", diff)
        self.assertNotIn("project-spec.json", diff)

    def test_create_project_with_explicit_spec_copies_it_into_the_repo(self) -> None:
        external = self.root / "elsewhere.json"
        external.write_text(json.dumps({"name": "Ext", "description": "d"}))
        project = create_project(self.db_path, home=self.root, slug="ext", spec_path=external)
        inside = Path(project["repo_path"]) / "project-spec.json"
        self.assertEqual(project["spec_path"], str(inside))
        self.assertEqual(json.loads(inside.read_text())["name"], "Ext")
        self.assertIn("project-spec.json", _git_out(inside.parent, "ls-files").split())

    def test_create_project_refuses_an_existing_non_empty_directory(self) -> None:
        taken = self.root / "projects" / "taken"
        taken.mkdir(parents=True)
        (taken / "stuff.txt").write_text("mine")
        with self.assertRaisesRegex(ValueError, "already exists"):
            create_project(self.db_path, home=self.root, slug="taken")
        self.assertEqual(list_projects(self.db_path), [])
        self.assertEqual((taken / "stuff.txt").read_text(), "mine")

    def test_duplicate_slug_is_refused_before_touching_disk(self) -> None:
        create_project(self.db_path, home=self.root, slug="dup")
        with self.assertRaisesRegex(ValueError, "already exists"):
            create_project(self.db_path, home=self.root, slug="dup")

    def test_home_defaults_to_factory_home(self) -> None:
        project = create_project(self.db_path, slug="defaulted")
        self.assertEqual(Path(project["repo_path"]), layout.projects_dir() / "defaulted")

    def test_create_project_with_stack_writes_default_project_spec(self) -> None:
        project = create_project(
            self.db_path,
            home=self.root,
            slug="orders-api",
            name="Orders API",
            stack="fastapi",
        )

        spec_path = self.root / "projects" / "orders-api" / "project-spec.json"
        data = json.loads(spec_path.read_text())
        self.assertEqual(data["name"], "Orders API")
        self.assertEqual(data["framework"], "FastAPI")
        self.assertEqual(project["spec_path"], str(spec_path))

    def test_create_project_allocates_incrementing_ids_and_lookup_accepts_slug(self) -> None:
        create_project(self.db_path, home=self.root, slug="first", name="First")
        second = create_project(self.db_path, home=self.root, slug="second", name="Second")

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
            home=self.root,
            slug="spec-app",
            name="Spec App",
            spec_path=spec_path,
        )

        with patch("factory.runs.service.run_pipeline") as run_pipeline:
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

        self.assertEqual(Path(row["spec_path"]).resolve(), spec_path.resolve())
        self.assertEqual(json.loads(spec_path.read_text())["framework"], "FastAPI")

    def test_run_command_dispatches_to_project_pipeline(self) -> None:
        from factory.interfaces.cli.main import main

        with patch("factory.runs.run_project_pipeline") as run_project_pipeline:
            with patch(
                "sys.argv",
                ["factory", "run", "--project", "PROJ-001", "Add a health endpoint"],
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
