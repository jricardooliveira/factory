"""gate-release + Checkpoint 3: the release gate ASKS, it never allows.

EFFECTIVENESS §2 parks the line at three checkpoints; until now a run that passed
gate-test marked ITSELF completed — the factory approving its own work. The
gate's verdict is whether the evidence bar is met (`passed` = ready); either way
it parks for the operator, with every gap named, because no agent may pass the
release gate.
"""

from __future__ import annotations

import unittest

from factory.domain.contracts import ArchitectOutput, ReleaseOutput
from factory.domain.gates import gate_after_release

NOTES = ReleaseOutput(summary="Adds search.", changes=["GET /search"],
                      how_to_verify=["curl /search?q=x"])
PLAIN = ArchitectOutput(architecture_notes="n", modules_affected=["src/"])
SCHEMA = ArchitectOutput(architecture_notes="n", modules_affected=["src/"],
                         db_impact="yes — adds a tickets table", migration_needed="yes")


class GateAfterReleaseTests(unittest.TestCase):
    def test_a_green_release_is_ready_but_still_parks(self) -> None:
        result = gate_after_release([], NOTES, PLAIN)
        self.assertTrue(result.passed)
        self.assertTrue(result.needs_human)  # no agent passes the release gate
        self.assertEqual(result.gate, "gate-release")
        self.assertIn("READY", result.reason)

    def test_evidence_gaps_are_named_and_the_release_is_not_ready(self) -> None:
        result = gate_after_release(["Tests were never executed"], NOTES, PLAIN)
        self.assertFalse(result.passed)
        self.assertTrue(result.needs_human)  # the operator decides, gaps in view
        self.assertIn("Tests were never executed", "\n".join(result.human_questions))

    def test_missing_release_notes_are_a_gap(self) -> None:
        result = gate_after_release([], None, PLAIN)
        self.assertFalse(result.passed)
        self.assertIn("release notes", "\n".join(result.human_questions).lower())

    def test_a_schema_change_needs_migration_and_rollback_notes(self) -> None:
        result = gate_after_release([], NOTES, SCHEMA)
        text = "\n".join(result.human_questions).lower()
        self.assertFalse(result.passed)
        self.assertIn("migration", text)
        self.assertIn("rollback", text)

    def test_none_written_as_a_sentence_is_still_no_migration_notes(self) -> None:
        # Observed live: the release-agent wrote "None." (with a full stop).
        notes = NOTES.model_copy(update={"migration_notes": "None.",
                                         "rollback_notes": "migrate down 1"})
        self.assertFalse(gate_after_release([], notes, SCHEMA).passed)

    def test_a_documented_schema_change_is_ready(self) -> None:
        notes = NOTES.model_copy(update={"migration_notes": "run migrate up",
                                         "rollback_notes": "migrate down 1"})
        self.assertTrue(gate_after_release([], notes, SCHEMA).passed)

    def test_a_breaking_change_needs_rollback_notes(self) -> None:
        arch = PLAIN.model_copy(update={"breaking_changes": ["renames /tickets"]})
        self.assertFalse(gate_after_release([], NOTES, arch).passed)

    def test_the_release_agents_blocking_concern_is_a_gap(self) -> None:
        notes = NOTES.model_copy(update={"verdict": "fail",
                                         "concerns": ["the diff never touches AC 2"]})
        result = gate_after_release([], notes, PLAIN)
        self.assertFalse(result.passed)
        self.assertIn("the diff never touches AC 2", "\n".join(result.human_questions))

    def test_a_warning_concern_is_shown_but_does_not_block(self) -> None:
        notes = NOTES.model_copy(update={"verdict": "warn", "concerns": ["slow on 10k rows"]})
        result = gate_after_release([], notes, PLAIN)
        self.assertTrue(result.passed)
        self.assertIn("slow on 10k rows", "\n".join(result.human_questions))

    def test_the_question_says_what_approve_and_reject_do(self) -> None:
        text = "\n".join(gate_after_release(["gap"], NOTES, PLAIN).human_questions)
        self.assertIn("Approve", text)
        self.assertIn("reject", text.lower())


if __name__ == "__main__":
    unittest.main()
