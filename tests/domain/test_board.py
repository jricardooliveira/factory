"""The board's pure derivations (design handoff: design_handoff_factory_board/screens.js)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from factory.domain.board import (
    StoryFacts, age, inbox_group, next_start, pick_batch, stage_strip, story_state,
)
from factory.domain.workflow import ResourceClaim, StoryPlan

NOW = datetime(2026, 10, 3, 14, 32, tzinfo=timezone.utc)


def _plan(n: int, *files: str) -> StoryPlan:
    return StoryPlan(id=f"p{n}", project_id="P", backlog_id=n, revision=1, context_revision="c",
                     base_commit="b", request="r", ready=True,
                     resources=tuple(ResourceClaim(kind="file", name=f) for f in files))


class StageStripTests(unittest.TestCase):
    def test_pipeline_stages_fold_into_spec_design_code_test(self) -> None:
        progress = [("Spec", "done"), ("Gate 1", "done"), ("Architect", "done"),
                    ("Gate 2", "waiting"), ("Coder", "pending"), ("Build", "pending"),
                    ("Tester", "pending"), ("Test", "pending"), ("Release", "pending")]
        self.assertEqual(stage_strip(progress), ("done", "wait", "todo", "todo"))

    def test_a_failure_and_a_running_stage(self) -> None:
        self.assertEqual(stage_strip([("Spec", "done"), ("Gate 1", "done"),
                                      ("Architect", "current")]), ("done", "run", "todo", "todo"))
        self.assertEqual(stage_strip([("Spec", "failed")]), ("fail", "todo", "todo", "todo"))


class StoryStateTests(unittest.TestCase):
    def test_states_from_what_is_recorded(self) -> None:
        cases = [
            (StoryFacts(), ("draft", "")),
            (StoryFacts(session="refining"), ("refining", "")),
            (StoryFacts(session="refining", refine_failed=True), ("notready", "refinement failed")),
            (StoryFacts(session="needs_input", questions=3), ("needs", "3 questions")),
            (StoryFacts(session="blocked", uncertainties=2), ("notready", "2 uncertainties")),
            (StoryFacts(session="ready", plan_ready=True), ("ready", "")),
            (StoryFacts(plan_ready=True, build_queued=True), ("working", "starting")),
            (StoryFacts(run="running"), ("working", "")),
            (StoryFacts(run="waiting_human", run_stage="gate-2-architect"),
             ("working", "waiting for you")),
            (StoryFacts(run="waiting_human", run_stage="gate-release-human"),
             ("release", "waiting for you")),
            (StoryFacts(run="completed"), ("done", "")),
            (StoryFacts(run="failed"), ("notready", "last run failed")),
            (StoryFacts(run="running", stop_requested=True), ("working", "stopping after this step")),
        ]
        for facts, expected in cases:
            with self.subTest(facts=facts):
                self.assertEqual(story_state(facts), expected)


class InboxTests(unittest.TestCase):
    def test_failed_then_approve_then_answer(self) -> None:
        self.assertEqual([inbox_group(k) for k in ("fail", "ckpt", "release", "backlog",
                                                    "brief", "questions")],
                         [0, 1, 1, 1, 1, 2])

    def test_age_is_relative_under_an_hour_then_hours(self) -> None:
        self.assertEqual(age((NOW - timedelta(seconds=12)).isoformat(), NOW), "12s ago")
        self.assertEqual(age((NOW - timedelta(minutes=40)).isoformat(), NOW), "40m ago")
        self.assertEqual(age((NOW - timedelta(hours=3, minutes=5)).isoformat(), NOW), "3h ago")
        self.assertEqual(age((NOW - timedelta(days=2)).isoformat(), NOW), "2d ago")


class PickBatchTests(unittest.TestCase):
    def test_greedy_non_overlapping_pick_with_the_reason_the_rest_wait(self) -> None:
        plans = [_plan(3, "ai/opponent.py"), _plan(4, "game/state.py", "ui/menu.py"),
                 _plan(8, "ui/menu.py", "assets/sounds/")]
        picked, why = pick_batch(plans, limit=2)
        self.assertEqual([p.backlog_id for p in picked], [3, 4])
        self.assertEqual(why, "#8 waits: it changes ui/menu.py like #4.")

    def test_the_cap_is_the_reason_when_nothing_overlaps(self) -> None:
        picked, why = pick_batch([_plan(1, "a.py"), _plan(2, "b.py"), _plan(3, "c.py")], limit=2)
        self.assertEqual(len(picked), 2)
        self.assertEqual(why, "#3 waits: 2 run at a time.")


class NextStartTests(unittest.TestCase):
    def test_priority_order(self) -> None:
        base = dict(brief=True, intake_open=False, paused=False, ready=[], stories=3,
                    backlog_open=False, question_subject="")
        self.assertEqual(next_start(**{**base, "brief": False}).action, "interview")
        self.assertEqual(next_start(**{**base, "brief": False, "intake_open": True}).action, "")
        self.assertEqual(next_start(**{**base, "paused": True}).action, "pause")
        plans = [_plan(3, "a.py"), _plan(4, "b.py")]
        step = next_start(**{**base, "ready": plans})
        self.assertEqual(step.action, "batch")
        self.assertIn("#3 and #4 can run together", step.title)
        self.assertEqual(next_start(**{**base, "stories": 0}).action, "backlog")
        idle = next_start(**{**base, "question_subject": "Story #2 Board"})
        self.assertEqual((idle.action, idle.title), ("", "Nothing can start yet."))
        self.assertIn("Answering Story #2 Board would make it ready.", idle.reason)


if __name__ == "__main__":
    unittest.main()
