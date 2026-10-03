"""The coder sees the CURRENT contents of its task's in-scope files.

Without them a `modify` is a blind full-file rewrite (EFFECTIVENESS.md §6
promises "the files in scope").
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.domain.contracts import SpecOutput, TaskDef
from factory.domain.gates import MAX_SCOPE_FILE_CHARS
from factory.pipeline.prompts.blocks import scope_files_block
from factory.pipeline.prompts.coder import build_remediation_prompt
from factory.pipeline.prompts.context_pack import build_task_pack

HEADING = "## Current contents of files in scope (modify these; return the whole file)"


def _task(*scope: str) -> TaskDef:
    return TaskDef(id="T-1", title="t", purpose="p", scope=list(scope))


def _scope(*scope: str) -> list[str]:
    return list(scope)


class ScopeFilesBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = {"project_dir": str(self.root), "opencode_cwd": str(self.root)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_existing_file_text_appears(self) -> None:
        (self.root / "app").mkdir()
        (self.root / "app" / "main.py").write_text("def hello():\n    return 1\n")
        block = scope_files_block(self.state, _scope("app/main.py"))
        self.assertIn(HEADING, block)
        self.assertIn("### app/main.py", block)
        self.assertIn("def hello():\n    return 1\n", block)

    def test_missing_path_is_marked_new(self) -> None:
        block = scope_files_block(self.state, _scope("app/new.py"))
        self.assertIn("### app/new.py (new file)", block)

    def test_oversize_file_is_truncated_with_notice(self) -> None:
        (self.root / "big.py").write_text("x" * (MAX_SCOPE_FILE_CHARS + 500))
        block = scope_files_block(self.state, _scope("big.py"))
        self.assertIn("x" * MAX_SCOPE_FILE_CHARS, block)
        self.assertNotIn("x" * (MAX_SCOPE_FILE_CHARS + 1), block)
        self.assertIn("truncated", block)
        # The coder returns whole files: one it cannot see in full must not be rewritten.
        self.assertIn("do NOT return this file", block)

    def test_a_normal_sized_source_file_is_shown_whole(self) -> None:
        # Live (habits run #8): tests/test_app.py was 14,812 chars, shown to 12,000.
        self.assertGreaterEqual(MAX_SCOPE_FILE_CHARS, 40000)

    def test_evidence_paths_are_skipped(self) -> None:
        (self.root / "PROJECT_RULES.md").write_text("secret rules")
        (self.root / "docs" / "work").mkdir(parents=True)
        (self.root / "docs" / "work" / "BRIEF.md").write_text("brief")
        block = scope_files_block(self.state, _scope("PROJECT_RULES.md", "docs/work/BRIEF.md"))
        self.assertEqual(block, "")

    def test_path_escaping_the_project_is_not_read(self) -> None:
        block = scope_files_block(self.state, _scope("../../etc/passwd"))
        self.assertNotIn("root:", block)
        self.assertEqual(block, "")

    def test_no_scope_means_no_block(self) -> None:
        self.assertEqual(scope_files_block(self.state, _scope()), "")

    def test_block_lands_in_the_task_pack(self) -> None:
        spec = SpecOutput(title="t", problem="p", why="w", acceptance_criteria=["a"])
        pack = build_task_pack(_task("a.py"), spec, {}, scope_files_context="SCOPE-FILES\n\n")
        self.assertIn("SCOPE-FILES", pack)

    def test_repo_prefix_is_stripped(self) -> None:
        (self.root / "app.py").write_text("A = 1\n")
        block = scope_files_block(self.state, _scope("repo/app.py"))
        self.assertIn("### app.py\n", block)
        self.assertIn("A = 1\n", block)

    def test_directory_expands_to_its_code_files(self) -> None:
        (self.root / "app").mkdir()
        (self.root / "app" / "a.py").write_text("A = 1\n")
        (self.root / "app" / "b.py").write_text("B = 2\n")
        (self.root / "app" / "notes.txt").write_text("not code")
        block = scope_files_block(self.state, _scope("app/"))
        self.assertIn("### app/a.py", block)
        self.assertIn("### app/b.py", block)
        self.assertNotIn("not code", block)

    def test_directory_expansion_is_bounded_in_total(self) -> None:
        (self.root / "app").mkdir()
        for name in ("a.py", "b.py", "c.py"):
            (self.root / "app" / name).write_text("x" * (MAX_SCOPE_FILE_CHARS // 2 + 10))
        block = scope_files_block(self.state, _scope("app"))
        # The whole ENTRY shares one budget, not one per file under it.
        self.assertLessEqual(block.count("x"), MAX_SCOPE_FILE_CHARS)
        self.assertIn("truncated", block)


class RemediationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_remediation_prompt_carries_inventory_and_in_scope_files(self) -> None:
        (self.root / "app").mkdir()
        (self.root / "app" / "routes.py").write_text("def list_items():\n    return []\n")
        spec = SpecOutput(title="t", problem="p", why="w", acceptance_criteria=["a"],
                          tasks=[_task("app/routes.py"), _task("app/new.py")])
        state = {"opencode_cwd": str(self.root), "architect": {},
                 "prior_findings": ["routes return nothing"]}
        prompt = build_remediation_prompt(state, spec, "")
        self.assertIn("## Existing codebase (interfaces only", prompt)
        self.assertIn(HEADING, prompt)
        self.assertIn("def list_items():\n    return []\n", prompt)
        self.assertIn("### app/new.py (new file)", prompt)


if __name__ == "__main__":
    unittest.main()
