"""Shared CLI argument helpers (`factory.interfaces.cli.common`)."""

from __future__ import annotations

import unittest


class CliRunIdValidationTests(unittest.TestCase):
    def test_non_integer_run_id_exits_cleanly(self) -> None:
        from factory.interfaces.cli.common import run_id_arg

        with self.assertRaises(SystemExit):
            run_id_arg(["abc"], "Usage: factory review <run_id>")

    def test_missing_run_id_exits_cleanly(self) -> None:
        from factory.interfaces.cli.common import run_id_arg

        with self.assertRaises(SystemExit):
            run_id_arg([], "Usage: factory review <run_id>")

    def test_valid_run_id_parsed(self) -> None:
        from factory.interfaces.cli.common import run_id_arg

        self.assertEqual(run_id_arg(["42"], "usage"), 42)


if __name__ == "__main__":
    unittest.main()
