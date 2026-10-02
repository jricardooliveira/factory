"""`factory workspace import-legacy <old_dir>`: the one-off move into $FACTORY_HOME.

The old layout kept `old_dir/factory.db` beside `old_dir/projects/PROJ-*/`, and each
project was split in two: code in `repo/` (its own git repository) and its audit
trail (docs/work INTENT/SPEC/PLAN, ADRs, releases, PROJECT_RULES.md,
project-spec.json) OUTSIDE that repository. The import moves the DB and every
project into $FACTORY_HOME, merges each project's outside-repo evidence INTO its
repository (committing it with a ``factory:`` subject), and rewrites the
projects.repo_path / spec_path rows.

Exercised on a synthetic old layout only — never on real data.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.evidence import trust_package as tp
from factory.state import db
from factory.workspace import git, layout, legacy
from factory.workspace.projects import _package_root

OLD_RULES = (
    "# Alpha Project Rules\n\n"
    "- Project ID: PROJ-001\n"
    "- Slug: alpha\n"
    "- Source root: repo/\n"
    "- Pipeline artifacts: docs/pipeline/\n"
    "- Project tasks: docs/work/tasks/\n"
    "- Money is integer cents, never float.\n"
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(root), capture_output=True, text=True, check=False,
    ).stdout


def _write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_old_layout(old: Path) -> dict[str, object]:
    """old/factory.db + old/projects/PROJ-*/{repo/, docs/, PROJECT_RULES.md, project-spec.json}."""
    old.mkdir(parents=True, exist_ok=True)
    db_path = old / "factory.db"
    db.init_db(db_path)

    alpha = old / "projects" / "PROJ-001-alpha"
    repo = alpha / "repo"
    repo.mkdir(parents=True)
    git.git_init(repo)
    # The old symlink points at wherever the factory used to live.
    (repo / ".opencode").symlink_to("/nonexistent/old-factory/.opencode")
    _write(repo / "app.py", "def search():\n    return []\n")
    git.git_commit_all(repo, "factory: T-1 search")
    base = None  # the run's base: before any factory code (repo had no commits)

    _write(alpha / "docs" / "work" / "US-0001" / "INTENT.md", "# Intent\nsearch bookmarks\n")
    _write(alpha / "docs" / "work" / "US-0001" / "SPEC.md", "# Spec\n")
    _write(alpha / "docs" / "work" / "US-0001" / "PLAN.md", "# Plan\n")
    _write(alpha / "docs" / "architecture" / "adr" / "ADR-US-0001-search.md", "# ADR\n")
    _write(alpha / "docs" / "releases" / "run-1-trust-package.json", "{}\n")
    (alpha / "docs" / "pipeline").mkdir(parents=True)  # empty old scaffold
    (alpha / "docs" / "context").mkdir(parents=True)
    (alpha / "state").mkdir()
    _write(alpha / "PROJECT_RULES.md", OLD_RULES)
    _write(alpha / "project-spec.json", json.dumps({"name": "Alpha", "description": "a"}))
    _write(alpha / ".secrets" / "token", "s3cret\n")  # not evidence: must stay behind

    # beta: registered with an EXTERNAL spec and no git repo yet.
    beta = old / "projects" / "PROJ-002-beta"
    (beta / "repo").mkdir(parents=True)
    _write(beta / "docs" / "work" / "US-0002" / "INTENT.md", "# Intent beta\n")
    external_spec = _write(old / "specs" / "beta.json", json.dumps({"name": "Beta"}))

    # An unregistered folder: reported, never touched.
    _write(old / "projects" / "PROJ-009-orphan" / "repo" / "x.py")

    with db.get_db(db_path) as conn:
        for pid, slug, name, root, spec in (
            ("PROJ-001", "alpha", "Alpha", repo, alpha / "project-spec.json"),
            ("PROJ-002", "beta", "Beta", beta / "repo", external_spec),
        ):
            conn.execute(
                "INSERT INTO projects (id, slug, name, repo_path, spec_path, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', 'now', 'now')",
                (pid, slug, name, str(root), str(spec)),
            )
        db.create_story(conn, "US-0001", "Search", "search bookmarks", project_id="PROJ-001")
        run_id = db.start_run(conn, "US-0001", project_id="PROJ-001", base_commit=base)
        db.log_agent(conn, run_id, "spec-agent", "in", json.dumps({
            "title": "Search", "acceptance_criteria": [], "tasks": [
                {"id": "T-1", "title": "s", "purpose": "p", "scope": ["app.py"],
                 "completion_evidence": "e"}]}), verdict="pass")
        db.finish_run(conn, run_id, "completed")
    return {"db": db_path, "alpha": alpha, "beta": beta, "external_spec": external_spec,
            "run_id": run_id}


class LegacyImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.old = self.tmp / "old-factory"
        self.home = self.tmp / "home"
        self.fx = build_old_layout(self.old)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _import(self, **kw) -> legacy.LegacyImport:
        return legacy.import_legacy(self.old, home=self.home, **kw)

    # ── the move ─────────────────────────────────────────────────────

    def test_db_moves_into_home_and_rows_point_at_the_new_repos(self) -> None:
        self._import()
        self.assertFalse((self.old / "factory.db").exists())
        new_db = self.home / "factory.db"
        self.assertTrue(new_db.is_file())
        with db.get_db(new_db) as conn:
            rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM projects")}
            runs = conn.execute("SELECT COUNT(*) c FROM pipeline_runs").fetchone()["c"]
        alpha = self.home / "projects" / "alpha"
        self.assertEqual(rows["PROJ-001"]["repo_path"], str(alpha))
        self.assertEqual(rows["PROJ-001"]["spec_path"], str(alpha / "project-spec.json"))
        beta = self.home / "projects" / "beta"
        self.assertEqual(rows["PROJ-002"]["repo_path"], str(beta))
        # An external spec is COPIED into the repo — the product carries the spec it
        # was designed against — and the operator's original is left untouched.
        self.assertEqual(rows["PROJ-002"]["spec_path"], str(beta / "project-spec.json"))
        self.assertEqual(json.loads((beta / "project-spec.json").read_text())["name"], "Beta")
        self.assertIn("project-spec.json", _git(beta, "ls-files").split())
        self.assertTrue(Path(self.fx["external_spec"]).is_file())
        self.assertEqual(runs, 1, "run history must survive the move")

    def test_a_stale_external_spec_is_found_by_name_in_the_factory_examples(self) -> None:
        # The real legacy DB recorded <factory>/mvp/specs/supportflow.json; the
        # restructure moved that file to <factory>/examples/specs/.
        with db.get_db(self.fx["db"]) as conn:
            conn.execute("UPDATE projects SET spec_path = ? WHERE id = 'PROJ-002'",
                         ("/gone/mvp/specs/taskflow.json",))
        result = self._import()
        beta = self.home / "projects" / "beta"
        shipped = _package_root() / "examples" / "specs" / "taskflow.json"
        self.assertEqual((beta / "project-spec.json").read_bytes(), shipped.read_bytes())
        move = next(m for m in result.projects if m.project_id == "PROJ-002")
        self.assertEqual(move.spec_source, shipped)

    def test_an_unfindable_spec_keeps_its_row_and_is_reported(self) -> None:
        with db.get_db(self.fx["db"]) as conn:
            conn.execute("UPDATE projects SET spec_path = ? WHERE id = 'PROJ-002'",
                         ("/gone/nowhere/never-existed.json",))
        result = self._import()
        with db.get_db(self.home / "factory.db") as conn:
            row = conn.execute("SELECT spec_path FROM projects WHERE id = 'PROJ-002'").fetchone()
        self.assertEqual(row["spec_path"], "/gone/nowhere/never-existed.json")
        move = next(m for m in result.projects if m.project_id == "PROJ-002")
        self.assertTrue(any("never-existed.json" in n for n in move.notes), move.notes)

    def test_evidence_is_merged_into_the_repo_and_committed(self) -> None:
        self._import()
        repo = self.home / "projects" / "alpha"
        tracked = set(_git(repo, "ls-files").split())
        for rel in ("app.py", "PROJECT_RULES.md", "project-spec.json",
                    "docs/work/US-0001/INTENT.md", "docs/work/US-0001/SPEC.md",
                    "docs/work/US-0001/PLAN.md", "docs/architecture/adr/ADR-US-0001-search.md",
                    "docs/releases/run-1-trust-package.json"):
            self.assertIn(rel, tracked)
        self.assertEqual(git.git_changed_paths(repo), [], "import left the repo dirty")
        subjects = _git(repo, "log", "--format=%s").splitlines()
        self.assertTrue(subjects[0].startswith("factory: import legacy evidence"), subjects)
        self.assertIn("factory: T-1 search", subjects, "the product's history must survive")
        self.assertFalse((repo / "repo").exists())

    def test_a_repo_without_git_becomes_one(self) -> None:
        self._import()
        beta = self.home / "projects" / "beta"
        self.assertTrue((beta / ".git").exists())
        self.assertIn("docs/work/US-0002/INTENT.md", _git(beta, "ls-files").split())

    def test_project_rules_stop_telling_agents_the_source_root_is_repo(self) -> None:
        self._import()
        rules = (self.home / "projects" / "alpha" / "PROJECT_RULES.md").read_text()
        self.assertNotIn("repo/", rules)
        self.assertNotIn("docs/pipeline/", rules)
        self.assertIn("- Money is integer cents, never float.", rules, "operator text kept")
        self.assertIn("- Project ID: PROJ-001", rules)

    def test_opencode_link_is_repointed_at_this_factory(self) -> None:
        self._import()
        link = self.home / "projects" / "alpha" / ".opencode"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (_package_root() / ".opencode").resolve())

    def test_non_evidence_leftovers_and_unregistered_folders_stay_put(self) -> None:
        result = self._import()
        self.assertTrue((self.fx["alpha"] / ".secrets" / "token").is_file())
        self.assertFalse((self.home / "projects" / "alpha" / ".secrets").exists())
        self.assertTrue((self.old / "projects" / "PROJ-009-orphan" / "repo" / "x.py").is_file())
        self.assertIn("PROJ-009-orphan", " ".join(str(p) for p in result.unregistered))
        alpha_move = next(m for m in result.projects if m.project_id == "PROJ-001")
        self.assertIn(".secrets", alpha_move.left_behind)
        # Empty old scaffolding is simply dropped, and beta's emptied folder removed.
        self.assertFalse((self.fx["alpha"] / "state").exists())
        self.assertFalse(self.fx["beta"].exists())

    def test_an_emptied_old_projects_folder_is_removed(self) -> None:
        import shutil

        shutil.rmtree(self.old / "projects" / "PROJ-009-orphan")
        shutil.rmtree(self.fx["alpha"] / ".secrets")
        self._import()
        self.assertFalse((self.old / "projects").exists())

    def test_the_old_runs_trust_package_still_measures_only_code(self) -> None:
        self._import()
        pkg = tp.assemble(self.home / "factory.db", self.fx["run_id"])
        self.assertEqual(pkg["diff"]["source"], "git")
        self.assertEqual(pkg["diff"]["files"], [{"path": "app.py", "change": "added"}])
        self.assertEqual(pkg["diff"]["scope_violations"], [])
        self.assertTrue(pkg["adr"]["path"].endswith("ADR-US-0001-search.md"))

    def test_it_lands_in_factory_home_by_default(self) -> None:
        legacy.import_legacy(self.old)
        self.assertTrue(layout.db_path().is_file())
        self.assertTrue((layout.projects_dir() / "alpha" / ".git").exists())

    # ── dry run + refusals: nothing moves ────────────────────────────

    def test_dry_run_plans_without_moving_anything(self) -> None:
        result = self._import(dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.problems, [])
        self.assertEqual({m.slug for m in result.projects}, {"alpha", "beta"})
        alpha = next(m for m in result.projects if m.slug == "alpha")
        self.assertEqual(alpha.target, self.home / "projects" / "alpha")
        self.assertIn("docs/work/US-0001/SPEC.md", alpha.evidence)
        self.assertIn("PROJECT_RULES.md", alpha.evidence)
        self.assertTrue((self.old / "factory.db").is_file())
        self.assertTrue((self.fx["alpha"] / "repo" / "app.py").is_file())
        self.assertFalse(self.home.exists() and any(self.home.iterdir()))

    def test_refuses_to_merge_into_a_home_that_already_has_history(self) -> None:
        db.init_db(self.home / "factory.db")
        with db.get_db(self.home / "factory.db") as conn:
            db.create_story(conn, "US-0001", "x", "y")
        with self.assertRaisesRegex(legacy.LegacyImportError, "already has"):
            self._import()
        self.assertTrue((self.old / "factory.db").is_file())
        self.assertTrue((self.fx["alpha"] / "repo" / "app.py").is_file())

    def test_an_empty_home_db_is_not_history(self) -> None:
        db.init_db(self.home / "factory.db")  # e.g. someone ran `factory list` first
        self._import()
        self.assertFalse((self.old / "factory.db").exists())

    def test_refuses_when_a_target_project_dir_is_taken(self) -> None:
        _write(self.home / "projects" / "alpha" / "keep.txt", "mine")
        with self.assertRaisesRegex(legacy.LegacyImportError, "alpha"):
            self._import()
        self.assertTrue((self.old / "factory.db").is_file())
        self.assertEqual((self.home / "projects" / "alpha" / "keep.txt").read_text(), "mine")

    def test_refuses_conflicting_evidence_already_inside_the_repo(self) -> None:
        repo = self.fx["alpha"] / "repo"
        _write(repo / "PROJECT_RULES.md", "different rules\n")
        git.git_commit_all(repo, "factory: rules in repo")
        with self.assertRaisesRegex(legacy.LegacyImportError, "PROJECT_RULES.md"):
            self._import()
        self.assertTrue((self.fx["alpha"] / "PROJECT_RULES.md").is_file())

    def test_refuses_an_old_dir_without_a_database(self) -> None:
        with self.assertRaisesRegex(legacy.LegacyImportError, "factory.db"):
            legacy.import_legacy(self.tmp / "nowhere", home=self.home)

    def test_refuses_importing_home_into_itself(self) -> None:
        with self.assertRaisesRegex(legacy.LegacyImportError, "same"):
            legacy.import_legacy(self.old, home=self.old)

    def test_resolves_projects_whose_db_paths_predate_a_directory_move(self) -> None:
        # The DB recorded .../mvp/projects/PROJ-001-alpha/repo, but the folder has
        # since moved: the project is still found by its folder name under old_dir.
        with db.get_db(self.fx["db"]) as conn:
            conn.execute(
                "UPDATE projects SET repo_path = ?, spec_path = ? WHERE id = 'PROJ-001'",
                ("/gone/mvp/projects/PROJ-001-alpha/repo",
                 "/gone/mvp/projects/PROJ-001-alpha/project-spec.json"),
            )
        self._import()
        with db.get_db(self.home / "factory.db") as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = 'PROJ-001'").fetchone()
        alpha = self.home / "projects" / "alpha"
        self.assertEqual(row["repo_path"], str(alpha))
        self.assertEqual(row["spec_path"], str(alpha / "project-spec.json"))


class LegacyImportCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.old = Path(self._tmp.name) / "old"
        build_old_layout(self.old)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _main(self, *argv: str) -> None:
        from unittest.mock import patch

        from factory.interfaces.cli.main import main

        with patch("sys.argv", ["factory", *argv]):
            main()

    def test_import_legacy_command_moves_into_factory_home(self) -> None:
        self._main("workspace", "import-legacy", str(self.old))
        self.assertTrue(layout.db_path().is_file())
        self.assertTrue((layout.projects_dir() / "alpha" / "app.py").is_file())

    def test_import_legacy_dry_run_changes_nothing(self) -> None:
        self._main("workspace", "import-legacy", str(self.old), "--dry-run")
        self.assertTrue((self.old / "factory.db").is_file())
        self.assertFalse(layout.db_path().exists())

    def test_import_legacy_refusal_exits_non_zero(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self._main("workspace", "import-legacy", str(self.old / "missing"))
        self.assertEqual(raised.exception.code, 1)

    def test_import_legacy_requires_a_directory(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self._main("workspace", "import-legacy")
        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
