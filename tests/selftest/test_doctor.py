"""`factory doctor`: the preflight that runs before any tokens are spent.

Every model probe goes through `factory.selftest.doctor.run_agent`, which these
tests patch — a live opencode call from a test is never acceptable.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from factory.adapters.opencode import AgentResult
from factory.selftest import doctor

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
            k: os.environ.pop(k)
            for k in ("FACTORY_TIER_FRONTIER", "FACTORY_TIER_STANDARD", "FACTORY_TIER_FAST")
            if k in os.environ
        }

    def tearDown(self) -> None:
        os.environ.update(self._saved)

    def test_all_green_passes_and_probes_each_distinct_model_once(self) -> None:
        with patch("factory.selftest.doctor.run_agent", side_effect=_ok) as probe:
            report = doctor.run_doctor(which=_which(ALL_TOOLS))
        self.assertTrue(report.passed)
        probed = sorted(call.kwargs["model"] for call in probe.call_args_list)
        self.assertEqual(probed, ["openai/gpt-5.5", "openai/gpt-5.5-fast"])
        checks = _by_name(report)
        self.assertEqual(checks["opencode"].status, "ok")
        self.assertEqual(checks["model openai/gpt-5.5"].status, "ok")
        self.assertIn("frontier", checks["model openai/gpt-5.5"].detail)
        self.assertIn("standard", checks["model openai/gpt-5.5"].detail)
        for tool in ("go", "node", "tsc"):
            self.assertEqual(checks[tool].status, "ok")

    def test_probe_uses_a_short_timeout_and_the_repo_cwd(self) -> None:
        with patch("factory.selftest.doctor.run_agent", side_effect=_ok) as probe:
            doctor.run_doctor(which=_which(ALL_TOOLS))
        kwargs = probe.call_args.kwargs
        self.assertEqual(kwargs["timeout"], doctor.PROBE_TIMEOUT_SECS)
        # opencode resolves `.opencode/agents` from cwd: the probe must run where
        # that symlink lives, or every probe "fails" with an unknown agent.
        self.assertTrue((doctor.repo_root() / ".opencode" / "agents").exists())
        self.assertEqual(kwargs["cwd"], str(doctor.repo_root()))

    def test_unreachable_model_fails_the_report_with_its_error(self) -> None:
        def probe(agent: str, prompt: str, **kwargs) -> AgentResult:
            if kwargs["model"] == "openai/gpt-5.5-fast":
                return AgentResult(
                    agent=agent,
                    output="ERROR: model gpt-5.5-fast is not supported",
                    duration_secs=0.5,
                    returncode=1,
                )
            return _ok(agent, prompt)

        with patch("factory.selftest.doctor.run_agent", side_effect=probe):
            report = doctor.run_doctor(which=_which(ALL_TOOLS))
        self.assertFalse(report.passed)
        bad = _by_name(report)["model openai/gpt-5.5-fast"]
        self.assertEqual(bad.status, "fail")
        self.assertIn("not supported", bad.detail)

    def test_success_exit_with_no_output_is_unreachable(self) -> None:
        # opencode can exit 0 having streamed only an error event: an empty answer
        # is not proof the model works.
        silent = AgentResult(agent="x", output="", duration_secs=0.1, returncode=0)
        with patch("factory.selftest.doctor.run_agent", return_value=silent):
            report = doctor.run_doctor(which=_which(ALL_TOOLS))
        self.assertFalse(report.passed)

    def test_env_override_is_what_gets_probed(self) -> None:
        os.environ["FACTORY_TIER_FAST"] = "openai/override-model"
        try:
            with patch("factory.selftest.doctor.run_agent", side_effect=_ok) as probe:
                doctor.run_doctor(which=_which(ALL_TOOLS))
        finally:
            del os.environ["FACTORY_TIER_FAST"]
        probed = {call.kwargs["model"] for call in probe.call_args_list}
        self.assertIn("openai/override-model", probed)
        self.assertNotIn("openai/gpt-5.5-fast", probed)

    def test_offline_skips_every_model_probe(self) -> None:
        with patch("factory.selftest.doctor.run_agent") as probe:
            report = doctor.run_doctor(offline=True, which=_which(ALL_TOOLS))
        probe.assert_not_called()
        self.assertTrue(report.passed)
        models = [c for c in report.checks if c.name.startswith("model ")]
        self.assertEqual(len(models), 2)
        self.assertTrue(all(c.status == "skip" for c in models))

    def test_missing_opencode_fails_without_probing(self) -> None:
        with patch("factory.selftest.doctor.run_agent") as probe:
            report = doctor.run_doctor(which=_which({"go", "node", "tsc"}))
        probe.assert_not_called()
        self.assertFalse(report.passed)
        checks = _by_name(report)
        self.assertEqual(checks["opencode"].status, "fail")
        self.assertEqual(checks["model openai/gpt-5.5"].status, "fail")

    def test_missing_toolchains_warn_but_do_not_fail(self) -> None:
        # A missing go/node/tsc only matters for projects in that stack, so it is
        # reported, not fatal.
        with patch("factory.selftest.doctor.run_agent", side_effect=_ok):
            report = doctor.run_doctor(which=_which({"opencode"}))
        self.assertTrue(report.passed)
        checks = _by_name(report)
        for tool in ("go", "node", "tsc"):
            self.assertEqual(checks[tool].status, "warn")


if __name__ == "__main__":
    unittest.main()
