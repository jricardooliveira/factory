"""`factory board` option handling and its no-terminal behaviour."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from rich.console import Console

from factory.interfaces.cli import board


def _board(*args: str, tty: bool) -> tuple[int, str, object, object]:
    buf = io.StringIO()
    with patch("factory.interfaces.render.output.console", Console(file=buf, width=200)), \
            patch.object(board, "_has_terminal", return_value=tty), \
            patch("factory.interfaces.board.tui.run_board_tui") as tui, \
            patch.object(board, "show_board") as show:
        try:
            board.board_command(list(args))
            code = 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, buf.getvalue(), tui, show


class BoardCommandTests(unittest.TestCase):
    def test_an_unknown_option_is_refused_rather_than_ignored(self) -> None:
        code, out, tui, show = _board("--bogus", tty=True)
        self.assertEqual(code, 1)
        self.assertIn("--bogus", out)
        tui.assert_not_called()
        show.assert_not_called()

    def test_without_a_terminal_it_prints_one_snapshot_instead_of_the_tui(self) -> None:
        # A full-screen TUI with no terminal never exits and burns a CPU core.
        code, _out, tui, show = _board(tty=False)
        self.assertEqual(code, 0)
        tui.assert_not_called()
        show.assert_called_once_with(once=True)

    def test_with_a_terminal_it_opens_the_tui(self) -> None:
        code, _out, tui, show = _board(tty=True)
        self.assertEqual(code, 0)
        tui.assert_called_once()
        show.assert_not_called()

    def test_once_and_interval_are_still_accepted(self) -> None:
        code, _out, _tui, show = _board("--plain", "--interval", "5", tty=True)
        self.assertEqual(code, 0)
        show.assert_called_once_with(once=False, interval=5.0)

    def test_a_non_numeric_interval_is_refused(self) -> None:
        code, out, _tui, show = _board("--plain", "--interval", "soon", tty=True)
        self.assertEqual(code, 1)
        self.assertIn("--interval", out)
        show.assert_not_called()


if __name__ == "__main__":
    unittest.main()
