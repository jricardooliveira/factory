"""Tests for the model-tier registry (reserve frontier models for thinking)."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from factory import model_tiers as mt


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

    AGENTS_DIR = Path(__file__).resolve().parents[1] / ".opencode" / "agents"

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


if __name__ == "__main__":
    unittest.main()
