"""The eval harness — regression testing for the AGENT CONFIGURATION.

The factory is an agent configuration (`agents/*.md` + `domain/gates.py` +
`agent_config/tiers.py` + prompt assembly). These tests pin the harness that catches
drift in it, offline and for free.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.selftest import evals


class ConfigCheckTests(unittest.TestCase):
    """Deterministic invariants over the real agent definitions in this repo."""

    def test_config_checks_all_pass_for_the_real_repo(self) -> None:
        results = evals.config_checks()
        failures = [f"{r.name}: {r.detail}" for r in results if not r.passed]
        self.assertEqual(failures, [], "agent configuration drifted: " + "; ".join(failures))

    def test_every_registered_agent_is_checked(self) -> None:
        from factory.agent_config.tiers import AGENT_TIERS

        names = [r.name for r in evals.config_checks()]
        for agent in AGENT_TIERS:
            self.assertTrue(
                any(agent in n for n in names), f"no config check covers {agent}"
            )

    def test_tools_must_be_disabled_is_actually_enforced(self) -> None:
        """The governance invariant: an agent that can write files bypasses
        materialize + the out-of-band-write gate. Flipping it must FAIL evals."""
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "agents"
            agents.mkdir()
            src = evals.default_agents_dir()
            for md in src.glob("*.md"):
                (agents / md.name).write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
            coder = agents / "coder-agent.md"
            coder.write_text(
                coder.read_text(encoding="utf-8").replace("write: false", "write: true"),
                encoding="utf-8",
            )
            results = evals.config_checks(agents_dir=agents)
            offending = [r for r in results if r.name == "agent-tools-disabled:coder-agent"]
            self.assertEqual(len(offending), 1)
            self.assertFalse(offending[0].passed)
            self.assertIn("write", offending[0].detail)

    def test_output_contract_drift_is_caught(self) -> None:
        """The JSON example in an agent .md is the contract shown to the model.
        A key the Pydantic model doesn't have is silently dropped by the code —
        so evals must fail on it."""
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "agents"
            agents.mkdir()
            for md in evals.default_agents_dir().glob("*.md"):
                (agents / md.name).write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
            tester = agents / "tester-agent.md"
            tester.write_text(
                tester.read_text(encoding="utf-8").replace(
                    '"overall": "pass | warn | fail",',
                    '"overall": "pass | warn | fail",\n  "invented_field": "oops",',
                ),
                encoding="utf-8",
            )
            results = evals.config_checks(agents_dir=agents)
            offending = [r for r in results if r.name == "agent-output-contract:tester-agent"]
            self.assertEqual(len(offending), 1)
            self.assertFalse(offending[0].passed)
            self.assertIn("invented_field", offending[0].detail)


class OpencodeAgentsDirTests(unittest.TestCase):
    """What opencode LOADS is agent configuration too.

    Found by review: opencode scans ``.opencode/{agent,agents}/**/*.md`` with
    symlinks followed, so with ``.opencode/agents -> ../agents`` the review policy
    (``agents/policies/REVIEW.md``) registered as a fifth agent, "policies/REVIEW",
    with opencode's default permissions — write/edit/bash NOT disabled. The
    per-agent checks only ever looked at the four agents they knew by name.
    """

    def _opencode_dir(self, root: Path, agents: Path) -> Path:
        opencode = root / ".opencode"
        opencode.mkdir()
        (opencode / "agents").symlink_to(agents, target_is_directory=True)
        return opencode

    def _copy_agents(self, root: Path) -> Path:
        agents = root / "agents"
        agents.mkdir()
        for md in evals.default_agents_dir().glob("*.md"):
            (agents / md.name).write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
        return agents

    def _check(self, results: list) -> object:
        found = [r for r in results if r.name == "opencode-loads-exactly-the-agents"]
        self.assertEqual(len(found), 1)
        return found[0]

    def test_the_real_checkout_exposes_exactly_the_four_agents(self) -> None:
        self.assertTrue(self._check(evals.config_checks()).passed)

    def test_a_markdown_file_beside_the_agents_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = self._copy_agents(root)
            (agents / "policies").mkdir()
            (agents / "policies" / "REVIEW.md").write_text("# Review Policy\n")
            opencode = self._opencode_dir(root, agents)
            check = self._check(evals.config_checks(agents_dir=agents, opencode_dir=opencode))
        self.assertFalse(check.passed)
        self.assertIn("policies/REVIEW", check.detail)

    def test_a_missing_agent_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = self._copy_agents(root)
            opencode = root / ".opencode"
            (opencode / "agents").mkdir(parents=True)
            (opencode / "agents" / "coder-agent.md").symlink_to(agents / "coder-agent.md")
            check = self._check(evals.config_checks(agents_dir=agents, opencode_dir=opencode))
        self.assertFalse(check.passed)
        self.assertIn("spec-agent", check.detail)


class ReplayCaseTests(unittest.TestCase):
    """Behavioural cases: frozen agent outputs driven through the real pipeline."""

    def test_shipped_corpus_is_non_empty_and_well_formed(self) -> None:
        cases = evals.load_cases()
        self.assertGreaterEqual(len(cases), 5, "the eval corpus is too thin to be a net")
        for case in cases:
            self.assertTrue(case.name)
            self.assertTrue(case.description)
            self.assertTrue(case.agent_outputs, f"{case.name} has no frozen outputs")
            self.assertTrue(case.expect_status, f"{case.name} asserts no expected status")

    def test_every_shipped_case_behaves_as_expected(self) -> None:
        results = [evals.run_case(c) for c in evals.load_cases()]
        failures = [f"{r.name}: expected {r.expected}, got {r.actual}" for r in results if not r.passed]
        self.assertEqual(failures, [], "eval corpus drifted: " + "; ".join(failures))

    def test_a_case_with_a_wrong_expectation_fails(self) -> None:
        """The harness must be able to fail — a case asserting the wrong outcome
        is the control that proves it isn't vacuously green."""
        case = evals.load_cases()[0]
        broken = evals.EvalCase(
            name=case.name + "-inverted",
            description="control: deliberately wrong expectation",
            agent_outputs=case.agent_outputs,
            expect_status="this-status-cannot-happen",
        )
        self.assertFalse(evals.run_case(broken).passed)

    def test_gate_expectations_are_checked(self) -> None:
        case = evals.load_cases()[0]
        broken = evals.EvalCase(
            name=case.name + "-badgate",
            description="control: asserts a gate that cannot pass",
            agent_outputs=case.agent_outputs,
            expect_status=case.expect_status,
            expect_gates={"gate-that-does-not-exist": True},
        )
        result = evals.run_case(broken)
        self.assertFalse(result.passed)
        self.assertIn("gate-that-does-not-exist", result.detail)


class ReportTests(unittest.TestCase):
    def test_report_computes_pass_rate_and_gate_verdict(self) -> None:
        ok = evals.EvalResult("a", "config", True)
        bad = evals.EvalResult("b", "config", False, detail="boom")
        self.assertEqual(evals.EvalReport([ok, ok]).pass_rate, 1.0)
        self.assertEqual(evals.EvalReport([ok, bad]).pass_rate, 0.5)
        self.assertTrue(evals.EvalReport([ok, ok]).passed)
        self.assertFalse(evals.EvalReport([ok, bad]).passed)
        self.assertEqual([r.name for r in evals.EvalReport([ok, bad]).failures], ["b"])

    def test_empty_report_does_not_pass(self) -> None:
        """A vacuous suite must never report green — that is how an eval gate rots."""
        self.assertFalse(evals.EvalReport([]).passed)

    def test_markdown_report_names_failures(self) -> None:
        md = evals.render_markdown(
            evals.EvalReport(
                [evals.EvalResult("good", "config", True),
                 evals.EvalResult("bad", "replay", False, detail="why it broke")]
            )
        )
        self.assertIn("Agent Configuration Eval Report", md)
        self.assertIn("bad", md)
        self.assertIn("why it broke", md)
        self.assertIn("50", md)  # pass rate percentage

    def test_run_all_covers_config_and_replay(self) -> None:
        report = evals.run_all()
        kinds = {r.kind for r in report.results}
        self.assertIn("config", kinds)
        self.assertIn("replay", kinds)
        self.assertTrue(report.passed, [f"{r.name}: {r.detail}" for r in report.failures])


class CaptureTests(unittest.TestCase):
    """Every real run (and every incident) must be capturable as a permanent case."""

    def test_capture_writes_a_runnable_case_from_a_real_run(self) -> None:
        from factory.state import db

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "factory.db"
            db.init_db(db_path)
            spec = json.dumps({
                "title": "Captured", "problem": "p", "why": "w",
                "acceptance_criteria": ["ac one", "ac two"],
                "tasks": [{"id": "T-1", "title": "t", "purpose": "p",
                           "scope": [], "completion_evidence": "done"}],
                "verdict": "pass",
            })
            arch = json.dumps({"verdict": "pass", "architecture_notes": "n",
                               "modules_affected": ["c.py"]})
            coder = json.dumps({"verdict": "complete", "code_blocks": [
                {"path": "c.py", "content": "def f():\n    return 1\n", "action": "create"}]})
            tester = json.dumps({"overall": "pass", "qa_verdict": "pass",
                                 "ac_coverage": ["ac one", "ac two"],
                                 "security_verdict": "pass", "highest_severity": "none",
                                 "performance_verdict": "pass", "summary": "ok"})
            with db.get_db(db_path) as conn:
                db.create_story(conn, "US-0001", "Captured", "do it")
                rid = db.start_run(conn, "US-0001")
                db.log_agent(conn, rid, "spec-agent", "in", spec, verdict="pass")
                db.log_agent(conn, rid, "architect-agent", "in", arch, verdict="pass")
                db.log_agent(conn, rid, "coder-agent", "in", coder,
                             verdict="complete", stage_type="T-1")
                db.log_agent(conn, rid, "tester-agent", "in", tester, verdict="pass")
                db.finish_run(conn, rid, "completed")

            cases_dir = root / "cases"
            path = evals.capture_case(
                db_path, rid, "captured-run", description="from a real run",
                cases_dir=cases_dir,
            )
            self.assertTrue(path.is_file())

            loaded = evals.load_cases(cases_dir=cases_dir)
            self.assertEqual(len(loaded), 1)
            case = loaded[0]
            self.assertEqual(case.name, "captured-run")
            self.assertEqual(case.expect_status, "completed")
            self.assertEqual(case.source_run_id, rid)
            # Per-task coder outputs must be keyed by task id so replay picks the
            # right slot — otherwise a multi-task run captures unrunnably.
            self.assertEqual(set(case.agent_outputs["coder-agent"]), {"T-1"})
            # And the captured case must actually reproduce.
            self.assertTrue(evals.run_case(case).passed, evals.run_case(case).detail)

    def test_capture_refuses_an_unknown_run(self) -> None:
        from factory.state import db

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "factory.db"
            db.init_db(db_path)
            with self.assertRaises(ValueError):
                evals.capture_case(db_path, 999, "nope", cases_dir=Path(tmp) / "cases")


if __name__ == "__main__":
    unittest.main()
