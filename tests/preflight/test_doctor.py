"""`factory doctor`: the preflight that runs before any tokens are spent.

Every model probe goes through `factory.preflight.doctor.run_agent`, which these
tests patch — a live opencode call from a test is never acceptable.
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.agent_config import tiers as mt
from factory.agent_config.settings import settings
from factory.preflight import doctor
from factory.workspace import db_path
from factory.workspace.projects import create_project

ALL_TOOLS = {"opencode", "go", "node", "tsc"}


def _which(present: set[str]):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _ok(agent: str, prompt: str, **kwargs) -> AgentResult:
    return AgentResult(agent=agent, output="OK", duration_secs=1.0, returncode=0)


def _by_name(report: doctor.DoctorReport) -> dict[str, doctor.Check]:
    return {c.name: c for c in report.checks}


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pin the tier models so env overrides on the dev machine can't leak in.
        self._saved = {
            k: os.environ.pop(k) for k in list(os.environ) if k.startswith("FACTORY_TIER_")
        }

    def tearDown(self) -> None:
        os.environ.update(self._saved)

    def test_all_green_passes_and_probes_each_distinct_model_once(self) -> None:
        with patch("factory.preflight.doctor.run_agent", side_effect=_ok) as probe:
            report = doctor.run_doctor(which=_which(ALL_TOOLS))
        self.assertTrue(report.passed)
        probed = sorted(call.kwargs["model"] for call in probe.call_args_list)
        self.assertEqual(probed, sorted(set(mt.TIER_DEFAULTS.values())))
        checks = _by_name(report)
        self.assertEqual(checks["opencode"].status, "ok")
        self.assertEqual(checks["model openai/gpt-6-astra"].status, "ok")
        self.assertIn("frontier", checks["model openai/gpt-6-astra"].detail)
        for tool in ("go", "node", "tsc"):
            self.assertEqual(checks[tool].status, "ok")

    def test_probe_uses_a_short_timeout_and_the_repo_cwd(self) -> None:
        with patch("factory.preflight.doctor.run_agent", side_effect=_ok) as probe:
            doctor.run_doctor(which=_which(ALL_TOOLS))
        kwargs = probe.call_args.kwargs
        self.assertEqual(kwargs["timeout"], settings().timeouts.probe)
        # opencode resolves `.opencode/agents` from cwd: the probe must run where
        # that symlink lives, or every probe "fails" with an unknown agent.
        self.assertTrue((doctor.repo_root() / ".opencode" / "agents").exists())
        self.assertEqual(kwargs["cwd"], str(doctor.repo_root()))

    def test_unreachable_model_fails_the_report_with_its_error(self) -> None:
        def probe(agent: str, prompt: str, **kwargs) -> AgentResult:
            if kwargs["model"] == "requesty/claude-sonnet-5-5":
                return AgentResult(
                    agent=agent,
                    output="ERROR: The requested model is not approved for this API key",
                    duration_secs=0.5,
                    returncode=1,
                )
            return _ok(agent, prompt)

        with patch("factory.preflight.doctor.run_agent", side_effect=probe):
            report = doctor.run_doctor(which=_which(ALL_TOOLS))
        self.assertFalse(report.passed)
        bad = _by_name(report)["model requesty/claude-sonnet-5-5"]
        self.assertEqual(bad.status, "fail")
        self.assertIn("not approved", bad.detail)

    def test_success_exit_with_no_output_is_unreachable(self) -> None:
        # opencode can exit 0 having streamed only an error event: an empty answer
        # is not proof the model works.
        silent = AgentResult(agent="x", output="", duration_secs=0.1, returncode=0)
        with patch("factory.preflight.doctor.run_agent", return_value=silent):
            report = doctor.run_doctor(which=_which(ALL_TOOLS))
        self.assertFalse(report.passed)

    def test_env_override_is_what_gets_probed(self) -> None:
        os.environ["FACTORY_TIER_BUILD"] = "openai/override-model"
        try:
            with patch("factory.preflight.doctor.run_agent", side_effect=_ok) as probe:
                doctor.run_doctor(which=_which(ALL_TOOLS))
        finally:
            del os.environ["FACTORY_TIER_BUILD"]
        probed = {call.kwargs["model"] for call in probe.call_args_list}
        self.assertIn("openai/override-model", probed)
        self.assertNotIn("openai/gpt-6.1-sol", probed)

    def test_offline_skips_every_model_probe(self) -> None:
        with patch("factory.preflight.doctor.run_agent") as probe:
            report = doctor.run_doctor(offline=True, which=_which(ALL_TOOLS))
        probe.assert_not_called()
        self.assertTrue(report.passed)
        models = [c for c in report.checks if c.name.startswith("model ")]
        self.assertEqual(len(models), len(set(mt.TIER_DEFAULTS.values())))
        self.assertTrue(all(c.status == "skip" for c in models))

    def test_missing_opencode_fails_without_probing(self) -> None:
        with patch("factory.preflight.doctor.run_agent") as probe:
            report = doctor.run_doctor(which=_which({"go", "node", "tsc"}))
        probe.assert_not_called()
        self.assertFalse(report.passed)
        checks = _by_name(report)
        self.assertEqual(checks["opencode"].status, "fail")
        self.assertEqual(checks["model openai/gpt-6-astra"].status, "fail")

    def test_the_claude_runner_needs_claude_not_opencode_and_probes_its_models(self) -> None:
        with patch.dict(os.environ, {"FACTORY_RUNNER": "claude"}):
            with patch("factory.preflight.doctor.run_agent", side_effect=_ok) as probe:
                report = doctor.run_doctor(which=_which({"claude", "go", "node", "tsc"}))
        self.assertTrue(report.passed)
        checks = _by_name(report)
        self.assertEqual(checks["claude"].status, "ok")
        self.assertNotIn("opencode", checks)
        probed = {call.kwargs["model"] for call in probe.call_args_list}
        self.assertEqual(probed, set(mt.config().claude_tiers.values()))

    def test_missing_toolchains_warn_but_do_not_fail(self) -> None:
        # A missing go/node/tsc only matters for projects in that stack, so it is
        # reported, not fatal.
        with patch("factory.preflight.doctor.run_agent", side_effect=_ok):
            report = doctor.run_doctor(which=_which({"opencode"}))
        self.assertTrue(report.passed)
        checks = _by_name(report)
        for tool in ("go", "node", "tsc"):
            self.assertEqual(checks[tool].status, "warn")


class DoctorWorkspaceTests(unittest.TestCase):
    """The doctor also preflights WHERE a run will write: $FACTORY_HOME, its DB,
    and each product's `.opencode` link. Read-only: the doctor never creates or
    repairs anything. ($FACTORY_HOME is a per-test temp dir, see conftest.)"""

    def _run(self, **kwargs) -> doctor.DoctorReport:
        with patch("factory.preflight.doctor.run_agent") as probe:
            report = doctor.run_doctor(offline=True, which=_which(ALL_TOOLS), **kwargs)
        probe.assert_not_called()
        return report

    def test_fresh_home_is_ok_and_is_not_created(self) -> None:
        home = Path(os.environ["FACTORY_HOME"]) / "not-yet"
        os.environ["FACTORY_HOME"] = str(home)
        report = self._run()
        check = _by_name(report)["workspace"]
        self.assertEqual(check.status, "ok")
        self.assertIn(str(home), check.detail)
        self.assertIn("no factory.db yet", check.detail)
        self.assertTrue(report.passed)
        self.assertFalse(home.exists(), "the doctor must not create $FACTORY_HOME")

    def test_reports_runs_and_projects_in_the_home_db(self) -> None:
        create_project(db_path(), slug="shop", stack="fastapi")
        check = _by_name(self._run())["workspace"]
        self.assertEqual(check.status, "ok")
        self.assertIn("1 project", check.detail)
        self.assertIn("0 runs", check.detail)

    def test_home_that_is_a_file_fails(self) -> None:
        home = Path(os.environ["FACTORY_HOME"]) / "a-file"
        home.write_text("not a directory", encoding="utf-8")
        os.environ["FACTORY_HOME"] = str(home)
        report = self._run()
        self.assertEqual(_by_name(report)["workspace"].status, "fail")
        self.assertFalse(report.passed)

    def test_unreadable_db_fails(self) -> None:
        home = Path(os.environ["FACTORY_HOME"])
        (home / "factory.db").write_bytes(b"this is not a sqlite database at all" * 64)
        report = self._run()
        check = _by_name(report)["workspace"]
        self.assertEqual(check.status, "fail")
        self.assertIn("factory.db", check.detail)
        self.assertFalse(report.passed)

    def test_project_linked_to_this_checkout_is_ok(self) -> None:
        create_project(db_path(), slug="shop", stack="fastapi")
        check = _by_name(self._run())["project shop"]
        self.assertEqual(check.status, "ok")
        self.assertFalse(check.blocking)

    def test_stale_opencode_link_warns_with_the_fix(self) -> None:
        # A product whose `.opencode` points at another checkout (an old worktree,
        # the pre-flatten mvp/) runs THAT agent configuration, not the one
        # `factory evals` just validated — or fails with an unknown agent.
        project = create_project(db_path(), slug="shop", stack="fastapi")
        link = Path(project["repo_path"]) / ".opencode"
        elsewhere = Path(os.environ["FACTORY_HOME"]) / "old-checkout" / ".opencode"
        elsewhere.mkdir(parents=True)
        link.unlink()
        link.symlink_to(elsewhere, target_is_directory=True)
        report = self._run()
        check = _by_name(report)["project shop"]
        self.assertEqual(check.status, "warn")
        self.assertIn("old-checkout", check.detail)
        self.assertIn("ln -sfn", check.detail)
        self.assertTrue(report.passed, "one stale product must not block the others")

    def test_python_product_without_a_venv_warns_with_the_fix(self) -> None:
        # Its tests would run with the FACTORY's interpreter, which lacks the
        # product's dependencies: a failure for the wrong reason.
        create_project(db_path(), slug="shop", stack="fastapi")
        report = self._run()
        check = _by_name(report)["project shop python"]
        self.assertEqual(check.status, "warn")
        self.assertFalse(check.blocking)
        self.assertIn("factory's interpreter", check.detail)
        self.assertIn("uv venv && uv pip install -r requirements.txt", check.detail)
        self.assertTrue(report.passed)

    def test_python_product_with_a_venv_has_no_warning(self) -> None:
        project = create_project(db_path(), slug="shop", stack="fastapi")
        (Path(project["repo_path"]) / ".venv" / "bin").mkdir(parents=True)
        (Path(project["repo_path"]) / ".venv" / "bin" / "python").write_text("")
        self.assertNotIn("project shop python", _by_name(self._run()))

    def test_python_detected_from_requirements_txt(self) -> None:
        project = create_project(db_path(), slug="shop", stack="fastapi")
        repo = Path(project["repo_path"])
        (repo / "project-spec.json").write_text('{"language": "Go"}')
        self.assertNotIn("project shop python", _by_name(self._run()))
        (repo / "requirements.txt").write_text("fastapi\n")
        self.assertEqual(_by_name(self._run())["project shop python"].status, "warn")

    def test_missing_project_repo_warns(self) -> None:
        project = create_project(db_path(), slug="shop", stack="fastapi")
        shutil.rmtree(project["repo_path"])
        check = _by_name(self._run())["project shop"]
        self.assertEqual(check.status, "warn")
        self.assertIn("missing", check.detail)

    def test_legacy_state_left_in_the_checkout_warns(self) -> None:
        # The pre-$FACTORY_HOME layout kept factory.db in the checkout. Left there,
        # the factory starts on an EMPTY ~/.factory and the history looks lost.
        checkout = Path(os.environ["FACTORY_HOME"]) / "checkout"
        (checkout / "mvp").mkdir(parents=True)
        (checkout / "mvp" / "factory.db").write_bytes(b"")
        report = self._run(checkout=checkout)
        check = _by_name(report)["legacy state"]
        self.assertEqual(check.status, "warn")
        self.assertIn("import-legacy", check.detail)
        self.assertIn("mvp", check.detail)
        self.assertTrue(report.passed)

    def test_no_legacy_check_line_when_the_checkout_is_clean(self) -> None:
        checkout = Path(os.environ["FACTORY_HOME"]) / "checkout"
        checkout.mkdir()
        self.assertNotIn("legacy state", _by_name(self._run(checkout=checkout)))


if __name__ == "__main__":
    unittest.main()


class BrokenTierConfigTests(unittest.TestCase):
    """The doctor exists to diagnose a broken setup — it must REPORT a missing or
    invalid tiers.toml as a failed check, not die on it like every other verb did."""

    def setUp(self) -> None:
        from factory.agent_config import location, tiers

        self._tmp = Path(__import__("tempfile").mkdtemp())
        self._env = patch.dict(os.environ, {location.AGENTS_DIR_ENV: str(self._tmp)})
        self._env.start()
        tiers.config.cache_clear()

    def tearDown(self) -> None:
        from factory.agent_config import tiers

        self._env.stop()
        tiers.config.cache_clear()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self) -> doctor.DoctorReport:
        with patch("factory.preflight.doctor.run_agent", side_effect=_ok) as probe:
            report = doctor.run_doctor(which=_which(ALL_TOOLS))
        probe.assert_not_called()
        return report

    def test_a_missing_tiers_file_is_a_failed_check(self) -> None:
        report = self._run()
        self.assertFalse(report.passed)
        check = _by_name(report)["tiers"]
        self.assertEqual(check.status, "fail")
        self.assertIn("tiers.toml", check.detail)

    def test_an_invalid_tiers_file_is_a_failed_check(self) -> None:
        (self._tmp / "tiers.toml").write_text('default_tier = "nope"\n[tiers]\nfast = "a/b"\n[agents]\n')
        check = _by_name(self._run())["tiers"]
        self.assertEqual(check.status, "fail")
        self.assertIn("default_tier", check.detail)


class TierLeverageTests(unittest.TestCase):
    """Escalate-on-retry is the tier policy's whole point for the coder: a failed
    cheap attempt re-runs on a stronger model. If both tiers resolve to the SAME
    model (an env override, or a tiers.toml edit), the retry buys nothing — the
    doctor says so instead of letting the policy silently degrade."""

    def test_an_escalation_onto_the_same_model_is_a_warning(self) -> None:
        with patch.dict(os.environ, {"FACTORY_TIER_SPECIAL": "openai/gpt-6.1-sol"}), \
                patch("factory.preflight.doctor.run_agent", side_effect=_ok):
            report = doctor.run_doctor(offline=True, which=_which(ALL_TOOLS))
        check = _by_name(report)["tier escalation"]
        self.assertEqual(check.status, "warn")
        self.assertFalse(check.blocking)
        self.assertIn("coder-agent", check.detail)
        self.assertTrue(report.passed)

    def test_a_tier_model_without_a_list_price_is_a_warning(self) -> None:
        """The $10-per-story cap prices tokens from agents/tiers.toml [prices]; a model
        without one is spent blind (its calls count as unknown)."""
        with patch.dict(os.environ, {"FACTORY_TIER_BUILD": "openai/unpriced-model"}), \
                patch("factory.preflight.doctor.run_agent", side_effect=_ok):
            report = doctor.run_doctor(offline=True, which=_which(ALL_TOOLS))
        check = _by_name(report)["price list"]
        self.assertEqual(check.status, "warn")
        self.assertIn("openai/unpriced-model", check.detail)
        self.assertTrue(report.passed)

    def test_distinct_models_are_ok(self) -> None:
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("FACTORY_TIER_")}
        try:
            report = doctor.run_doctor(offline=True, which=_which(ALL_TOOLS))
        finally:
            os.environ.update(saved)
        self.assertEqual(_by_name(report)["tier escalation"].status, "ok")
