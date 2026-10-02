"""Every boss decision is stored — allowed or refused — with what it rested on.

Kept apart from `gate_results`: a gate judges a stage's output, an authorization
its inputs. Mixing them would put an "authorization" row into every gate pass
rate `factory metrics` reports.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.state import db


class AuthorizationLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "f.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Story", "req")
            self.run_id = db.start_run(conn, "US-0001")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_decisions_round_trip_in_order_with_their_lists(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.log_authorization(conn, self.run_id, "architect-agent", True,
                                 granted_by="gate-1-spec passed")
            db.log_authorization(conn, self.run_id, "coder-agent:T-2", False,
                                 missing=["T-1 (a dependency of T-2) to be implemented first"],
                                 warnings=["T-2 declares no allowed scope"])
            rows = db.get_run_authorizations(conn, self.run_id)
        self.assertEqual([r["stage"] for r in rows], ["architect-agent", "coder-agent:T-2"])
        self.assertTrue(rows[0]["allowed"])
        self.assertEqual(rows[0]["missing"], [])
        self.assertFalse(rows[1]["allowed"])
        self.assertEqual(rows[1]["missing"], ["T-1 (a dependency of T-2) to be implemented first"])
        self.assertEqual(rows[1]["warnings"], ["T-2 declares no allowed scope"])
        self.assertTrue(rows[1]["decided_at"])

    def test_authorizations_never_show_up_as_gates(self) -> None:
        with db.get_db(self.db_path) as conn:
            db.log_authorization(conn, self.run_id, "tester-agent", False, missing=["x"])
            self.assertEqual(db.get_run_gates(conn, self.run_id), [])

    def test_an_old_database_gains_the_table(self) -> None:
        # init_db is the migration path: re-running it on an existing DB is safe.
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            self.assertEqual(db.get_run_authorizations(conn, self.run_id), [])


if __name__ == "__main__":
    unittest.main()
