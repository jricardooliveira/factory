"""Tests for the model-tier registry (reserve frontier models for thinking)."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from factory.agent_config import tiers as mt


def _family(model: str) -> str:
    return "anthropic" if "claude" in model else "openai" if "gpt" in model else model


class TierMappingTests(unittest.TestCase):
    def test_planning_agents_run_frontier(self) -> None:
        for agent in ("spec-agent", "architect-agent"):
            self.assertEqual(mt.tier_for_agent(agent), "frontier", agent)

    def test_each_agent_has_its_role_tier(self) -> None:
        self.assertEqual(mt.tier_for_agent("tester-agent"), "review")
        self.assertEqual(mt.tier_for_agent("boundary-agent"), "check")
        self.assertEqual(mt.tier_for_agent("release-agent"), "notes")
        self.assertEqual(mt.tier_for_agent("interview-agent"), "intake")

    def test_coder_builds_on_first_attempt(self) -> None:
        self.assertEqual(mt.tier_for_agent("coder-agent", attempt_number=1), "build")

    def test_a_failed_coder_attempt_is_the_special_case(self) -> None:
        self.assertEqual(mt.tier_for_agent("coder-agent", attempt_number=2), "special")
        self.assertEqual(mt.tier_for_agent("coder-agent", attempt_number=3), "special")

    def test_reviewers_never_share_a_model_family_with_the_authors(self) -> None:
        """Operator decision (2026-10-02): OpenAI and Anthropic models side by side.
        Reviews are independent only when the reviewer is not the author's family:
        two models of one family tend to share blind spots."""
        authors = {_family(mt.resolve_model(a, 1)[0])
                   for a in ("spec-agent", "architect-agent", "coder-agent")}
        for reviewer in ("boundary-agent", "tester-agent"):
            with self.subTest(reviewer=reviewer):
                self.assertNotIn(_family(mt.resolve_model(reviewer, 1)[0]), authors)
        # ...and a failed attempt is retried by the OTHER family's strongest model.
        self.assertNotEqual(_family(mt.resolve_model("coder-agent", 2)[0]),
                            _family(mt.resolve_model("coder-agent", 1)[0]))

    def test_other_agents_do_not_escalate(self) -> None:
        # Only the coder escalates; an architect re-run stays frontier (no change).
        self.assertEqual(mt.tier_for_agent("architect-agent", attempt_number=2), "frontier")

    def test_unknown_agent_falls_back_to_default_tier(self) -> None:
        self.assertEqual(mt.tier_for_agent("mystery-agent"), mt.DEFAULT_TIER)


class ModelResolutionTests(unittest.TestCase):
    def test_resolve_model_returns_model_and_tier(self) -> None:
        model, tier = mt.resolve_model("architect-agent")
        self.assertEqual(tier, "frontier")
        self.assertEqual(model, mt.TIER_DEFAULTS["frontier"])

    def test_coder_resolves_build_then_special(self) -> None:
        self.assertEqual(mt.resolve_model("coder-agent", 1), (mt.TIER_DEFAULTS["build"], "build"))
        self.assertEqual(
            mt.resolve_model("coder-agent", 2), (mt.TIER_DEFAULTS["special"], "special")
        )

    def test_env_override_repoints_a_tier(self) -> None:
        import os

        os.environ["FACTORY_TIER_BUILD"] = "openai/some-other-cheap"
        try:
            self.assertEqual(mt.model_for_tier("build"), "openai/some-other-cheap")
        finally:
            del os.environ["FACTORY_TIER_BUILD"]
        self.assertEqual(mt.model_for_tier("build"), mt.TIER_DEFAULTS["build"])

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

    def test_defaults_are_the_latest_openai_and_anthropic_models(self) -> None:
        # Operator decision (2026-10-02). OpenAI through the ChatGPT/Codex login;
        # Anthropic through Requesty (`requesty/*` — anthropic/* is not configured).
        # Reachability is `factory doctor`'s to prove before a run spends anything.
        self.assertEqual(
            mt.TIER_DEFAULTS,
            {
                "frontier": "openai/gpt-6-astra",
                "build": "openai/gpt-6.1-sol",
                "notes": "openai/gpt-6-luna",
                "review": "requesty/claude-opus-5-5",
                "intake": "requesty/claude-opus-5-5",
                "check": "requesty/claude-sonnet-5-5",
                "special": "requesty/claude-fable-5.1",
                "standard": "requesty/claude-haiku-4-5",
            },
        )

    def test_every_default_model_has_a_list_price(self) -> None:
        """The $10-per-story cap estimates spend from tokens x list price, because the
        ChatGPT/Codex login reports $0. A model without a price is unbudgetable."""
        prices = mt.config().prices
        for tier, model in mt.TIER_DEFAULTS.items():
            with self.subTest(tier=tier):
                self.assertIn(model, prices)
        # USD per 1M tokens, 2026-10-02: OpenAI's announced GPT-6 prices and opencode's
        # metadata for the Requesty-routed models.
        expected = {
            "openai/gpt-6-astra": (10.0, 50.0), "openai/gpt-6.1-sol": (2.0, 10.0),
            "openai/gpt-6-luna": (0.1, 0.5), "requesty/claude-opus-5-5": (4.0, 20.0),
            "requesty/claude-sonnet-5-5": (2.0, 10.0), "requesty/claude-haiku-4-5": (1.0, 5.0),
            "requesty/claude-fable-5.1": (10.0, 50.0),
            # Kept so spend recorded under the previous models is still priced.
            "openai/gpt-5.5": (5.0, 30.0), "openai/gpt-5.5-fast": (12.5, 75.0),
        }
        for model, (inp, out) in expected.items():
            with self.subTest(model=model):
                self.assertEqual((prices[model].input_per_m, prices[model].output_per_m),
                                 (inp, out))

    def test_a_malformed_price_is_rejected_at_load(self) -> None:
        path = self._write(
            'default_tier = "big"\n[tiers]\nbig = "openai/y"\n[agents]\n'
            '[prices."openai/y"]\ninput = "five"\noutput = 30\n'
        )
        with self.assertRaises(ValueError):
            mt.load_tiers(path)

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
        # Two tiers on one model are probed once, not twice.
        import os

        os.environ["FACTORY_TIER_STANDARD"] = "openai/gpt-6.1-sol"
        try:
            models = mt.distinct_models()
        finally:
            del os.environ["FACTORY_TIER_STANDARD"]
        self.assertEqual(models["openai/gpt-6.1-sol"], ["build", "standard"])
        self.assertEqual(len(models), 6)

    def test_distinct_models_honors_env_overrides(self) -> None:
        import os

        os.environ["FACTORY_TIER_STANDARD"] = "openai/other"
        try:
            models = mt.distinct_models()
        finally:
            del os.environ["FACTORY_TIER_STANDARD"]
        self.assertEqual(models["openai/other"], ["standard"])
        self.assertNotIn("requesty/claude-haiku-4-5", models)
        self.assertEqual(models["openai/gpt-6-astra"], ["frontier"])


if __name__ == "__main__":
    unittest.main()
