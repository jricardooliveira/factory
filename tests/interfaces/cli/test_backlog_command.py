"""`factory backlog <project>` and `factory next <project>`: the terminal side only.

The service is pinned in tests/runs/test_backlog.py; here it is replaced outright.
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from rich.console import Console

from factory.domain.backlog import BacklogStory
from factory.runs import BacklogOutcome, RunError, RunOutcome


class _ScriptedConsole(Console):
    def __init__(self, lines: list[str]) -> None:
        super().__init__(file=io.StringIO(), width=200)
        self.lines = list(lines)

    def input(self, prompt: str = "", *, markup: bool = True, **_kwargs: object) -> str:  # type: ignore[override]
        self.print(prompt, markup=markup)
        return self.lines.pop(0)


STORIES = [
    BacklogStory(title="Skeleton", request="Build the [empty] app.", rationale="first"),
    BacklogStory(title="Login", request="Let users sign in."),
]


def _main(argv: list[str], lines: list[str] | None = None) -> tuple[int, str]:
    from factory.interfaces.cli.main import main

    console = _ScriptedConsole(lines or [])
    with patch("factory.interfaces.render.output.console", console), \
            patch("sys.argv", ["factory", *argv]):
        try:
            main()
            code = 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, console.file.getvalue()  # type: ignore[attr-defined]


class BacklogCommandTests(unittest.TestCase):
    def _drive(self, lines: list[str], reviews: int = 1, outcome=BacklogOutcome(True, 2)):
        verdicts: list = []

        def fake(project_ref, *, db_path, review):
            verdicts.extend(review(STORIES) for _ in range(reviews))
            return outcome

        with patch("factory.runs.propose_backlog", side_effect=fake):
            code, out = _main(["backlog", "shop"], lines)
        return code, out, verdicts

    def test_shows_the_numbered_proposal_and_maps_the_answers(self) -> None:
        code, out, verdicts = self._drive(["y", "N", "split login up", "", "yes"], reviews=4)
        self.assertEqual(code, 0)
        self.assertEqual(verdicts, [True, False, "split login up", True])
        self.assertIn("1. Skeleton", out)
        self.assertIn("Build the [empty] app.", out)
        self.assertIn("first", out)
        self.assertIn("2. Login", out)
        self.assertIn("Approve? [y]es / [n]o / or type feedback", out)
        self.assertIn("BACKLOG.md", out)

    def test_unapproved_says_nothing_was_saved(self) -> None:
        _code, out, _ = self._drive(["n"], outcome=BacklogOutcome(False, 0))
        self.assertIn("not approved", out)

    def test_a_service_refusal_fails_the_command(self) -> None:
        with patch("factory.runs.propose_backlog", side_effect=RunError("no brief")):
            code, out = _main(["backlog", "shop"])
        self.assertEqual(code, 1)
        self.assertIn("no brief", out)

    def test_usage(self) -> None:
        self.assertEqual(_main(["backlog"])[0], 1)
        self.assertEqual(_main(["next"])[0], 1)


class NextCommandTests(unittest.TestCase):
    ROW = {"id": 3, "position": 1, "title": "Skeleton", "request": "Build the app."}

    def test_runs_the_next_story_and_marks_it_started(self) -> None:
        outcome = RunOutcome(run_id=9, story_id="STORY-002", status="waiting_human",
                             error=None, current_stage=None, db_path=Path("x"))
        with patch("factory.runs.next_story", return_value=self.ROW), \
                patch("factory.runs.run_project_pipeline", return_value=outcome) as run, \
                patch("factory.runs.mark_started") as mark:
            code, out = _main(["next", "shop"])
        self.assertEqual(code, 0)
        run.assert_called_once_with("shop", "Build the app.", db_path=ANY, on_event=ANY)
        mark.assert_called_once_with(3, story_id="STORY-002", run_id=9, db_path=ANY)
        self.assertIn("Skeleton", out)

    def _next(self, argv: list[str], *, tty: bool, brief: bool = True):
        outcome = RunOutcome(run_id=9, story_id="STORY-002", status="waiting_human",
                             error=None, current_stage=None, db_path=Path("x"))
        with patch("factory.runs.next_story", return_value=self.ROW), \
                patch("factory.runs.run_project_pipeline", return_value=outcome) as run, \
                patch("factory.runs.mark_started"), \
                patch("factory.runs.has_brief", return_value=brief), \
                patch("factory.runs.run_story_interview",
                      side_effect=lambda ref, req, **kw: req + " [clarified]") as story, \
                patch("sys.stdin.isatty", return_value=tty):
            code, _out = _main(["next", *argv])
        self.assertEqual(code, 0)
        return run.call_args.args[1], story

    def test_on_a_terminal_the_story_interview_clarifies_the_request(self) -> None:
        request, story = self._next(["shop"], tty=True)
        self.assertEqual(request, "Build the app. [clarified]")
        self.assertEqual(story.call_args.args, ("shop", "Build the app."))

    def test_no_story_interview_off_a_terminal_without_a_brief_or_with_the_flag(self) -> None:
        for argv, tty, brief in ((["shop"], False, True), (["shop"], True, False),
                                 (["shop", "--no-interview"], True, True)):
            with self.subTest(argv=argv, tty=tty, brief=brief):
                request, story = self._next(argv, tty=tty, brief=brief)
                self.assertEqual(request, "Build the app.")
                story.assert_not_called()

    def test_empty_backlog(self) -> None:
        with patch("factory.runs.next_story", return_value=None), \
                patch("factory.runs.run_project_pipeline") as run:
            code, out = _main(["next", "shop"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertIn("backlog empty", out)

    def test_unknown_project_fails(self) -> None:
        with patch("factory.runs.next_story", side_effect=ValueError("Unknown project: x")):
            code, out = _main(["next", "x"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown project", out)
