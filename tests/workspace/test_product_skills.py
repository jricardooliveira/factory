"""Every product gets the anti-slop practice: skills in .claude/skills + code-discipline rules.

The rules reach the agents (PROJECT_RULES.md heads every prompt's project memory);
the skills serve whoever works on the product in Claude Code.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.state import db
from factory.workspace.projects import (
    PRODUCT_SKILLS,
    RULES_FILENAME,
    create_project,
    refresh_project,
)
from factory.workspace.repo_map import build_repo_inventory


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout


class ProductSkillsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)

    def test_the_recommended_set(self) -> None:
        self.assertEqual(set(PRODUCT_SKILLS), {
            "ponytail", "ponytail-review", "karpathy-guidelines", "test-driven-development",
            "systematic-debugging", "verification-before-completion"})

    def test_a_new_project_ships_the_skills_and_the_rules_in_its_scaffold_commit(self) -> None:
        repo = Path(create_project(self.db_path, home=self.home, slug="habits")["repo_path"])
        for name in PRODUCT_SKILLS:
            self.assertTrue((repo / ".claude/skills" / name / "SKILL.md").is_file(), name)
        self.assertTrue((repo / ".claude/skills/LICENSES.md").is_file())
        rules = (repo / RULES_FILENAME).read_text()
        self.assertIn("## Code discipline", rules)
        committed = _git(repo, "show", "--name-only", "--format=", "HEAD")
        self.assertIn(".claude/skills/ponytail/SKILL.md", committed)
        self.assertEqual(_git(repo, "status", "--porcelain").strip(), "")

    def test_the_skills_never_show_up_as_product_code(self) -> None:
        repo = Path(create_project(self.db_path, home=self.home, slug="habits")["repo_path"])
        self.assertNotIn("SKILL.md", build_repo_inventory(repo))

    def test_refresh_brings_an_older_project_up_to_date_once(self) -> None:
        repo = Path(create_project(self.db_path, home=self.home, slug="habits")["repo_path"])
        # An older project: no skills, rules without the discipline section.
        subprocess.run(["git", "rm", "-rq", ".claude"], cwd=repo, check=True)
        (repo / RULES_FILENAME).write_text("# Habits Project Rules\n\n- Slug: habits\n")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "older")

        changed = refresh_project(self.db_path, "habits")
        self.assertTrue(changed)
        self.assertTrue((repo / ".claude/skills/ponytail/SKILL.md").is_file())
        rules = (repo / RULES_FILENAME).read_text()
        self.assertTrue(rules.startswith("# Habits Project Rules"))
        self.assertEqual(rules.count("## Code discipline"), 1)
        self.assertTrue(_git(repo, "log", "-1", "--format=%s").startswith("factory: "))

        head = _git(repo, "rev-parse", "HEAD")
        self.assertFalse(refresh_project(self.db_path, "habits"))
        self.assertEqual(_git(repo, "rev-parse", "HEAD"), head)


if __name__ == "__main__":
    unittest.main()
