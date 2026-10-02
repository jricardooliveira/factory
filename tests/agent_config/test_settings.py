"""factory.toml settings: env > file > default, read on every call."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.agent_config.settings import SETTINGS_ENV, settings
from factory.domain.gates import MAX_STORY_COST_USD


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "factory.toml"
        env = patch.dict("os.environ", {SETTINGS_ENV: str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_defaults_without_a_file(self) -> None:
        s = settings()
        self.assertEqual(s.budget.max_story_cost_usd, MAX_STORY_COST_USD)
        self.assertEqual(s.timeouts.probe, 120)

    def test_file_overrides_default_and_is_reread_per_call(self) -> None:
        self.path.write_text("[budget]\nmax_story_cost_usd = 4.5\n[timeouts]\nprobe = 30\n")
        self.assertEqual(settings().budget.max_story_cost_usd, 4.5)
        self.assertEqual(settings().timeouts.probe, 30)
        self.path.write_text("[budget]\nmax_story_cost_usd = 7\n")
        self.assertEqual(settings().budget.max_story_cost_usd, 7.0)

    def test_env_overrides_file(self) -> None:
        self.path.write_text("[budget]\nmax_story_cost_usd = 4.5\n")
        with patch.dict(
            "os.environ",
            {"FACTORY_MAX_STORY_COST_USD": "2", "FACTORY_PROBE_TIMEOUT": "15"},
        ):
            s = settings()
        self.assertEqual(s.budget.max_story_cost_usd, 2.0)
        self.assertEqual(s.timeouts.probe, 15)

    def test_invalid_values_are_refused_not_defaulted(self) -> None:
        # A typo'd budget must not silently become $10: the operator set a cap.
        for text in (
            "[budget]\nmax_story_cost_usd = 'ten'\n",
            "[budget]\nmax_story_cost_usd = -1\n",
            "[timeouts]\nprobe = 0\n",
            "not toml [[[",
        ):
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(ValueError):
                    settings()


if __name__ == "__main__":
    unittest.main()
