"""`factory <verb> --help` must print help — never execute the verb.

Found by a spot check: no verb understood `--help`, and `board` ignored unknown
options, so `factory board --help` launched the full-screen TUI; with no terminal
attached (a pipe, a script) it then spun at 99% CPU forever. For a verb that
spends tokens or changes state, "ignore the flag and run" is worse still.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from rich.console import Console


def _main(*argv: str) -> tuple[int, str]:
    from factory.interfaces.cli import main as cli_main

    buf = io.StringIO()
    called: list[str] = []
    fakes = {verb: (lambda args, v=verb: called.append(v)) for verb in cli_main.COMMANDS}
    with patch("factory.interfaces.render.output.console", Console(file=buf, width=200)), \
            patch.dict(cli_main.COMMANDS, fakes), \
            patch("sys.argv", ["factory", *argv]):
        try:
            cli_main.main()
            code = 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    if called:
        raise AssertionError(f"`factory {' '.join(argv)}` executed {called[0]!r}")
    return code, buf.getvalue()


class VerbHelpTests(unittest.TestCase):
    def test_board_help_prints_board_usage_without_running_it(self) -> None:
        code, out = _main("board", "--help")
        self.assertEqual(code, 0)
        self.assertIn("factory board", out)

    def test_short_flag_works_too(self) -> None:
        code, out = _main("approve", "-h")
        self.assertEqual(code, 0)
        self.assertIn("factory approve <run_id>", out)

    def test_help_is_scoped_to_the_verb(self) -> None:
        _code, out = _main("evals", "--help")
        self.assertIn("factory evals capture", out)
        self.assertNotIn("factory board", out)

    def test_help_after_other_arguments_still_wins(self) -> None:
        code, out = _main("reject", "20", "--help")
        self.assertEqual(code, 0)
        self.assertIn("factory reject", out)

    def test_interview_and_next_help_name_their_flags(self) -> None:
        _code, out = _main("interview", "--help")
        self.assertIn("--amend", out)
        self.assertIn("--import", out)
        _code, out = _main("next", "--help")
        self.assertIn("--no-interview", out)

    def test_every_verb_has_a_usage_line(self) -> None:
        from factory.interfaces.cli.main import COMMANDS

        for verb in COMMANDS:
            with self.subTest(verb=verb):
                _code, out = _main(verb, "--help")
                self.assertIn(f"factory {verb}", out)


if __name__ == "__main__":
    unittest.main()
