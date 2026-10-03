"""Where a project stands (Define → Plan → Build → Release) and the next command."""

from __future__ import annotations

import unittest

from factory.domain.lifecycle import ProjectFacts, RunFact, StoryFact, lifecycle, next_step


def _facts(**kw) -> ProjectFacts:
    base = dict(slug="habits", has_brief=True, answers=21, assumptions=1, has_stack=True,
                backlog=(), runs=(), intake_usd=0.55, stories_usd=0.0)
    base.update(kw)
    return ProjectFacts(**base)


def _story(pos: int, status: str = "approved", run: RunFact | None = None) -> StoryFact:
    return StoryFact(position=pos, title=f"Story {pos}", status=status, run=run)


def _run(rid: int, status: str, stage: str = "gate-2-human", retryable: bool = False) -> RunFact:
    return RunFact(id=rid, status=status, stage=stage, title=f"run {rid}", retryable=retryable)


class NextStepTests(unittest.TestCase):
    def test_no_brief_means_interview(self) -> None:
        self.assertEqual(next_step(_facts(has_brief=False, answers=0)).command,
                         "factory interview habits")

    def test_a_paused_interview_says_resume(self) -> None:
        step = next_step(_facts(has_brief=False, answers=5))
        self.assertEqual(step.command, "factory interview habits")
        self.assertIn("5 answers", step.why)

    def test_brief_without_backlog_means_backlog(self) -> None:
        self.assertEqual(next_step(_facts()).command, "factory backlog habits")

    def test_a_parked_run_comes_before_anything_else(self) -> None:
        facts = _facts(backlog=(_story(1, "started", _run(1, "waiting_human")), _story(2)),
                       runs=(_run(1, "waiting_human"),))
        step = next_step(facts)
        self.assertEqual(step.command, "factory approve 1")
        self.assertIn("factory reject 1", step.alternative)

    def test_a_running_run_means_wait(self) -> None:
        step = next_step(_facts(backlog=(_story(1, "started", _run(1, "running", "coder")),),
                                runs=(_run(1, "running", "coder"),)))
        self.assertEqual(step.command, "factory board")
        self.assertIn("coder", step.why)

    def test_a_failed_run_means_review_then_dismiss_and_next(self) -> None:
        """No answered checkpoint (e.g. a gate-build failure): `factory retry`
        would refuse, so it is never suggested; dismiss returns the story."""
        for status in ("failed", "blocked"):
            with self.subTest(status=status):
                step = next_step(_facts(runs=(_run(3, status, "coder-agent"),),
                                        backlog=(_story(1, "started", _run(3, status)),)))
                self.assertEqual(step.command, "factory review 3")
                self.assertNotIn("retry", step.alternative)
                self.assertIn("factory dismiss 3", step.alternative)
                self.assertIn("factory next habits", step.alternative)

    def test_a_failed_run_with_an_answered_checkpoint_offers_retry(self) -> None:
        run = _run(3, "failed", "tester", retryable=True)
        step = next_step(_facts(runs=(run,), backlog=(_story(1, "started", run),)))
        self.assertEqual(step.command, "factory review 3")
        self.assertIn("factory retry 3", step.alternative)

    def test_otherwise_the_next_backlog_story(self) -> None:
        facts = _facts(backlog=(_story(1, "started", _run(1, "completed")), _story(2)))
        step = next_step(facts)
        self.assertEqual(step.command, "factory next habits")
        self.assertIn("Story 2", step.why)

    def test_a_finished_backlog_suggests_amending_or_a_new_story(self) -> None:
        step = next_step(_facts(backlog=(_story(1, "started", _run(1, "completed")),)))
        self.assertIn("factory interview habits --amend", step.command)


class LifecycleTests(unittest.TestCase):
    def test_phases_in_order_with_the_current_one_marked(self) -> None:
        facts = _facts(backlog=(_story(1, "started", _run(1, "waiting_human")), _story(2),
                                _story(3)), runs=(_run(1, "waiting_human"),))
        phases = lifecycle(facts)
        self.assertEqual([p.name for p in phases], ["Define", "Plan", "Build", "Release"])
        self.assertEqual([p.state for p in phases], ["done", "done", "now", "todo"])
        self.assertIn("1 assumption", phases[0].detail)
        self.assertIn("3 stories", phases[1].detail)
        self.assertIn("0 of 3 released", phases[3].detail)

    def test_a_fresh_project_is_at_define(self) -> None:
        phases = lifecycle(_facts(has_brief=False, answers=0, has_stack=False))
        self.assertEqual([p.state for p in phases], ["now", "todo", "todo", "todo"])

    def test_everything_released_marks_release_done(self) -> None:
        facts = _facts(backlog=(_story(1, "started", _run(1, "completed")),))
        self.assertEqual(lifecycle(facts)[3].state, "done")
        self.assertIn("1 of 1 released", lifecycle(facts)[3].detail)


if __name__ == "__main__":
    unittest.main()
