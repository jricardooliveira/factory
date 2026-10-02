"""The story budget: never spend more than $10 on one user story (operator decision).

A subscription login reports cost $0 for real tokens, so spend is ESTIMATED from the
tokens each call used at the model's public API price (`agents/tiers.toml`
[prices]). A provider-reported cost wins when there is one. Pure policy here; the
enforcement before every model call is in tests/pipeline/test_story_budget.py.
"""

from __future__ import annotations

import unittest

from factory.domain.budget import ModelPrice, budget_refusal, call_cost, story_spend
from factory.domain.gates import MAX_STORY_COST_USD

PRICES = {"openai/gpt-5.5": ModelPrice(5.0, 30.0), "openai/gpt-5.5-fast": ModelPrice(12.5, 75.0)}


class CallCostTests(unittest.TestCase):
    def test_tokens_are_priced_at_the_model_list_price(self) -> None:
        cost = call_cost(1_000_000, 100_000, 0, "openai/gpt-5.5", PRICES)
        self.assertAlmostEqual(cost, 5.0 + 3.0)

    def test_a_provider_reported_cost_wins(self) -> None:
        self.assertEqual(call_cost(1000, 100, 0.42, "openai/gpt-5.5", PRICES), 0.42)

    def test_unknown_usage_or_an_unpriced_model_is_unknown_not_zero(self) -> None:
        self.assertIsNone(call_cost(None, None, None, "openai/gpt-5.5", PRICES))
        self.assertIsNone(call_cost(1000, 100, 0, "anthropic/other", PRICES))


class StorySpendTests(unittest.TestCase):
    def test_spend_sums_priced_calls_and_counts_the_unknown_ones(self) -> None:
        rows = [
            {"tokens_in": 1_000_000, "tokens_out": 0, "cost_usd": 0, "model_name": "openai/gpt-5.5"},
            {"tokens_in": 0, "tokens_out": 100_000, "cost_usd": 0,
             "model_name": "openai/gpt-5.5-fast"},
            {"tokens_in": None, "tokens_out": None, "cost_usd": None, "model_name": None},
        ]
        spend = story_spend(rows, PRICES)
        self.assertAlmostEqual(spend.estimated_usd, 5.0 + 7.5)
        self.assertEqual(spend.unknown_calls, 1)

    def test_the_cap_refuses_at_or_past_ten_dollars(self) -> None:
        self.assertEqual(MAX_STORY_COST_USD, 10.0)
        under = story_spend([{"tokens_in": 1_000_000, "tokens_out": 0, "cost_usd": 0,
                              "model_name": "openai/gpt-5.5"}], PRICES)
        self.assertIsNone(budget_refusal(under, MAX_STORY_COST_USD))
        over = story_spend([{"tokens_in": 0, "tokens_out": 200_000, "cost_usd": 0,
                             "model_name": "openai/gpt-5.5-fast"}], PRICES)
        message = budget_refusal(over, MAX_STORY_COST_USD)
        self.assertIsNotNone(message)
        self.assertIn("$15.00", message)
        self.assertIn("$10.00", message)


if __name__ == "__main__":
    unittest.main()
