"""Offline orchestration tests driven by frozen agent outputs (no opencode calls).

Seeds an "original" run's agent_logs from fixture files, then replays the
compiled pipeline against those frozen outputs in a temp working directory and
asserts gate verdicts + final status. This is the regression net for gate/edge
logic and runs entirely without an LLM.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.domain.gates import MAX_CODER_ATTEMPTS
from factory.pipeline import (
    _retry_context_block,
    _reviewer_feedback_block,
    compile_architect_resume_pipeline,
    compile_pipeline,
    route_after_coder,
)
from factory.state import db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "agent_outputs"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


class ReplayHarness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.db_path = self.root / "factory.db"
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_original(self, outputs: dict[str, object]) -> int:
        """Create a story + original run with stored agent outputs. Returns run_id.

        Coder outputs are per-task: replay fetches them by task id (stored in
        stage_type). The "coder-agent" value may be a single string (applied to
        the spec's first task) or a {task_id: output} dict for multi-task specs.
        """
        # The tester gate now always runs on completion; default-seed a passing
        # tester output so completing scenarios don't hit "cannot replay".
        if "tester-agent" not in outputs:
            outputs = {**outputs, "tester-agent": _load("tester_pass.json")}

        spec_text = outputs.get("spec-agent")
        task_ids: list[str] = []
        if isinstance(spec_text, str):
            try:
                task_ids = [t["id"] for t in json.loads(spec_text).get("tasks", [])]
            except (ValueError, KeyError, TypeError):
                task_ids = []

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Seed", "do the thing")
            run_id = db.start_run(conn, "US-0001")
            for agent, value in outputs.items():
                if agent == "coder-agent":
                    if isinstance(value, dict):
                        per_task = value
                    else:
                        slot = task_ids[0] if task_ids else "T-0001"
                        per_task = {slot: value}
                    for tid, out in per_task.items():
                        db.log_agent(conn, run_id, "coder-agent", "seed-prompt", out,
                                     verdict="complete", stage_type=tid)
                else:
                    db.log_agent(conn, run_id, agent, "seed-prompt", str(value), verdict="pass")
        return run_id

    def _replay(
        self, original_run_id: int, project_dir: str | None = None
    ) -> tuple[dict, list[dict]]:
        """Run the pipeline in replay mode; return (final_state, gate_results)."""
        with db.get_db(self.db_path) as conn:
            new_run_id = db.start_run(conn, "US-0001")
        self.new_run_id = new_run_id
        state = {
            "request": "do the thing",
            "story_id": "US-0001",
            "run_id": new_run_id,
            "db_path": str(self.db_path),
            "opencode_cwd": str(self.cwd),
            "replay_run_id": original_run_id,
        }
        if project_dir:
            state["project_dir"] = project_dir
        final: dict = dict(state)
        for event in compile_pipeline().stream(state):
            for _, node_output in event.items():
                final.update(node_output)
        with db.get_db(self.db_path) as conn:
            gates = db.get_run_gates(conn, new_run_id)
        return final, gates


class CleanPassTests(ReplayHarness):
    def test_clean_pass_completes_and_materializes(self) -> None:
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),
            }
        )
        final, gates = self._replay(orig)

        self.assertEqual(final.get("status"), "completed")
        gate_map = {g["gate_name"]: bool(g["passed"]) for g in gates}
        self.assertTrue(gate_map.get("gate-1-spec"))
        self.assertTrue(gate_map.get("gate-2-architect"))
        self.assertTrue(gate_map.get("gate-build"))
        # Code block was materialized into the temp repo
        self.assertTrue((self.cwd / "convert.py").is_file())
        self.assertIn("celsius_to_fahrenheit", (self.cwd / "convert.py").read_text())


class SpecGateFailTests(ReplayHarness):
    def test_open_questions_park_at_checkpoint_1_and_stop(self) -> None:
        orig = self._seed_original(
            {"spec-agent": _load("spec_open_questions.spec.json")}
        )
        final, gates = self._replay(orig)

        # The gate PASSES structurally and parks for the operator's answer; the
        # line still stops. (It used to kill the run and never persist why.)
        self.assertEqual(final.get("status"), "waiting_human")
        gate1 = next(g for g in gates if g["gate_name"] == "gate-1-spec")
        self.assertTrue(bool(gate1["passed"]))
        self.assertTrue(bool(gate1["needs_human"]))
        self.assertTrue(gate1["human_questions"])
        # Architect must never have run
        self.assertNotIn("architect", final)


class ArchitectHumanGateTests(ReplayHarness):
    def test_breaking_change_pauses_for_human_before_coder(self) -> None:
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("architect_breaking.architect.json"),
            }
        )
        final, gates = self._replay(orig)

        self.assertEqual(final.get("status"), "waiting_human")
        gate2 = next(g for g in gates if g["gate_name"] == "gate-2-architect")
        self.assertTrue(bool(gate2["passed"]))
        self.assertTrue(bool(gate2["needs_human"]))
        # Coder must not have run, so no file materialized
        self.assertFalse((self.cwd / "convert.py").exists())


class BuildGateTests(ReplayHarness):
    def test_broken_code_fails_build_gate_despite_complete_verdict(self) -> None:
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("broken_code.coder.json"),
            }
        )
        final, gates = self._replay(orig)

        # Coder claimed "complete", but the written code does not parse.
        self.assertEqual(final.get("status"), "failed")
        gate_map = {g["gate_name"]: bool(g["passed"]) for g in gates}
        self.assertIn("gate-build", gate_map)
        self.assertFalse(gate_map["gate-build"])
        # The broken file was still written (we verify after materialization)
        self.assertTrue((self.cwd / "convert.py").is_file())


class DecisionMemoryTests(ReplayHarness):
    def test_adr_written_and_prior_memory_injected(self) -> None:
        # Project root with rules + a prior ADR from a different story.
        rules = self.root / "PROJECT_RULES.md"
        rules.write_text("- Use only the Python standard library")
        adr_dir = self.root / "docs" / "architecture" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "ADR-US-0009-prior-thing.md").write_text(
            "# ADR-US-0009: Prior thing\n\n## Decision\nWe chose approach Z.\n"
        )

        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),
            }
        )
        final, _ = self._replay(orig, project_dir=str(self.root))

        self.assertEqual(final.get("status"), "completed")
        # 2.1 — an ADR for the current story was persisted.
        current_adrs = list(adr_dir.glob("ADR-US-0001-*.md"))
        self.assertEqual(len(current_adrs), 1)
        self.assertIn("adr_path", final)

        # 2.2 — rules + prior ADR were injected into the agent prompts (logged input).
        with db.get_db(self.db_path) as conn:
            logs = db.get_run_logs(conn, self.new_run_id)
        arch_input = next(l["input_text"] for l in logs if l["agent"] == "architect-agent")
        self.assertIn("Use only the Python standard library", arch_input)
        self.assertIn("ADR-US-0009", arch_input)  # prior decision fed in
        self.assertNotIn("ADR-US-0001", arch_input)  # but not its own (excluded)


class RemediationLoopTests(ReplayHarness):
    def test_build_failure_retries_within_budget_then_gives_up(self) -> None:
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("broken_code.coder.json"),
            }
        )
        final, gates = self._replay(orig)

        # Budget exhausted -> failed, with an explicit give-up.
        self.assertEqual(final.get("status"), "failed")
        self.assertEqual(final.get("next_action"), "give_up")
        self.assertIn("budget", final.get("error", ""))

        with db.get_db(self.db_path) as conn:
            logs = db.get_run_logs(conn, self.new_run_id)
        coder_logs = [l for l in logs if l["agent"] == "coder-agent"]
        build_gates = [g for g in gates if g["gate_name"] == "gate-build"]

        # The coder was re-attempted exactly up to the budget, each build-gated.
        self.assertEqual(len(coder_logs), MAX_CODER_ATTEMPTS)
        self.assertEqual(len(build_gates), MAX_CODER_ATTEMPTS)
        self.assertTrue(all(not g["passed"] for g in build_gates))

        # The retry prompt carried the prior failure findings (3.1).
        self.assertIn("Previous attempt failed", coder_logs[1]["input_text"])


class PerTaskExecutionTests(ReplayHarness):
    def test_tasks_run_individually_in_dependency_order(self) -> None:
        orig = self._seed_original(
            {
                "spec-agent": _load("multitask.spec.json"),
                "architect-agent": _load("multitask.architect.json"),
                "coder-agent": {
                    "T-0001": _load("multitask.coder.T-0001.json"),
                    "T-0002": _load("multitask.coder.T-0002.json"),
                },
            }
        )
        final, gates = self._replay(orig)

        self.assertEqual(final.get("status"), "completed")
        # Both files materialized (each by its own task call)
        self.assertTrue((self.cwd / "calc" / "models.py").is_file())
        self.assertTrue((self.cwd / "calc" / "cli.py").is_file())

        with db.get_db(self.db_path) as conn:
            logs = db.get_run_logs(conn, self.new_run_id)
        coder_logs = [l for l in logs if l["agent"] == "coder-agent"]
        # One coder call per task, in dependency order (T-0001 before T-0002).
        self.assertEqual([l["stage_type"] for l in coder_logs], ["T-0001", "T-0002"])
        # One gate-build per task.
        build_gates = [g for g in gates if g["gate_name"] == "gate-build"]
        self.assertEqual(len(build_gates), 2)
        self.assertTrue(all(g["passed"] for g in build_gates))
        # The second task's prompt was told the first was already implemented.
        self.assertIn("Already implemented", coder_logs[1]["input_text"])
        self.assertIn("T-0001", coder_logs[1]["input_text"])
        self.assertEqual(final.get("tasks_completed"), ["T-0001", "T-0002"])


class GovernanceTests(ReplayHarness):
    def test_out_of_band_write_is_blocked(self) -> None:
        # The repo is a git baseline with a file the coder will NOT declare —
        # simulating an agent that wrote directly, bypassing materialize.
        from factory.workspace.git import git_init

        git_init(self.cwd)
        (self.cwd / "evil.py").write_text("x = 1\n")

        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),  # claims only convert.py
            }
        )
        final, gates = self._replay(orig)

        self.assertEqual(final.get("status"), "blocked")
        self.assertIn("GOVERNANCE", final.get("error", ""))
        self.assertIn("evil.py", final.get("error", ""))
        build = next(g for g in gates if g["gate_name"] == "gate-build")
        self.assertFalse(build["passed"])

    def test_infra_files_do_not_trip_governance(self) -> None:
        # A .opencode symlink/dir in a git repo must NOT count as an out-of-band write.
        from factory.workspace.git import git_init

        git_init(self.cwd)
        (self.cwd / ".opencode").mkdir()
        (self.cwd / ".opencode" / "config").write_text("noise")

        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),
            }
        )
        final, _ = self._replay(orig)
        self.assertEqual(final.get("status"), "completed")  # .opencode ignored


class PerTaskGitRepoTests(ReplayHarness):
    def test_multitask_in_git_repo_completes(self) -> None:
        # Regression: in a git repo, task 2 used to be blocked because the scope
        # check saw task 1's files as "out-of-band". A per-task commit fixes it.
        from factory.workspace.git import git_init

        git_init(self.cwd)
        orig = self._seed_original(
            {
                "spec-agent": _load("multitask.spec.json"),
                "architect-agent": _load("multitask.architect.json"),
                "coder-agent": {
                    "T-0001": _load("multitask.coder.T-0001.json"),
                    "T-0002": _load("multitask.coder.T-0002.json"),
                },
            }
        )
        final, gates = self._replay(orig)
        self.assertEqual(final.get("status"), "completed")
        build = [g for g in gates if g["gate_name"] == "gate-build"]
        self.assertTrue(all(g["passed"] for g in build))  # no governance false-positive


class TesterGateTests(ReplayHarness):
    def test_tester_pass_completes(self) -> None:
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),
                "tester-agent": _load("tester_pass.json"),
            }
        )
        final, gates = self._replay(orig)
        self.assertEqual(final.get("status"), "completed")
        self.assertTrue(any(g["gate_name"] == "gate-test" and g["passed"] for g in gates))

    def test_tester_fail_downgrades_to_failed(self) -> None:
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),
                "tester-agent": _load("tester_fail.json"),
            }
        )
        final, gates = self._replay(orig)
        # Coder finished, but the tester gate blocks release.
        self.assertEqual(final.get("status"), "failed")
        gate_test = next(g for g in gates if g["gate_name"] == "gate-test")
        self.assertFalse(gate_test["passed"])
        # gate-build still passed (code compiles); the failure is the QA gate.
        self.assertTrue(any(g["gate_name"] == "gate-build" and g["passed"] for g in gates))


class RepoAwarenessTests(ReplayHarness):
    def test_architect_sees_existing_repo_interfaces(self) -> None:
        # Brownfield: an existing module is already in the repo before the run.
        (self.cwd / "existing.py").write_text(
            "def legacy_helper(x: int) -> int:\n    return x + 1\n"
        )
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),
            }
        )
        final, _ = self._replay(orig)
        self.assertEqual(final.get("status"), "completed")

        with db.get_db(self.db_path) as conn:
            logs = db.get_run_logs(conn, self.new_run_id)
        arch_input = next(l["input_text"] for l in logs if l["agent"] == "architect-agent")
        self.assertIn("Existing codebase", arch_input)
        self.assertIn("existing.py", arch_input)
        self.assertIn("def legacy_helper(x: int)", arch_input)


class TesterDiffTests(ReplayHarness):
    def test_tester_receives_real_cumulative_git_diff(self) -> None:
        # In a git repo the tester must judge the REAL diff (every task's change),
        # not the last task's self-report.
        from factory.workspace.git import git_init

        git_init(self.cwd)
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),
                "tester-agent": _load("tester_pass.json"),
            }
        )
        final, _ = self._replay(orig)
        self.assertEqual(final.get("status"), "completed")

        with db.get_db(self.db_path) as conn:
            logs = db.get_run_logs(conn, self.new_run_id)
        tester_input = next(l["input_text"] for l in logs if l["agent"] == "tester-agent")
        # Real git-diff markers (unique to git output, absent from the self-report).
        self.assertIn("diff --git", tester_input)
        self.assertIn("convert.py", tester_input)
        self.assertIn("celsius_to_fahrenheit", tester_input)


class RoutingTests(unittest.TestCase):
    def test_route_retry_loops_back_to_coder(self) -> None:
        self.assertEqual(route_after_coder({"next_action": "retry"}), "coder-agent")

    def test_route_complete_goes_to_tester(self) -> None:
        self.assertEqual(route_after_coder({"next_action": "complete"}), "tester-agent")

    def test_route_giveup_ends(self) -> None:
        from langgraph.graph import END

        self.assertEqual(route_after_coder({"next_action": "give_up"}), END)
        self.assertEqual(route_after_coder({}), END)

    def test_retry_block_empty_on_first_attempt(self) -> None:
        self.assertEqual(_retry_context_block({"prior_findings": ["x"]}, 1), "")
        self.assertEqual(_retry_context_block({}, 2), "")

    def test_retry_block_includes_findings_on_later_attempts(self) -> None:
        block = _retry_context_block(
            {"prior_findings": ["py_compile:fail"], "triggered_by": "gate-build"}, 2
        )
        self.assertIn("Previous attempt failed", block)
        self.assertIn("py_compile:fail", block)
        self.assertIn("gate-build", block)

    def test_reviewer_feedback_block_only_on_rejection(self) -> None:
        self.assertEqual(_reviewer_feedback_block({}), "")
        self.assertEqual(
            _reviewer_feedback_block({"prior_findings": ["x"], "triggered_by": "gate-build"}), ""
        )
        block = _reviewer_feedback_block(
            {"prior_findings": ["Use Postgres, not SQLite"], "triggered_by": "architecture-rejected"}
        )
        self.assertIn("Reviewer feedback", block)
        self.assertIn("Use Postgres, not SQLite", block)


class ArchitectResumeTests(ReplayHarness):
    def test_rejection_reenters_architecture_with_feedback(self) -> None:
        orig = self._seed_original(
            {
                "spec-agent": _load("clean_pass.spec.json"),
                "architect-agent": _load("clean_pass.architect.json"),
                "coder-agent": _load("clean_pass.coder.json"),
            }
        )
        # Drive the architecture-resume pipeline directly, in replay mode, as the
        # reject flow would — carrying reviewer feedback into the architect.
        with db.get_db(self.db_path) as conn:
            new_run_id = db.start_run(conn, "US-0001")
        state = {
            "request": "do the thing",
            "story_id": "US-0001",
            "run_id": new_run_id,
            "db_path": str(self.db_path),
            "opencode_cwd": str(self.cwd),
            "replay_run_id": orig,
            "spec": json.loads(_load("clean_pass.spec.json")),
            "status": "running",
            "prior_findings": ["Use a layered design, not a single file"],
            "triggered_by": "architecture-rejected",
        }
        final: dict = dict(state)
        for event in compile_architect_resume_pipeline().stream(state):
            for _, out in event.items():
                final.update(out)

        self.assertEqual(final.get("status"), "completed")
        with db.get_db(self.db_path) as conn:
            logs = db.get_run_logs(conn, new_run_id)
        arch_input = next(l["input_text"] for l in logs if l["agent"] == "architect-agent")
        self.assertIn("Reviewer feedback", arch_input)
        self.assertIn("Use a layered design, not a single file", arch_input)


class MissingFixtureTests(ReplayHarness):
    def test_replay_without_stored_output_raises(self) -> None:
        orig = self._seed_original({"spec-agent": _load("clean_pass.spec.json")})
        # Make spec pass so the pipeline reaches architect-agent, which has no
        # stored output -> replay must raise rather than silently call opencode.
        final, _ = self._replay(orig)
        self.assertEqual(final.get("status"), "failed")
        self.assertIn("Cannot replay", final.get("error", ""))


if __name__ == "__main__":
    unittest.main()
