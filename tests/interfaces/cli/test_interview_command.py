"""`factory interview <project>` and the brief `factory run --project` now requires.

The interview service is pinned in tests/runs/test_interview.py; these tests only
check the terminal side: what `ask` / `approve` print and return, and that a run
never starts on a project nobody has defined.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from rich.console import Console

from factory.domain.interview import InterviewOption, InterviewQuestion
from factory.domain.project_spec import ProjectSpec
from factory.runs import InterviewOutcome, RunError
from factory.workspace import layout


class _ScriptedConsole(Console):
    """A console whose `input` replays scripted lines instead of reading stdin."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__(file=io.StringIO(), width=200)
        self.lines = list(lines)

    def input(self, prompt: str = "", *, markup: bool = True, **_kwargs: object) -> str:  # type: ignore[override]
        # Rendered as the real Console.input would: "[y]es" survives only unmarked-up.
        self.print(prompt, markup=markup)
        return self.lines.pop(0)


QUESTION = InterviewQuestion(
    topic="must_not_do",
    question="What should it never do?",
    options=[
        InterviewOption(label="Send email", description="no outbound mail"),
        InterviewOption(label="Delete data"),
    ],
)
APPROVED = InterviewOutcome("PROJ-001", True, Path("/repo/docs/work/BRIEF.md"), 9)
PAUSED = InterviewOutcome("PROJ-001", False, None, 3)


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


class InterviewCommandTests(unittest.TestCase):
    def _drive(self, script, lines: list[str], outcome: InterviewOutcome = APPROVED):
        """Run `factory interview`, with the service replaced by `script(ask, approve)`."""
        seen: dict = {}

        def fake(project_ref, *, db_path, ask, approve, **kwargs):
            seen.update(ref=project_ref, db_path=db_path, kwargs=kwargs,
                        result=script(ask, approve))
            return outcome

        with patch("factory.runs.run_interview", side_effect=fake), \
                patch("factory.runs.has_brief", return_value=False):
            code, out = _main(["interview", "cli-app"], lines)
        return code, out, seen

    def test_dispatches_to_the_interview_service(self) -> None:
        code, out, seen = self._drive(lambda ask, approve: None, [])
        self.assertEqual(code, 0)
        self.assertEqual((seen["ref"], seen["db_path"]), ("cli-app", layout.db_path()))
        self.assertIn("BRIEF.md", out)

    def test_ask_prints_topic_question_options_and_hint(self) -> None:
        _code, out, seen = self._drive(lambda ask, approve: ask(QUESTION, ["must_not_do"]), ["2"])
        self.assertEqual(seen["result"], "2")
        self.assertIn("What it must NOT do", out)
        self.assertIn("What should it never do?", out)
        self.assertIn("1. Send email", out)
        self.assertIn("no outbound mail", out)
        self.assertIn("2. Delete data", out)
        self.assertIn("(number, your own words, 'you decide', or 'done')", out)

    def test_blank_input_asks_again(self) -> None:
        _code, _out, seen = self._drive(
            lambda ask, approve: ask(QUESTION, []), ["", "   ", "nothing destructive"]
        )
        self.assertEqual(seen["result"], "nothing destructive")

    def test_done_returns_none(self) -> None:
        _code, _out, seen = self._drive(lambda ask, approve: ask(QUESTION, []), ["Done"])
        self.assertIsNone(seen["result"])

    def test_asked_again_after_done_names_the_topics_still_required(self) -> None:
        def script(ask, approve):
            first = ask(QUESTION, ["data", "security"])
            return first, ask(QUESTION, ["data", "security"])

        _code, out, seen = self._drive(script, ["done", "x"])
        self.assertEqual(seen["result"], (None, "x"))
        self.assertIn("still required", out)
        self.assertIn("Data, Security and access", out)

    def test_topics_are_not_nagged_before_the_operator_says_done(self) -> None:
        _code, out, _seen = self._drive(lambda ask, approve: ask(QUESTION, ["data"]), ["x"])
        self.assertNotIn("still required", out)

    def test_approve_maps_yes_no_and_a_correction(self) -> None:
        def script(ask, approve):
            return [approve("# Product brief — CLI App") for _ in range(3)]

        _code, out, seen = self._drive(script, ["Y", "no", "the users are wrong"])
        self.assertEqual(seen["result"], [True, False, "the users are wrong"])
        self.assertIn("Product brief — CLI App", out)
        self.assertIn("Approve? [y]es / [n]o, stop for now / or type what is wrong", out)

    def test_unapproved_outcome_says_how_to_resume(self) -> None:
        code, out, _seen = self._drive(lambda ask, approve: None, [], outcome=PAUSED)
        self.assertEqual(code, 0)
        self.assertIn("factory interview cli-app", out)
        self.assertIn("3 answer(s) saved", out)

    def test_refusals_exit_non_zero(self) -> None:
        for exc in (RunError("interview-agent call failed"), ValueError("Unknown project: nope")):
            with self.subTest(exc=exc), patch("factory.runs.run_interview", side_effect=exc), \
                    patch("factory.runs.has_brief", return_value=False):
                code, out = _main(["interview", "nope"])
                self.assertEqual(code, 1)
                self.assertIn(str(exc), out)

    def test_missing_project_argument_is_a_usage_error(self) -> None:
        with patch("factory.runs.run_interview") as run_interview:
            code, out = _main(["interview"])
        self.assertEqual(code, 1)
        self.assertIn("factory interview <project-id-or-slug>", out)
        run_interview.assert_not_called()

    def test_confirm_stack_shows_the_spec_and_maps_the_answers(self) -> None:
        spec = ProjectSpec(name="Shop", description="d", language="Go 1.23",
                           framework="net/http", database="SQLite")

        def script(ask, approve):
            confirm = seen_kwargs["confirm_stack"]
            return [confirm(spec) for _ in range(3)]

        seen_kwargs: dict = {}

        def fake(project_ref, *, db_path, ask, approve, **kwargs):
            seen_kwargs.update(kwargs)
            self.result = script(ask, approve)
            return APPROVED

        with patch("factory.runs.run_interview", side_effect=fake), \
                patch("factory.runs.has_brief", return_value=False):
            _code, out = _main(["interview", "cli-app"], ["y", "n", "use Python"])
        self.assertEqual(self.result, [True, False, "use Python"])
        self.assertIn("Go 1.23", out)
        self.assertIn("Use this stack?", out)
        self.assertIsNone(seen_kwargs["amend"])

    def test_an_approved_brief_is_not_reopened_without_amend(self) -> None:
        with patch("factory.runs.has_brief", return_value=True), \
                patch("factory.runs.run_interview") as run_interview:
            code, out = _main(["interview", "cli-app"])
        self.assertEqual(code, 0)
        run_interview.assert_not_called()
        self.assertIn("--amend", out)


class AmendTests(unittest.TestCase):
    def _amend(self, lines: list[str], next_row):
        with patch("factory.runs.run_interview", return_value=APPROVED) as run_interview, \
                patch("factory.runs.next_story", return_value=next_row), \
                patch("factory.runs.propose_backlog") as propose:
            propose.return_value = runs_backlog_outcome()
            code, out = _main(["interview", "cli-app", "--amend", "Refunds", "too"], lines)
        return code, out, run_interview, propose

    def test_amend_passes_the_change_and_offers_to_re_propose_the_backlog(self) -> None:
        code, out, run_interview, propose = self._amend(["y"], {"id": 1})
        self.assertEqual(code, 0)
        self.assertEqual(run_interview.call_args.kwargs["amend"], "Refunds too")
        self.assertEqual(propose.call_args.args[0], "cli-app")
        self.assertIn("backlog", out)

    def test_the_backlog_offer_can_be_declined_and_is_skipped_without_one(self) -> None:
        _code, _out, _ri, propose = self._amend(["n"], {"id": 1})
        propose.assert_not_called()
        _code, out, _ri, propose = self._amend([], None)
        propose.assert_not_called()
        self.assertNotIn("Re-propose", out)

    def test_amend_needs_its_text(self) -> None:
        with patch("factory.runs.run_interview") as run_interview:
            code, _out = _main(["interview", "cli-app", "--amend"])
        self.assertEqual(code, 1)
        run_interview.assert_not_called()


def runs_backlog_outcome():
    from factory.runs import BacklogOutcome
    return BacklogOutcome(True, 2)


class ImportTests(unittest.TestCase):
    def _file(self, content: str) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(content)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_import_reads_the_file_and_reports_the_brief(self) -> None:
        answers = [{"topic": "goal", "question": "Q?", "answer": "A"}]
        path = self._file(json.dumps(answers))
        with patch("factory.runs.import_answers", return_value=APPROVED) as imp:
            code, out = _main(["interview", "cli-app", "--import", path])
        self.assertEqual(code, 0)
        imp.assert_called_once_with("cli-app", answers, db_path=layout.db_path())
        self.assertIn("BRIEF.md", out)

    def test_bad_json_missing_file_and_refusals_exit_non_zero(self) -> None:
        with patch("factory.runs.import_answers", side_effect=RunError("uncovered: data")):
            for argv, needle in (
                (["--import", self._file("{not json")], "not valid JSON"),
                (["--import", "/no/such/answers.json"], "not found"),
                (["--import", self._file("[]")], "uncovered: data"),
            ):
                with self.subTest(argv=argv):
                    code, out = _main(["interview", "cli-app", *argv])
                    self.assertEqual(code, 1)
                    self.assertIn(needle, out)


class RunRequiresBriefTests(unittest.TestCase):
    def _run(self, argv: list[str], *, brief: bool, tty: bool, outcome=APPROVED):
        with patch("factory.runs.run_project_pipeline") as pipeline, \
                patch("factory.runs.has_brief", return_value=brief), \
                patch("factory.runs.run_interview", return_value=outcome) as interview, \
                patch("factory.runs.run_story_interview",
                      side_effect=lambda ref, req, **kw: req + " [clarified]") as story, \
                patch("sys.stdin.isatty", return_value=tty):
            code, out = _main(["run", *argv])
        self.story = story
        return code, out, pipeline, interview

    def test_approved_brief_runs_without_an_interview(self) -> None:
        code, _out, pipeline, interview = self._run(
            ["--project", "cli-app", "Add", "health"], brief=True, tty=False
        )
        self.assertEqual(code, 0)
        interview.assert_not_called()
        pipeline.assert_called_once_with(
            "cli-app", "Add health", db_path=layout.db_path(), on_event=ANY
        )

    def test_no_brief_without_a_terminal_refuses_and_points_at_the_way_out(self) -> None:
        code, out, pipeline, interview = self._run(
            ["--project", "cli-app", "Add health"], brief=False, tty=False
        )
        self.assertEqual(code, 1)
        self.assertIn("factory interview cli-app", out)
        self.assertIn("--no-interview", out)
        pipeline.assert_not_called()
        interview.assert_not_called()

    def test_no_brief_on_a_terminal_interviews_then_runs(self) -> None:
        code, _out, pipeline, interview = self._run(
            ["--project", "cli-app", "Add health"], brief=False, tty=True
        )
        self.assertEqual(code, 0)
        interview.assert_called_once()
        pipeline.assert_called_once()

    def test_a_briefed_project_on_a_terminal_gets_the_story_interview(self) -> None:
        code, _out, pipeline, interview = self._run(
            ["--project", "cli-app", "Add health"], brief=True, tty=True
        )
        self.assertEqual(code, 0)
        interview.assert_not_called()
        self.assertEqual(self.story.call_args.args, ("cli-app", "Add health"))
        pipeline.assert_called_once_with(
            "cli-app", "Add health [clarified]", db_path=layout.db_path(), on_event=ANY
        )

    def test_no_story_interview_off_a_terminal_or_with_no_interview(self) -> None:
        for argv, tty in ((["--project", "cli-app", "Add health"], False),
                          (["--no-interview", "--project", "cli-app", "Add health"], True)):
            with self.subTest(argv=argv):
                self._run(argv, brief=True, tty=tty)
                self.story.assert_not_called()

    def test_unapproved_interview_does_not_start_the_run(self) -> None:
        _code, out, pipeline, _interview = self._run(
            ["--project", "cli-app", "Add health"], brief=False, tty=True, outcome=PAUSED
        )
        pipeline.assert_not_called()
        self.assertIn("factory interview cli-app", out)

    def test_no_interview_flag_skips_the_check_and_stays_out_of_the_request(self) -> None:
        for argv in (
            ["--no-interview", "--project", "cli-app", "Add health"],
            ["--project", "cli-app", "Add health", "--no-interview"],
        ):
            with self.subTest(argv=argv):
                with patch("factory.runs.has_brief") as has_brief:
                    code, _out, pipeline, interview = self._run(argv, brief=False, tty=False)
                self.assertEqual(code, 0)
                has_brief.assert_not_called()
                interview.assert_not_called()
                pipeline.assert_called_once_with(
                    "cli-app", "Add health", db_path=layout.db_path(), on_event=ANY
                )

    def test_unknown_project_fails_cleanly(self) -> None:
        with patch("factory.runs.has_brief", side_effect=ValueError("Unknown project: nope")), \
                patch("factory.runs.run_project_pipeline") as pipeline:
            code, out = _main(["run", "--project", "nope", "Add health"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown project: nope", out)
        pipeline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
