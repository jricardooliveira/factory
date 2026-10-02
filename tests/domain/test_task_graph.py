"""A story's task graph must be executable before anyone designs it.

`order_tasks` is deliberately forgiving — a dependency on an unknown id is
ignored and a cycle is appended in original order — so on bad data the coder
implemented a task BEFORE the task it depends on, and nothing recorded it.
`dependency_problems` names those defects so gate-1 can reject the story while
it is still cheap (before the architect is paid for).
"""

from __future__ import annotations

import unittest

from factory.domain import gates
from factory.domain.contracts import SpecOutput, TaskDef
from factory.domain.task_order import dependency_problems


def _t(tid: str, *deps: str) -> TaskDef:
    return TaskDef(id=tid, title=tid, purpose=f"do {tid}", depends_on=list(deps))


class DependencyProblemsTests(unittest.TestCase):
    def test_a_clean_graph_has_no_problems(self) -> None:
        self.assertEqual(dependency_problems([_t("T-1"), _t("T-2", "T-1")]), [])

    def test_a_dependency_on_an_unknown_task_is_named(self) -> None:
        problems = dependency_problems([_t("T-1"), _t("T-2", "T-9")])
        self.assertEqual(len(problems), 1)
        self.assertIn("T-2", problems[0])
        self.assertIn("T-9", problems[0])

    def test_a_task_depending_on_itself_is_named(self) -> None:
        problems = dependency_problems([_t("T-1", "T-1")])
        self.assertEqual(len(problems), 1)
        self.assertIn("itself", problems[0])

    def test_a_cycle_names_every_task_in_it(self) -> None:
        problems = dependency_problems([_t("T-1", "T-3"), _t("T-2", "T-1"), _t("T-3", "T-2"),
                                        _t("T-4")])
        self.assertEqual(len(problems), 1)
        for tid in ("T-1", "T-2", "T-3"):
            self.assertIn(tid, problems[0])
        self.assertNotIn("T-4", problems[0])

    def test_duplicate_task_ids_are_named(self) -> None:
        problems = dependency_problems([_t("T-1"), _t("T-1")])
        self.assertTrue(any("T-1" in p and "duplicate" in p for p in problems))


class GateOneRejectsAnUnexecutableTaskGraphTests(unittest.TestCase):
    def _spec(self, tasks: list[dict]) -> SpecOutput:
        return SpecOutput.model_validate({
            "title": "t", "problem": "p", "why": "w",
            "acceptance_criteria": ["a one", "a two"], "tasks": tasks,
        })

    def test_a_cycle_fails_the_story(self) -> None:
        spec = self._spec([
            {"id": "T-1", "title": "a", "purpose": "a", "depends_on": ["T-2"]},
            {"id": "T-2", "title": "b", "purpose": "b", "depends_on": ["T-1"]},
        ])
        result = gates.gate_after_spec(spec)
        self.assertFalse(result.passed)
        self.assertIn("cycle", result.reason)

    def test_a_dangling_dependency_fails_the_story_even_with_questions(self) -> None:
        # Structural defects are never laundered into a checkpoint.
        spec = self._spec([{"id": "T-1", "title": "a", "purpose": "a", "depends_on": ["T-0"]}])
        spec.questions = ["which database?"]
        result = gates.gate_after_spec(spec)
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)


if __name__ == "__main__":
    unittest.main()
