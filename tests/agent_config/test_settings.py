"""factory.toml: the operator's settings file (budget, timeouts, feature switches).

Precedence is env var > factory.toml > built-in default, and a missing file is fine
(a wheel install has no checkout around it). A malformed file is an error, never a
silent fallback: a typo'd budget key must not quietly leave the cap at its default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.agent_config import settings as st
from factory.domain import gates


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "factory.toml"
    path.write_text(body)
    return path

    def test_runner_defaults_to_opencode_and_is_chosen_by_file_or_env(self) -> None:
        self.assertEqual(settings().runner.agents, "opencode")
        self.path.write_text('[runner]\nagents = "claude"\n')
        self.assertEqual(settings().runner.agents, "claude")
        with patch.dict("os.environ", {"FACTORY_RUNNER": "opencode"}):
            self.assertEqual(settings().runner.agents, "opencode")

    def test_an_unknown_runner_is_refused(self) -> None:
        self.path.write_text('[runner]\nagents = "codex"\n')
        with self.assertRaises(ValueError):
            settings()


def test_missing_file_yields_the_built_in_defaults(tmp_path: Path) -> None:
    s = st.load_settings(tmp_path / "nope.toml", environ={})
    assert s.budget.max_story_cost_usd == gates.MAX_STORY_COST_USD
    assert s.budget.max_coder_attempts == gates.MAX_CODER_ATTEMPTS
    assert s.budget.max_tester_remediations == gates.MAX_TESTER_REMEDIATIONS
    assert s.budget.max_rearchitect_loops == gates.MAX_REARCHITECT_LOOPS
    assert s.budget.max_boundary_redesigns == gates.MAX_BOUNDARY_REDESIGNS
    assert (s.timeouts.command, s.timeouts.test, s.timeouts.build) == (60, 180, 180)
    assert s.timeouts.agent == 600
    assert s.features.run_tests is False and s.features.notify is False


def test_file_values_override_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "[budget]\nmax_story_cost_usd = 25.5\nmax_coder_attempts = 3\n"
                            "[timeouts]\ntest = 300\n[features]\nrun_tests = true\n")
    s = st.load_settings(path, environ={})
    assert s.budget.max_story_cost_usd == 25.5
    assert s.budget.max_coder_attempts == 3
    assert s.timeouts.test == 300 and s.timeouts.build == 180  # untouched keys keep defaults
    assert s.features.run_tests is True


def test_env_beats_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "[timeouts]\nagent = 900\n[features]\nrun_tests = false\nnotify = false\n"
    )
    env = {"FACTORY_AGENT_TIMEOUT": "120", "FACTORY_RUN_TESTS": "yes", "FACTORY_NOTIFY": "1"}
    s = st.load_settings(path, environ=env)
    assert s.timeouts.agent == 120
    assert s.features.run_tests is True and s.features.notify is True


def test_env_false_beats_file_true(tmp_path: Path) -> None:
    path = _write(tmp_path, "[features]\nrun_tests = true\n")
    s = st.load_settings(path, environ={"FACTORY_RUN_TESTS": "0"})
    assert s.features.run_tests is False


def test_unparseable_agent_timeout_env_falls_back_to_the_file(tmp_path: Path) -> None:
    # Pre-existing behaviour of FACTORY_AGENT_TIMEOUT: garbage never crashes a run.
    path = _write(tmp_path, "[timeouts]\nagent = 900\n")
    assert st.load_settings(path, environ={"FACTORY_AGENT_TIMEOUT": "abc"}).timeouts.agent == 900


@pytest.mark.parametrize("body", [
    "[budget]\nmax_story_cost = 5\n",            # typo'd key
    "[bogus]\nx = 1\n",                          # unknown section
    "[budget]\nmax_coder_attempts = 0\n",        # must be positive
    "[budget]\nmax_story_cost_usd = 'ten'\n",    # wrong type
    "[timeouts]\ntest = -1\n",
    "[features]\nrun_tests = 'yes'\n",           # bool means bool in a file
    "[budget\n",                                 # not TOML
])
def test_malformed_file_is_an_error(tmp_path: Path, body: str) -> None:
    with pytest.raises(st.SettingsError):
        st.load_settings(_write(tmp_path, body), environ={})


def test_settings_path_honours_factory_config_env(tmp_path: Path) -> None:
    other = _write(tmp_path, "")
    assert st.settings_path({"FACTORY_CONFIG": str(other)}) == other.resolve()


def test_shipped_factory_toml_matches_the_defaults() -> None:
    # The checked-in file documents every knob; it must not silently diverge from the code.
    shipped = st.settings_path({})
    assert shipped.is_file()
    defaults = st.load_settings(Path("/nonexistent"), environ={})
    assert st.load_settings(shipped, environ={}) == defaults


def test_consumers_follow_factory_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.adapters import notify, opencode
    from factory.pipeline.prompts.blocks import retry_context_block
    from factory.verification import base

    path = _write(tmp_path, "[timeouts]\ncommand = 7\ntest = 8\nbuild = 9\nagent = 11\n"
                            "[features]\nrun_tests = true\nnotify = true\n[budget]\nmax_coder_attempts = 5\n")
    monkeypatch.setenv("FACTORY_CONFIG", str(path))
    for var in ("FACTORY_AGENT_TIMEOUT", "FACTORY_RUN_TESTS", "FACTORY_NOTIFY"):
        monkeypatch.delenv(var, raising=False)
    assert (base.command_timeout(), base.suite_timeout(), base.build_timeout()) == (7, 8, 9)
    assert opencode._default_timeout() == 11
    assert base.tests_enabled() is True
    assert notify.enabled() is True
    block = retry_context_block({"prior_findings": ["boom"], "triggered_by": "gate-build"}, 2)
    assert "attempt 2 of 5" in block
