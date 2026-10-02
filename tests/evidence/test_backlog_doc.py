"""docs/work/BACKLOG.md: the ordered story list with each story's status."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.evidence.backlog import BACKLOG_RELPATH, render_backlog, write_backlog

ROWS = [
    {"position": 1, "title": "Skeleton", "request": "Build it.", "rationale": "first",
     "status": "started", "story_id": "STORY-001"},
    {"position": 2, "title": "Login", "request": "Let users sign in.", "rationale": "",
     "status": "approved", "story_id": None},
]


class BacklogDocTests(unittest.TestCase):
    def test_render_lists_stories_in_order_with_status(self) -> None:
        text = render_backlog("Shop", ROWS)
        self.assertTrue(text.startswith("# Backlog — Shop\n"))
        self.assertIn("1. **Skeleton** — started (STORY-001)", text)
        self.assertIn("2. **Login** — approved", text)
        self.assertIn("Let users sign in.", text)
        self.assertIn("Why: first", text)
        self.assertLess(text.index("Skeleton"), text.index("Login"))

    def test_write_puts_it_under_docs_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_backlog(Path(tmp), "Shop", ROWS)
            self.assertEqual(path, Path(tmp) / BACKLOG_RELPATH)
            self.assertEqual(BACKLOG_RELPATH, "docs/work/BACKLOG.md")
            self.assertEqual(path.read_text(), render_backlog("Shop", ROWS))
