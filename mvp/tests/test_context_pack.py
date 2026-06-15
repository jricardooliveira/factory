"""Tests for task ordering and per-task context packs."""

from __future__ import annotations

import unittest

from factory.context_pack import build_task_pack, order_tasks
from factory.models import SpecOutput, TaskDef


def _task(tid: str, deps: list[str] | None = None, **kw) -> TaskDef:
    return TaskDef(
        id=tid, title=kw.get("title", tid), purpose=kw.get("purpose", "do " + tid),
        scope=kw.get("scope", []), completion_evidence=kw.get("ce", ""),
        depends_on=deps or [],
    )


class OrderTasksTests(unittest.TestCase):
    def test_no_deps_preserves_order(self) -> None:
        tasks = [_task("A"), _task("B"), _task("C")]
        self.assertEqual([t.id for t in order_tasks(tasks)], ["A", "B", "C"])

    def test_respects_dependencies(self) -> None:
        # Listed out of order: C depends on B, B depends on A.
        tasks = [_task("C", ["B"]), _task("A"), _task("B", ["A"])]
        self.assertEqual([t.id for t in order_tasks(tasks)], ["A", "B", "C"])

    def test_unknown_dependency_is_ignored(self) -> None:
        tasks = [_task("A", ["ghost"]), _task("B")]
        # Does not raise; both tasks still returned.
        self.assertEqual(sorted(t.id for t in order_tasks(tasks)), ["A", "B"])

    def test_cycle_does_not_drop_tasks(self) -> None:
        tasks = [_task("A", ["B"]), _task("B", ["A"])]
        out = order_tasks(tasks)
        self.assertEqual(sorted(t.id for t in out), ["A", "B"])  # all preserved


class BuildTaskPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SpecOutput(
            title="X", problem="the problem", why="the why",
            acceptance_criteria=["ac one", "ac two"],
            tasks=[_task("T-0001", scope=["a.py"], ce="a works")],
        )

    def test_pack_scopes_to_single_task(self) -> None:
        pack = build_task_pack(
            self.spec.tasks[0], self.spec, {"verdict": "pass"},
            position=(1, 3),
        )
        self.assertIn("Current task (1/3): T-0001", pack)
        self.assertIn("Implement ONLY this task", pack)
        self.assertIn("a.py", pack)            # allowed scope
        self.assertIn("a works", pack)         # done-when
        self.assertIn("the problem", pack)     # story context
        self.assertIn("ac one", pack)          # acceptance criteria

    def test_pack_lists_completed_tasks(self) -> None:
        pack = build_task_pack(
            self.spec.tasks[0], self.spec, {"verdict": "pass"},
            completed=["T-0000"],
        )
        self.assertIn("Already implemented", pack)
        self.assertIn("T-0000", pack)

    def test_pack_omits_completed_block_when_none(self) -> None:
        pack = build_task_pack(self.spec.tasks[0], self.spec, {"verdict": "pass"})
        self.assertNotIn("Already implemented", pack)


if __name__ == "__main__":
    unittest.main()
