"""Tests for the model-tier registry (reserve frontier models for thinking)."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from factory.agent_config import tiers as mt


class TierMappingTests(unittest.TestCase):
    def test_thinking_agents_run_frontier(self) -> None:
        for agent in ("spec-agent", "architect-agent", "tester-agent"):
            self.assertEqual(mt.tier_for_agent(agent), "frontier", agent)

    def test_coder_runs_fast_on_first_attempt(self) -> None:
        self.assertEqual(mt.tier_for_agent("coder-agent", attempt_number=1), "fast")

    def test_coder_escalates_to_frontier_on_retry(self) -> None:
        self.assertEqual(mt.tier_for_agent("coder-agent", attempt_number=2), "frontier")
        self.assertEqual(mt.tier_for_agent("coder-agent", attempt_number=3), "frontier")

    def test_thinking_agents_do_not_escalate(self) -> None:
        # Only the coder escalates; an architect re-run stays frontier (no change).
        self.assertEqual(mt.tier_for_agent("architect-agent", attempt_number=2), "frontier")

    def test_unknown_agent_falls_back_to_default_tier(self) -> None:
        self.assertEqual(mt.tier_for_agent("mystery-agent"), mt.DEFAULT_TIER)


class ModelResolutionTests(unittest.TestCase):
    def test_resolve_model_returns_model_and_tier(self) -> None:
        model, tier = mt.resolve_model("architect-agent")
        self.assertEqual(tier, "frontier")
        self.assertEqual(model, mt.TIER_DEFAULTS["frontier"])

    def test_coder_resolves_cheap_then_frontier(self) -> None:
        self.assertEqual(mt.resolve_model("coder-agent", 1), (mt.TIER_DEFAULTS["fast"], "fast"))
        self.assertEqual(
            mt.resolve_model("coder-agent", 2), (mt.TIER_DEFAULTS["frontier"], "frontier")
        )

    def test_env_override_repoints_a_tier(self) -> None:
        import os

        os.environ["FACTORY_TIER_FAST"] = "openai/some-other-cheap"
        try:
            self.assertEqual(mt.model_for_tier("fast"), "openai/some-other-cheap")
        finally:
            del os.environ["FACTORY_TIER_FAST"]
        self.assertEqual(mt.model_for_tier("fast"), mt.TIER_DEFAULTS["fast"])

    def test_unknown_tier_raises(self) -> None:
        with self.assertRaises(ValueError):
            mt.model_for_tier("platinum")


class FrontmatterDriftTests(unittest.TestCase):
    """The declared `model_tier` in each agent .md must match the registry."""

    AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"

    def _frontmatter(self, agent: str) -> dict:
        text = (self.AGENTS_DIR / f"{agent}.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), f"{agent}.md has no frontmatter")
        block = text.split("---", 2)[1]
        return yaml.safe_load(block) or {}

    def test_declared_tier_matches_registry(self) -> None:
        for agent, tier in mt.AGENT_TIERS.items():
            fm = self._frontmatter(agent)
            self.assertEqual(
                fm.get("model_tier"), tier,
                f"{agent}.md declares model_tier={fm.get('model_tier')!r}, registry says {tier!r}",
            )

    def test_declared_model_matches_tier_default(self) -> None:
        # The static `model:` is the fallback for manual `opencode run`; keep it
        # aligned with the tier's default so a hand-run matches the factory.
        for agent, tier in mt.AGENT_TIERS.items():
            fm = self._frontmatter(agent)
            self.assertEqual(fm.get("model"), mt.TIER_DEFAULTS[tier], agent)


class TiersTomlTests(unittest.TestCase):
    """agents/tiers.toml is the single source of truth for the tier policy."""

    TOML = Path(__file__).resolve().parents[2] / "agents" / "tiers.toml"

    def _write(self, text: str) -> Path:
        import tempfile

        tmp = Path(tempfile.mkdtemp()) / "tiers.toml"
        tmp.write_text(text, encoding="utf-8")
        return tmp

    def test_registry_is_loaded_from_agents_tiers_toml(self) -> None:
        import tomllib

        data = tomllib.loads(self.TOML.read_text(encoding="utf-8"))
        self.assertEqual(mt.default_tiers_path(), self.TOML)
        self.assertEqual(mt.TIER_DEFAULTS, data["tiers"])
        self.assertEqual(mt.AGENT_TIERS, data["agents"])
        self.assertEqual(mt.ESCALATE_ON_RETRY, data["escalate_on_retry"])
        self.assertEqual(mt.DEFAULT_TIER, data["default_tier"])

    def test_defaults_are_models_this_machine_can_reach(self) -> None:
        # The account is a ChatGPT/Codex login: gpt-5.4-mini is rejected and the
        # anthropic/* ids are not a configured provider (live-loop finding).
        self.assertEqual(
            mt.TIER_DEFAULTS,
            {
                "frontier": "openai/gpt-5.5",
                "standard": "openai/gpt-5.5",
                "fast": "openai/gpt-5.5-fast",
            },
        )

    def test_load_tiers_reads_an_arbitrary_file(self) -> None:
        path = self._write(
            'default_tier = "cheap"\n'
            "[tiers]\n"
            'cheap = "openai/x"\n'
            'big = "openai/y"\n'
            "[agents]\n"
            'coder-agent = "cheap"\n'
            "[escalate_on_retry]\n"
            'coder-agent = "big"\n'
        )
        cfg = mt.load_tiers(path)
        self.assertEqual(cfg.tier_models, {"cheap": "openai/x", "big": "openai/y"})
        self.assertEqual(cfg.agent_tiers, {"coder-agent": "cheap"})
        self.assertEqual(cfg.escalate_on_retry, {"coder-agent": "big"})
        self.assertEqual(cfg.default_tier, "cheap")

    def test_escalation_section_is_optional(self) -> None:
        path = self._write(
            'default_tier = "a"\n[tiers]\na = "p/m"\n[agents]\nspec-agent = "a"\n'
        )
        self.assertEqual(mt.load_tiers(path).escalate_on_retry, {})

    def test_agent_on_an_undefined_tier_is_rejected(self) -> None:
        path = self._write(
            'default_tier = "a"\n[tiers]\na = "p/m"\n[agents]\nspec-agent = "zzz"\n'
        )
        with self.assertRaisesRegex(ValueError, "zzz"):
            mt.load_tiers(path)

    def test_escalation_to_an_undefined_tier_is_rejected(self) -> None:
        path = self._write(
            'default_tier = "a"\n[tiers]\na = "p/m"\n[agents]\ncoder-agent = "a"\n'
            '[escalate_on_retry]\ncoder-agent = "zzz"\n'
        )
        with self.assertRaisesRegex(ValueError, "zzz"):
            mt.load_tiers(path)

    def test_undefined_default_tier_is_rejected(self) -> None:
        path = self._write('default_tier = "zzz"\n[tiers]\na = "p/m"\n[agents]\n')
        with self.assertRaisesRegex(ValueError, "zzz"):
            mt.load_tiers(path)

    def test_missing_tiers_table_is_rejected(self) -> None:
        path = self._write('default_tier = "a"\n[agents]\n')
        with self.assertRaisesRegex(ValueError, "tiers"):
            mt.load_tiers(path)

    def test_model_ids_must_be_provider_slash_model(self) -> None:
        path = self._write('default_tier = "a"\n[tiers]\na = "nomodel"\n[agents]\n')
        with self.assertRaisesRegex(ValueError, "provider/model"):
            mt.load_tiers(path)

    def test_distinct_models_groups_tiers_by_model(self) -> None:
        # frontier and standard share a model: doctor probes it once, not twice.
        self.assertEqual(
            mt.distinct_models(),
            {"openai/gpt-5.5": ["frontier", "standard"], "openai/gpt-5.5-fast": ["fast"]},
        )

    def test_distinct_models_honors_env_overrides(self) -> None:
        import os

        os.environ["FACTORY_TIER_STANDARD"] = "openai/other"
        try:
            models = mt.distinct_models()
        finally:
            del os.environ["FACTORY_TIER_STANDARD"]
        self.assertEqual(models["openai/other"], ["standard"])
        self.assertEqual(models["openai/gpt-5.5"], ["frontier"])


if __name__ == "__main__":
    unittest.main()
