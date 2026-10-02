"""Tests for decision memory: ADR persistence + memory loading."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.evidence.adr import adr_dir_for, load_project_memory, render_adr, write_adr
from factory.domain.contracts import ArchitectOutput


def _arch(**kw) -> ArchitectOutput:
    base = dict(
        verdict="pass",
        architecture_notes="Single pure module.",
        modules_affected=["convert.py"],
        implementation_constraints=["Pure function"],
        risks=["none material"],
    )
    base.update(kw)
    return ArchitectOutput(**base)


class AdrPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_adr_creates_named_file_with_decision(self) -> None:
        path = write_adr(self.project, "US-0001", "Celsius converter", _arch())
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, adr_dir_for(self.project))
        self.assertEqual(path.name, "ADR-US-0001-celsius-converter.md")
        body = path.read_text()
        self.assertIn("# ADR-US-0001: Celsius converter", body)
        self.assertIn("Single pure module.", body)
        self.assertIn("pending architecture sign-off", body)

    def test_write_adr_is_deterministic_and_replay_safe(self) -> None:
        p1 = write_adr(self.project, "US-0001", "Celsius converter", _arch())
        p2 = write_adr(self.project, "US-0001", "Celsius converter", _arch())
        self.assertEqual(p1, p2)  # overwrites, does not pile up duplicates
        self.assertEqual(len(list(adr_dir_for(self.project).glob("ADR-*.md"))), 1)

    def test_render_adr_lists_breaking_changes_and_sensitivity(self) -> None:
        text = render_adr(
            "US-0002",
            "API reshape",
            _arch(breaking_changes=["GET /items now returns an object"], sensitivity=["security"]),
        )
        self.assertIn("GET /items now returns an object", text)
        self.assertIn("security", text)


class LoadMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_project_returns_empty(self) -> None:
        self.assertEqual(load_project_memory(self.project), "")

    def test_includes_rules_and_prior_adrs(self) -> None:
        (self.project / "PROJECT_RULES.md").write_text("- Always use stdlib only")
        write_adr(self.project, "US-0001", "First decision", _arch())
        mem = load_project_memory(self.project)
        self.assertIn("Project Rules", mem)
        self.assertIn("Always use stdlib only", mem)
        self.assertIn("Prior Architecture Decisions", mem)
        self.assertIn("ADR-US-0001", mem)

    def test_excludes_current_story_adr(self) -> None:
        write_adr(self.project, "US-0001", "Prior", _arch())
        write_adr(self.project, "US-0002", "Current", _arch())
        mem = load_project_memory(self.project, exclude_story="US-0002")
        self.assertIn("ADR-US-0001", mem)
        self.assertNotIn("ADR-US-0002: Current", mem)


if __name__ == "__main__":
    unittest.main()
