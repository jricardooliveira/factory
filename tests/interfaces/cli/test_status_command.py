"""`factory status [project]`: the lifecycle view and the next command, in the terminal."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from factory.interfaces.render import output
from factory.interfaces.render.status import status_lines
from factory.workspace import layout
from factory.workspace.projects import create_project


def _run(*argv: str) -> str:
    from factory.interfaces.cli.main import main

    with output.console.capture() as captured, patch("sys.argv", ["factory", *argv]):
        main()
    return " ".join(captured.get().split())


class StatusCommandTests(unittest.TestCase):
    def test_a_new_project_shows_define_now_and_the_interview_command(self) -> None:
        create_project(layout.db_path(), slug="habits")
        text = _run("status", "habits")
        self.assertIn("Define", text)
        self.assertIn("no brief yet", text)
        self.assertIn("Next: factory interview habits", text)

    def test_without_a_project_every_project_is_shown(self) -> None:
        create_project(layout.db_path(), slug="habits")
        create_project(layout.db_path(), slug="shop")
        text = _run("status")
        self.assertIn("factory interview habits", text)
        self.assertIn("factory interview shop", text)

    def test_no_projects_says_how_to_start(self) -> None:
        self.assertIn("factory project create", _run("status"))

    def test_lines_are_plain_text_for_the_board_too(self) -> None:
        from factory import runs

        create_project(layout.db_path(), slug="habits")
        lines = status_lines(runs.project_status("habits", db_path=layout.db_path()))
        self.assertTrue(any(line.startswith("Next:") for line in lines))
        self.assertFalse(any("[/" in line for line in lines), "no rich markup in plain lines")


if __name__ == "__main__":
    unittest.main()
