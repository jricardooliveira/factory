"""The product brief is rendered from recorded answers only — and never hides an assumption."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.evidence.brief import (
    BRIEF_RELPATH,
    TRANSCRIPT_RELPATH,
    brief_path,
    load_brief,
    render_brief,
    render_transcript,
    write_brief,
)


def _a(topic: str, question: str, answer: str, *, options=(), assumed=False) -> dict:
    return {"topic": topic, "question": question, "answer": answer,
            "options": list(options), "assumed": assumed}


ANSWERS = [
    _a("users", "Who uses it?", "Clerks", options=["Clerks", "Admins"], assumed=True),
    _a("stack", "Which stack?", "Python CLI"),
    _a("goal", "Why build it?", "Track stock"),
    _a("pricing", "How is it priced?", "Free"),
    _a("goal", "What changes for them?", "No more spreadsheets"),
]


class RenderBriefTests(unittest.TestCase):
    def test_sections_follow_the_checklist_order_then_the_rest(self) -> None:
        brief = render_brief("Stockroom", ANSWERS)
        self.assertTrue(brief.startswith("# Product brief — Stockroom\n"))
        order = [brief.index(h) for h in (
            "## Goal", "## Users", "## Tech stack", "## Other",
            "## Assumptions (NOT decided by the operator)",
        )]
        self.assertEqual(order, sorted(order))
        self.assertIn("- **Why build it?** Track stock\n- **What changes for them?** "
                      "No more spreadsheets", brief)
        self.assertIn("- **How is it priced?** Free", brief)
        self.assertNotIn("## Data", brief)  # no answers, no empty section

    def test_assumed_answers_are_listed_as_assumptions(self) -> None:
        brief = render_brief("Stockroom", ANSWERS)
        tail = brief.split("## Assumptions (NOT decided by the operator)")[1]
        self.assertIn("- **Who uses it?** Clerks", tail)
        self.assertNotIn("Track stock", tail)

    def test_no_assumptions_says_none(self) -> None:
        brief = render_brief("Stockroom", [_a("goal", "Why?", "Because")])
        self.assertTrue(brief.rstrip().endswith(
            "## Assumptions (NOT decided by the operator)\n\n- (none)"))

    def test_rendering_is_deterministic(self) -> None:
        self.assertEqual(render_brief("S", ANSWERS), render_brief("S", ANSWERS))


class RenderTranscriptTests(unittest.TestCase):
    def test_numbered_in_asked_order_with_options_and_assumed_marks(self) -> None:
        text = render_transcript("Stockroom", ANSWERS)
        self.assertIn("Stockroom", text.splitlines()[0])
        self.assertLess(text.index("1. "), text.index("2. "))
        self.assertLess(text.index("Who uses it?"), text.index("Which stack?"))
        self.assertIn("Clerks / Admins", text)
        self.assertEqual(text.count("(assumed"), 1)
        self.assertIn("5. ", text)


class WriteAndLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_brief_loads_as_empty(self) -> None:
        self.assertEqual(load_brief(self.project), "")
        self.assertEqual(load_brief(None), "")

    def test_write_creates_both_files_and_load_returns_the_brief(self) -> None:
        paths = write_brief(self.project, "Stockroom", ANSWERS)
        self.assertEqual(paths, [self.project / BRIEF_RELPATH, self.project / TRANSCRIPT_RELPATH])
        self.assertEqual(brief_path(self.project), self.project / "docs/work/BRIEF.md")
        self.assertEqual(load_brief(self.project), render_brief("Stockroom", ANSWERS))
        self.assertEqual((self.project / "docs/work/INTERVIEW.md").read_text(encoding="utf-8"),
                         render_transcript("Stockroom", ANSWERS))


if __name__ == "__main__":
    unittest.main()
