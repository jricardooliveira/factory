"""The intake interview service: the agent proposes questions, Python decides coverage.

Offline: `factory.runs.interview.run_agent` is patched to return frozen JSON, so no
test can reach a model.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import runs
from factory.adapters.opencode import AgentResult
from factory.domain.interview import FALLBACK_QUESTIONS, REQUIRED_TOPICS
from factory.evidence.brief import BRIEF_RELPATH, TRANSCRIPT_RELPATH
from factory.runs import RunError
from factory.state import db
from factory.state.interviews import add_answer, list_answers
from factory.workspace.projects import create_project


def _turn(*topics: str, done: bool = False, options: list[str] | None = None) -> AgentResult:
    questions = [
        {
            "topic": t,
            "question": f"Q about {t}?",
            "options": [{"label": o} for o in options or []],
        }
        for t in topics
    ]
    return AgentResult(
        agent="interview-agent",
        output=json.dumps({"questions": questions, "done": done}),
        duration_secs=0.1,
        returncode=0,
    )


DONE = _turn(done=True)


class _InterviewFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=self.home, slug="shop")
        self.repo = Path(self.project["repo_path"])
        self.asked: list[tuple[str, list[str]]] = []
        self.briefs: list[str] = []
        # Pin the opencode engine: with the optional SDK installed, "auto" would be live.
        env = patch.dict("os.environ", {"FACTORY_INTERVIEW_ENGINE": "opencode"})
        env.start()
        self.addCleanup(env.stop)

    def _agent(self, *results: AgentResult):
        mock = patch("factory.runs.interview.run_agent", side_effect=list(results))
        started = mock.start()
        self.addCleanup(mock.stop)
        return started

    def _ask(self, question, missing):
        self.asked.append((question.topic, list(missing)))
        return f"answer for {question.topic}"

    def _approve(self, brief: str):
        self.briefs.append(brief)
        return True

    def _answers(self) -> list[dict]:
        with db.get_db(self.db_path) as conn:
            return list_answers(conn, self.project["id"])

    def _run(self, ask=None, approve=None):
        return runs.run_interview(
            "shop", db_path=self.db_path, ask=ask or self._ask, approve=approve or self._approve
        )


class InterviewTests(_InterviewFixture):
    def test_happy_path_writes_and_commits_the_brief_and_transcript(self) -> None:
        agent = self._agent(
            _turn("goal"), _turn(*REQUIRED_TOPICS[1:5]), _turn(*REQUIRED_TOPICS[5:8]),
            _turn("security"), DONE,
        )

        self.assertFalse(runs.has_brief("shop", db_path=self.db_path))
        outcome = self._run()

        self.assertTrue(outcome.approved)
        self.assertEqual(outcome.project_id, self.project["id"])
        self.assertEqual(outcome.answers, 9)
        self.assertEqual(outcome.brief_path, self.repo / BRIEF_RELPATH)
        self.assertIn("answer for goal", (self.repo / BRIEF_RELPATH).read_text())
        self.assertIn("Q about users?", (self.repo / TRANSCRIPT_RELPATH).read_text())
        self.assertEqual(agent.call_count, 5)
        self.assertTrue(runs.has_brief("shop", db_path=self.db_path))

        log = subprocess.run(
            ["git", "log", "-1", "--name-only", "--format=%s"],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn(f"factory: product brief {self.project['id']}", log)
        self.assertIn(BRIEF_RELPATH, log)
        self.assertIn(TRANSCRIPT_RELPATH, log)

    def test_every_turn_is_logged_verbatim_and_the_prompt_carries_the_transcript(self) -> None:
        agent = self._agent(_turn("goal"), DONE, DONE)
        self._run()

        second_prompt = agent.call_args_list[1].args[1]
        self.assertIn("answer for goal", second_prompt)
        self.assertIn("users", second_prompt)  # still uncovered
        self.assertEqual(agent.call_args_list[0].kwargs["cwd"], str(self.repo))
        with db.get_db(self.db_path) as conn:
            rows = conn.execute("SELECT prompt, output_text FROM interview_turns").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["prompt"], second_prompt)
        self.assertEqual(rows[1]["output_text"], DONE.output)

    def test_agent_done_immediately_still_asks_the_nine_fallback_questions(self) -> None:
        agent = self._agent(DONE, DONE)
        outcome = self._run()

        self.assertTrue(outcome.approved)
        self.assertEqual([t for t, _ in self.asked], list(REQUIRED_TOPICS))
        self.assertEqual(
            [a["question"] for a in self._answers()],
            [FALLBACK_QUESTIONS[t] for t in REQUIRED_TOPICS],
        )
        self.assertEqual(agent.call_count, 2)

    def test_operator_done_with_topics_uncovered_gets_fallback_without_a_model_call(self) -> None:
        agent = self._agent(_turn("goal"), _turn("users"))
        replies = iter(["a goal", None])

        def ask(question, missing):
            self.asked.append((question.topic, list(missing)))
            return next(replies, "later answer")

        outcome = self._run(ask=ask)

        self.assertTrue(outcome.approved)
        self.assertEqual(agent.call_count, 2)
        # goal, users (-> done), then the eight still-uncovered topics from Python.
        self.assertEqual([t for t, _ in self.asked], ["goal", "users", *REQUIRED_TOPICS[1:]])
        self.assertEqual(self.asked[2][1], list(REQUIRED_TOPICS[1:]))

    def test_done_twice_pauses_and_a_second_run_resumes_without_re_asking(self) -> None:
        self._agent(_turn("goal"), _turn("users"))
        replies = iter(["a goal", None, None])
        outcome = self._run(ask=lambda q, m: next(replies))

        self.assertFalse(outcome.approved)
        self.assertIsNone(outcome.brief_path)
        self.assertEqual(outcome.answers, 1)
        self.assertFalse((self.repo / BRIEF_RELPATH).exists())
        self.assertEqual([a["topic"] for a in self._answers()], ["goal"])

        agent = self._agent(DONE, DONE)
        resumed = self._run()
        self.assertTrue(resumed.approved)
        self.assertEqual([t for t, _ in self.asked], list(REQUIRED_TOPICS[1:]))
        self.assertIn("a goal", agent.call_args_list[0].args[1])

    def test_you_decide_is_recorded_as_an_assumption(self) -> None:
        self._agent(_turn("goal", options=["Sell things", "Blog"]), DONE, DONE)
        replies = iter(["you decide"])
        self._run(ask=lambda q, m: next(replies, "x"))

        first = self._answers()[0]
        self.assertEqual((first["answer"], first["assumed"]), ("Sell things", True))
        self.assertEqual(first["options"], ["Sell things", "Blog"])

    def test_a_correction_is_recorded_and_the_interview_continues(self) -> None:
        agent = self._agent(DONE, DONE, _turn("stack"), DONE)
        verdicts = iter(["It is for wholesalers only", True])

        def approve(brief: str):
            self.briefs.append(brief)
            return next(verdicts)

        outcome = self._run(approve=approve)

        self.assertTrue(outcome.approved)
        self.assertEqual(agent.call_count, 4)  # the correction re-opens the agent's turn
        self.assertNotIn("wholesalers", self.briefs[0])
        self.assertIn("It is for wholesalers only", self.briefs[1])
        topics = [a["topic"] for a in self._answers()]
        self.assertEqual(topics[-2:], ["correction", "stack"])
        self.assertIn("Operator corrections", (self.repo / BRIEF_RELPATH).read_text())

    def test_approve_false_writes_nothing(self) -> None:
        self._agent(DONE, DONE)
        outcome = self._run(approve=lambda brief: False)

        self.assertFalse(outcome.approved)
        self.assertIsNone(outcome.brief_path)
        self.assertEqual(outcome.answers, 9)
        self.assertFalse((self.repo / BRIEF_RELPATH).exists())
        self.assertFalse((self.repo / TRANSCRIPT_RELPATH).exists())

    def test_unparseable_output_raises_run_error_and_is_still_logged(self) -> None:
        self._agent(AgentResult("interview-agent", "not json at all", 0.1, 0))
        with self.assertRaisesRegex(RunError, "interview-agent"):
            self._run()
        with db.get_db(self.db_path) as conn:
            logged = conn.execute("SELECT output_text FROM interview_turns").fetchone()
        self.assertEqual(logged["output_text"], "not json at all")

    def test_invalid_shape_and_failed_call_raise_run_error(self) -> None:
        self._agent(
            AgentResult("interview-agent", json.dumps({"questions": [{"topic": "goal"}]}), 0.1, 0),
            AgentResult("interview-agent", DONE.output, 0.1, 1),
        )
        for _ in range(2):
            with self.assertRaises(RunError):
                self._run()

    def test_the_first_turn_asks_only_the_broad_question(self) -> None:
        # Questions in one turn cannot see each other's answers: until the operator
        # has said what the product is, a second question may ask what that settles.
        agent = self._agent(_turn("goal", "users", "data"), DONE, DONE)
        self._run()
        self.assertIn("at most 1 in this turn", agent.call_args_list[0].args[1])
        self.assertEqual(self._answers()[0]["topic"], "goal")
        self.assertEqual(self._answers()[1]["question"], FALLBACK_QUESTIONS["users"])

    def test_at_most_four_questions_are_asked_per_turn(self) -> None:
        with db.get_db(self.db_path) as conn:
            add_answer(conn, self.project["id"], topic="goal", question="g", options=[],
                       answer="a shop", assumed=False)
        self._agent(_turn(*REQUIRED_TOPICS[1:7]), DONE, DONE)
        self._run()
        self.assertEqual([t for t, _ in self.asked][:4], list(REQUIRED_TOPICS[1:5]))
        # The fifth question asked is a fallback, not the agent's fifth.
        self.assertEqual(self._answers()[5]["question"], FALLBACK_QUESTIONS[REQUIRED_TOPICS[5]])

    def test_the_question_cap_stops_model_calls_but_not_the_minimum(self) -> None:
        with db.get_db(self.db_path) as conn:
            for n in range(3):
                add_answer(conn, self.project["id"], topic="other", question=f"q{n}",
                           options=[], answer="a", assumed=False)
        agent = self._agent()

        with patch("factory.runs.interview.MAX_INTERVIEW_QUESTIONS", 3):
            outcome = self._run()

        self.assertEqual(agent.call_count, 0)
        self.assertTrue(outcome.approved)
        self.assertEqual([t for t, _ in self.asked], list(REQUIRED_TOPICS))

    def test_a_cluster_left_wholly_unanswered_pauses_instead_of_calling_the_model_again(
        self,
    ) -> None:
        # An `ask` that yields nothing leaves the transcript unchanged: asking the
        # agent again would be the same paid call, forever.
        agent = self._agent(_turn("goal", "users"), _turn("goal", "users"))
        outcome = self._run(ask=lambda q, m: "   ")

        self.assertFalse(outcome.approved)
        self.assertEqual(agent.call_count, 1)
        self.assertEqual(self._answers(), [])
        self.assertFalse((self.repo / BRIEF_RELPATH).exists())

    def test_unknown_project_propagates_value_error(self) -> None:
        with self.assertRaises(ValueError):
            runs.run_interview("nope", db_path=self.db_path, ask=self._ask, approve=self._approve)


def _spec(language: str = "Python 3.12") -> AgentResult:
    spec = {"name": "Shop", "description": "A shop", "language": language,
            "framework": "FastAPI", "database": "SQLite"}
    return AgentResult("interview-agent", json.dumps(spec), 0.1, 0)


def _complete(project_id: str, db_path: Path) -> None:
    with db.get_db(db_path) as conn:
        for topic in REQUIRED_TOPICS:
            add_answer(conn, project_id, topic=topic, question=f"Q {topic}?", options=[],
                       answer=f"answer for {topic}", assumed=False)


def _last_commit(repo: Path) -> str:
    return subprocess.run(["git", "log", "-1", "--name-only", "--format=%s"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout


class StackProposalTests(_InterviewFixture):
    """After the brief is approved: one call proposes project-spec.json, the operator confirms."""

    def setUp(self) -> None:
        super().setUp()
        _complete(self.project["id"], self.db_path)
        self.proposed: list = []

    def _run_stack(self, *verdicts):
        replies = iter(verdicts)

        def confirm(spec):
            self.proposed.append(spec)
            return next(replies)

        return runs.run_interview("shop", db_path=self.db_path, ask=self._ask,
                                  approve=self._approve, confirm_stack=confirm)

    def _project(self) -> dict:
        from factory.workspace.projects import get_project
        return get_project(self.db_path, "shop")

    def test_confirmed_stack_is_written_registered_and_committed(self) -> None:
        agent = self._agent(DONE, _spec())
        outcome = self._run_stack(True)

        self.assertTrue(outcome.approved)
        self.assertEqual(self.proposed[0].language, "Python 3.12")
        stack_prompt = agent.call_args_list[1].args[1]
        self.assertIn("# Mode: stack", stack_prompt)
        self.assertIn("answer for goal", stack_prompt)  # the approved brief
        spec_file = self.repo / "project-spec.json"
        self.assertEqual(json.loads(spec_file.read_text())["framework"], "FastAPI")
        self.assertEqual(self._project()["spec_path"], str(spec_file))
        self.assertIn("project-spec.json", _last_commit(self.repo))

    def test_a_correction_is_recorded_as_a_stack_answer_and_re_proposed(self) -> None:
        agent = self._agent(DONE, _spec(), _spec("Go 1.23"))
        self._run_stack("Use Go instead", True)

        self.assertIn("Use Go instead", agent.call_args_list[2].args[1])
        last = self._answers()[-1]
        self.assertEqual((last["topic"], last["answer"]), ("stack", "Use Go instead"))
        self.assertIn("Use Go instead", (self.repo / BRIEF_RELPATH).read_text())
        self.assertIn("Go 1.23", (self.repo / "project-spec.json").read_text())

    def test_declined_stack_keeps_the_existing_spec(self) -> None:
        self._agent(DONE, _spec())
        outcome = self._run_stack(False)

        self.assertTrue(outcome.approved)
        self.assertFalse((self.repo / "project-spec.json").exists())
        self.assertIsNone(self._project()["spec_path"])

    def test_corrections_stop_at_the_proposal_cap(self) -> None:
        agent = self._agent(DONE, _spec(), _spec(), _spec())
        self._run_stack("a", "b", "c")

        self.assertEqual(agent.call_count, 4)  # one turn + MAX_STACK_PROPOSALS
        self.assertFalse((self.repo / "project-spec.json").exists())

    def test_an_unusable_stack_raises_but_the_brief_stays_approved(self) -> None:
        self._agent(DONE, AgentResult("interview-agent", '{"name": "x"}', 0.1, 0))
        with self.assertRaisesRegex(RunError, "stack"):
            self._run_stack(True)
        self.assertTrue(runs.has_brief("shop", db_path=self.db_path))


class AmendmentTests(_InterviewFixture):
    def setUp(self) -> None:
        super().setUp()
        _complete(self.project["id"], self.db_path)
        self._agent(DONE)
        self._run()  # the approved brief to amend

    def test_without_an_amendment_an_approved_brief_is_not_reopened(self) -> None:
        agent = self._agent()
        outcome = self._run()
        self.assertTrue(outcome.approved)
        self.assertEqual(outcome.brief_path, self.repo / BRIEF_RELPATH)
        agent.assert_not_called()

    def test_an_amendment_is_recorded_first_and_the_agent_asks_only_about_it(self) -> None:
        agent = self._agent(_turn("data"), DONE)
        outcome = runs.run_interview("shop", db_path=self.db_path, ask=self._ask,
                                     approve=self._approve, amend="Orders can be refunded")

        self.assertTrue(outcome.approved)
        prompt = agent.call_args_list[0].args[1]
        self.assertIn("Amendment", prompt)
        self.assertIn("Orders can be refunded", prompt)
        amended = [a for a in self._answers() if a["question"] == "Operator amendment"]
        self.assertEqual([a["answer"] for a in amended], ["Orders can be refunded"])
        self.assertEqual([t for t, _ in self.asked], ["data"])
        self.assertIn("Orders can be refunded", (self.repo / BRIEF_RELPATH).read_text())
        self.assertIn("product brief", _last_commit(self.repo))


class ImportAnswersTests(_InterviewFixture):
    def _all(self) -> list[dict]:
        return [{"topic": t, "question": f"Q {t}?", "answer": f"A {t}"} for t in REQUIRED_TOPICS]

    def test_complete_answers_write_and_commit_the_brief(self) -> None:
        answers = self._all()
        answers[0].update(options=["Sell", "Blog"], answer="Sell", assumed=True)
        outcome = runs.import_answers("shop", answers, db_path=self.db_path)

        self.assertTrue(outcome.approved)
        self.assertEqual(outcome.answers, 9)
        first = self._answers()[0]
        self.assertEqual((first["options"], first["assumed"]), (["Sell", "Blog"], True))
        self.assertIn("A users", (self.repo / BRIEF_RELPATH).read_text())
        self.assertIn(BRIEF_RELPATH, _last_commit(self.repo))

    def test_missing_topics_are_named_and_nothing_is_written(self) -> None:
        with self.assertRaisesRegex(RunError, "security"):
            runs.import_answers("shop", self._all()[:-1], db_path=self.db_path)
        self.assertEqual(self._answers(), [])
        self.assertFalse((self.repo / BRIEF_RELPATH).exists())

    def test_a_malformed_answer_is_refused_and_nothing_is_recorded(self) -> None:
        for bad in ([*self._all(), {"topic": "x"}], [*self._all(), "text"], {"a": 1}):
            with self.subTest(bad=bad), self.assertRaisesRegex(RunError, "answers"):
                runs.import_answers("shop", bad, db_path=self.db_path)
        self.assertEqual(self._answers(), [])


class StoryInterviewTests(_InterviewFixture):
    def setUp(self) -> None:
        super().setUp()
        _complete(self.project["id"], self.db_path)
        self._agent(DONE)
        self._run()

    def _story(self, ask=None) -> str:
        return runs.run_story_interview("shop", "Add refunds", db_path=self.db_path,
                                        ask=ask or self._ask)

    def _turn_count(self) -> int:
        with db.get_db(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM interview_turns").fetchone()[0]

    def test_nothing_to_ask_returns_the_request_unchanged(self) -> None:
        agent = self._agent(DONE)
        self.assertEqual(self._story(), "Add refunds")
        prompt = agent.call_args.args[1]
        self.assertIn("# Mode: story", prompt)
        self.assertIn("answer for goal", prompt)  # the brief
        self.assertIn("Add refunds", prompt)

    def test_answers_are_appended_not_stored_and_every_turn_is_logged(self) -> None:
        before_answers, before_turns = len(self._answers()), self._turn_count()
        agent = self._agent(_turn("errors", options=["Refuse", "Allow"]), DONE)
        replies = iter(["you decide"])
        text = self._story(ask=lambda q, m: next(replies))

        self.assertEqual(
            text,
            "Add refunds\n\n## Operator clarifications\n- Q about errors? — Refuse "
            "(assumption — the operator said \"you decide\")",
        )
        self.assertIn("Refuse", agent.call_args_list[1].args[1])
        self.assertEqual(len(self._answers()), before_answers)
        self.assertEqual(self._turn_count(), before_turns + 2)

    def _follow_up(self, of: int, *options: str) -> AgentResult:
        q = {"topic": "errors", "question": "Concretely?", "follow_up_of": of,
             "options": [{"label": o} for o in options]}
        return AgentResult("interview-agent", json.dumps({"questions": [q], "done": False}),
                           0.1, 0)

    def test_an_undecided_answer_is_flagged_to_the_agent_by_number(self) -> None:
        agent = self._agent(_turn("errors", options=["Refuse", "Allow"]), DONE)
        replies = iter(["I'm not sure, is it a problem?", "1"])
        self._story(ask=lambda q, m: next(replies))
        prompt = agent.call_args_list[1].args[1]
        self.assertIn("1. Q about errors? — I'm not sure, is it a problem? [UNDECIDED", prompt)

    def test_an_undecided_answer_nobody_followed_up_is_asked_again_without_a_model_call(
        self,
    ) -> None:
        agent = self._agent(_turn("errors", options=["Refuse", "Allow"]), DONE)
        asked: list[str] = []
        replies = iter(["I don't know, is it a problem?", "2"])

        def ask(question, missing):
            asked.append(question.question)
            return next(replies)

        text = self._story(ask=ask)
        self.assertEqual(agent.call_count, 2)  # the turn + done: the re-ask is Python's
        self.assertEqual(len(asked), 2)
        self.assertIn("Q about errors?", asked[1])
        self.assertIn("not sure", asked[1])
        self.assertEqual(text, "Add refunds\n\n## Operator clarifications\n"
                               "- Q about errors? — Allow")

    def test_still_undecided_when_asked_again_becomes_an_assumption(self) -> None:
        self._agent(_turn("errors", options=["Refuse", "Allow"]), DONE)
        replies = iter(["not sure", "really no idea"])
        text = self._story(ask=lambda q, m: next(replies))
        self.assertIn("- Q about errors? — Refuse (assumption", text)
        self.assertNotIn("UNDECIDED", text)

    def test_a_follow_up_that_settles_it_is_not_asked_a_third_time(self) -> None:
        self._agent(_turn("errors", options=["Refuse", "Allow"]),
                    self._follow_up(1, "Stop me", "Warn me"), DONE)
        asked: list[str] = []
        replies = iter(["I don't know", "1"])

        def ask(question, missing):
            asked.append(question.question)
            return next(replies)

        text = self._story(ask=ask)
        self.assertEqual(asked, ["Q about errors?", "Concretely?"])
        self.assertNotIn("I don't know", text)  # the follow-up carries the decision
        self.assertIn("- Concretely? — Stop me", text)

    def test_questions_stop_at_the_cap_and_done_stops_early(self) -> None:
        self._agent(*[_turn("errors", "data", "success", "users")] * 3)
        text = self._story()
        self.assertEqual(text.count("\n- "), 8)

        self._agent(_turn("errors", "data"))
        self.assertEqual(self._story(ask=lambda q, m: None), "Add refunds")

    def test_without_an_approved_brief_it_refuses(self) -> None:
        (self.repo / BRIEF_RELPATH).unlink()
        with self.assertRaises(RunError):
            self._story()


class InterviewEngineTests(unittest.TestCase):
    """FACTORY_INTERVIEW_ENGINE: the Claude Agent SDK when available, else opencode."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "factory.db"
        db.init_db(self.db_path)
        self.project = create_project(self.db_path, home=Path(tmp.name), slug="shop")
        self.sdk = self._patch("claude_sdk_run")
        self.opencode = self._patch("run_agent")
        self._patch("claude_sdk_available", return_value=True)

    def _patch(self, name: str, **kwargs):
        mock = patch(f"factory.runs.interview.{name}", **kwargs)
        started = mock.start()
        self.addCleanup(mock.stop)
        return started

    def _engine(self, value: str) -> None:
        env = patch.dict("os.environ", {"FACTORY_INTERVIEW_ENGINE": value})
        env.start()
        self.addCleanup(env.stop)

    def _next_turn(self):
        return runs.interview.next_turn(self.project, [], db_path=self.db_path)

    def _turns(self) -> list[str]:
        with db.get_db(self.db_path) as conn:
            return [r["model_name"] for r in conn.execute(
                "SELECT model_name FROM interview_turns ORDER BY id")]

    def _sdk_result(self, returncode: int = 0) -> AgentResult:
        return AgentResult("interview-agent", DONE.output, 0.1, returncode,
                           model_name="claude-sdk/claude-opus-5-5")

    def test_auto_uses_the_sdk_with_the_agent_body_and_the_bare_model(self) -> None:
        self._engine("auto")
        self.sdk.return_value = self._sdk_result()

        self.assertTrue(self._next_turn().done)

        self.opencode.assert_not_called()
        system_prompt, prompt = self.sdk.call_args.args
        self.assertTrue(system_prompt.startswith("# Interview Agent"))
        self.assertNotIn("model_tier:", system_prompt)
        self.assertIn("# Product:", prompt)
        self.assertEqual(self.sdk.call_args.kwargs["model"], "claude-opus-5-5")
        self.assertEqual(self._turns(), ["claude-sdk/claude-opus-5-5"])

    def test_auto_without_the_sdk_uses_opencode(self) -> None:
        self._engine("auto")
        self._patch("claude_sdk_available", return_value=False)
        self.opencode.return_value = DONE

        self._next_turn()

        self.sdk.assert_not_called()
        self.opencode.assert_called_once()

    def test_auto_retries_a_failed_sdk_turn_on_opencode_and_logs_both(self) -> None:
        self._engine("auto")
        self.sdk.return_value = self._sdk_result(returncode=1)
        self.opencode.return_value = DONE

        self.assertTrue(self._next_turn().done)

        self.assertEqual(self.opencode.call_args.args[1], self.sdk.call_args.args[1])
        self.assertEqual(self._turns()[0], "claude-sdk/claude-opus-5-5")
        self.assertEqual(len(self._turns()), 2)

    def test_auto_retries_an_unusable_sdk_answer_on_opencode(self) -> None:
        self._engine("auto")
        self.sdk.return_value = AgentResult("interview-agent", "Sure! Here are questions.", 0.1,
                                            0, model_name="claude-sdk/claude-opus-5-5")
        self.opencode.return_value = DONE

        self.assertTrue(self._next_turn().done)

        self.assertEqual(len(self._turns()), 2)

    def test_sdk_mode_failure_raises_and_never_falls_back(self) -> None:
        self._engine("sdk")
        self.sdk.return_value = self._sdk_result(returncode=1)

        with self.assertRaises(RunError):
            self._next_turn()

        self.opencode.assert_not_called()

    def test_opencode_mode_never_touches_the_sdk_and_an_unknown_engine_is_refused(self) -> None:
        self._engine("opencode")
        self.opencode.return_value = DONE
        self._next_turn()
        self.sdk.assert_not_called()

        self._engine("gpt")
        with self.assertRaisesRegex(RunError, "FACTORY_INTERVIEW_ENGINE"):
            self._next_turn()


if __name__ == "__main__":
    unittest.main()
