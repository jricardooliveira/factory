"""A typo must never start a live (token-spending) run.

Found by review: any unrecognised first argument was a free-form request, so
`factory lsit` ran the spec-agent on the request "lsit" — twice, counting the
JSON retry — and `factory 'project list'` (a quoted verb) made a real model call.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from rich.console import Console


def _main(*argv: str) -> tuple[int, str, object]:
    from factory.interfaces.cli.main import main

    buf = io.StringIO()
    with patch("factory.interfaces.render.output.console", Console(file=buf, width=200)), \
            patch("factory.runs.run_pipeline") as run_pipeline, \
            patch("sys.argv", ["factory", *argv]):
        try:
            main()
            code = 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, buf.getvalue(), run_pipeline


class RequestGuardTests(unittest.TestCase):
    def test_a_misspelt_verb_is_refused_with_a_suggestion(self) -> None:
        code, out, run_pipeline = _main("lsit")
        self.assertEqual(code, 1)
        run_pipeline.assert_not_called()
        self.assertIn("factory list", out)

    def test_a_quoted_verb_is_refused_with_a_suggestion(self) -> None:
        code, out, run_pipeline = _main("project list")
        self.assertEqual(code, 1)
        run_pipeline.assert_not_called()
        self.assertIn("factory project list", out)

    def test_a_single_word_that_is_no_verb_is_refused(self) -> None:
        code, out, run_pipeline = _main("zzzz")
        self.assertEqual(code, 1)
        run_pipeline.assert_not_called()
        self.assertIn("not a factory command", out)

    def test_a_real_request_still_runs(self) -> None:
        code, _out, run_pipeline = _main("Add a health endpoint to the API")
        self.assertEqual(code, 0)
        run_pipeline.assert_called_once()
        self.assertEqual(run_pipeline.call_args.args[0], "Add a health endpoint to the API")

    def test_a_request_that_merely_starts_with_a_verb_still_runs(self) -> None:
        code, _out, run_pipeline = _main("review the login flow for lockouts")
        self.assertEqual(code, 0)
        run_pipeline.assert_called_once()


if __name__ == "__main__":
    unittest.main()
