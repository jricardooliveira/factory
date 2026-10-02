"""Release readiness must not survive broken evidence (independent assessment, F1).

"A persistence or validation failure should prevent readiness and explain why":
a trust package that does not match its schema, or that could not be saved
where the operator reads it, is a named gap at Checkpoint 3 — never READY.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.pipeline.evidence_writers import release_evidence_gaps
from factory.pipeline.nodes.gates import node_gate_release
from factory.state import db

NOTES = {"verdict": "pass", "summary": "Adds search.", "rollback_notes": "revert"}
ARCH = {"architecture_notes": "n", "modules_affected": ["src/"]}


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "S", "req")
            self.run_id = db.start_run(conn, "US-0001")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, **extra) -> dict:
        state = {"run_id": self.run_id, "db_path": str(self.db_path), "story_id": "US-0001",
                 "release": NOTES, "architect": ARCH}
        state.update(extra)
        return state

    def test_a_malformed_package_is_a_gap(self) -> None:
        bad = {"verdict": "shipped", "blockers": []}
        with patch("factory.evidence.trust_package.assemble", return_value=bad):
            gaps = release_evidence_gaps(self._state())
        self.assertTrue(any("schema" in g and "verdict" in g for g in gaps), gaps)

    def test_a_package_that_could_not_be_saved_is_a_gap(self) -> None:
        clean = {"blockers": []}
        with patch("factory.pipeline.nodes.gates.release_evidence_gaps", return_value=[]), \
                patch("factory.pipeline.nodes.gates.write_trust_package", return_value=None), \
                patch("factory.evidence.trust_package.assemble", return_value=clean):
            out = node_gate_release(self._state(project_dir=str(self.root / "repo")))
        self.assertFalse(out["gate_release"]["passed"])
        self.assertIn("could not be saved", "\n".join(out["gate_release"]["human_questions"]))

    def test_with_saved_valid_evidence_the_release_is_ready(self) -> None:
        saved = self.root / "repo" / "docs" / "releases" / "run-1-trust-package.json"
        with patch("factory.pipeline.nodes.gates.release_evidence_gaps", return_value=[]), \
                patch("factory.pipeline.nodes.gates.write_trust_package", return_value=saved):
            out = node_gate_release(self._state(project_dir=str(self.root / "repo")))
        self.assertTrue(out["gate_release"]["passed"])
        self.assertEqual(out["status"], "waiting_human")  # READY still asks


if __name__ == "__main__":
    unittest.main()
