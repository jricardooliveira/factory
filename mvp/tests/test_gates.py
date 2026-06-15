"""Tests for the deterministic gate logic (gate_after_spec, gate_after_architect)."""

from __future__ import annotations

import unittest

from factory.gates import (
    MAX_MODULES_PER_STORY,
    MAX_TASKS_PER_STORY,
    gate_after_architect,
    gate_after_spec,
    gate_after_tester,
)
from factory.models import ArchitectOutput, SpecOutput, TaskDef, TesterOutput


def _spec(**kw) -> SpecOutput:
    base = dict(
        title="A story", problem="p", why="w",
        acceptance_criteria=["ac1", "ac2"],
        tasks=[TaskDef(id="T-1", title="t", purpose="do")],
        verdict="pass", questions=[],
    )
    base.update(kw)
    return SpecOutput(**base)


def _arch(**kw) -> ArchitectOutput:
    base = dict(verdict="pass", architecture_notes="notes", modules_affected=["a.py"])
    base.update(kw)
    return ArchitectOutput(**base)


class GateAfterSpecTests(unittest.TestCase):
    def test_clean_spec_passes(self) -> None:
        self.assertTrue(gate_after_spec(_spec()).passed)

    def test_non_pass_verdict_fails(self) -> None:
        r = gate_after_spec(_spec(verdict="fail"))
        self.assertFalse(r.passed)
        self.assertIn("verdict", r.reason)

    def test_too_few_acceptance_criteria_fails(self) -> None:
        self.assertFalse(gate_after_spec(_spec(acceptance_criteria=["only one"])).passed)

    def test_open_questions_block(self) -> None:
        r = gate_after_spec(_spec(questions=["which auth?"]))
        self.assertFalse(r.passed)
        self.assertIn("question", r.reason.lower())

    def test_too_many_tasks_fails(self) -> None:
        many = [TaskDef(id=f"T-{i}", title="t", purpose="p") for i in range(MAX_TASKS_PER_STORY + 1)]
        self.assertFalse(gate_after_spec(_spec(tasks=many)).passed)

    def test_no_tasks_fails(self) -> None:
        self.assertFalse(gate_after_spec(_spec(tasks=[])).passed)


class GateAfterArchitectTests(unittest.TestCase):
    def test_clean_architecture_passes_without_human(self) -> None:
        r = gate_after_architect(_arch())
        self.assertTrue(r.passed)
        self.assertFalse(r.needs_human)

    def test_fail_verdict_fails(self) -> None:
        self.assertFalse(gate_after_architect(_arch(verdict="fail")).passed)

    def test_missing_modules_fails(self) -> None:
        self.assertFalse(gate_after_architect(_arch(modules_affected=[])).passed)

    def test_too_many_modules_fails(self) -> None:
        mods = [f"m{i}.py" for i in range(MAX_MODULES_PER_STORY + 1)]
        self.assertFalse(gate_after_architect(_arch(modules_affected=mods)).passed)

    def test_breaking_changes_request_human(self) -> None:
        r = gate_after_architect(_arch(breaking_changes=["GET /x changed shape"]))
        self.assertTrue(r.passed)
        self.assertTrue(r.needs_human)
        self.assertTrue(any("BREAKING" in q for q in r.human_questions))

    def test_external_deps_request_human(self) -> None:
        r = gate_after_architect(_arch(external_dependencies=["Stripe key"]))
        self.assertTrue(r.needs_human)
        self.assertTrue(any("EXTERNAL" in q for q in r.human_questions))

    def test_sensitivity_requests_human(self) -> None:
        r = gate_after_architect(_arch(sensitivity=["pii", "security"]))
        self.assertTrue(r.needs_human)
        self.assertTrue(any("SENSITIVE" in q for q in r.human_questions))


class GateAfterTesterTests(unittest.TestCase):
    def test_clean_tester_passes(self) -> None:
        self.assertTrue(gate_after_tester(TesterOutput(ac_coverage=["a", "b"])).passed)

    def test_overall_fail_fails(self) -> None:
        self.assertFalse(gate_after_tester(TesterOutput(overall="fail")).passed)

    def test_qa_fail_fails(self) -> None:
        r = gate_after_tester(TesterOutput(qa_verdict="fail", missing_coverage=["x"]))
        self.assertFalse(r.passed)
        self.assertIn("QA", r.reason)

    def test_high_security_fails(self) -> None:
        r = gate_after_tester(TesterOutput(highest_severity="high", security_findings=["sqli"]))
        self.assertFalse(r.passed)

    def test_critical_security_fails(self) -> None:
        self.assertFalse(gate_after_tester(TesterOutput(security_verdict="fail")).passed)

    def test_performance_fail_fails(self) -> None:
        self.assertFalse(gate_after_tester(TesterOutput(performance_verdict="fail")).passed)

    def test_low_severity_warning_still_passes(self) -> None:
        self.assertTrue(gate_after_tester(TesterOutput(highest_severity="low")).passed)


if __name__ == "__main__":
    unittest.main()
