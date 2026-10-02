"""The story budget: what a user story has spent, and whether it may spend more (pure).

Operator decision (2026-10-02): never spend more than $10 on one user story. A
subscription login (ChatGPT/Codex) reports cost $0 for real tokens, so spend is
ESTIMATED from the tokens each call used at the model's public list price
(`agents/tiers.toml` [prices]); a provider-reported cost wins when there is one.
Cached input is priced as full input, so the estimate errs high — the safe side
for a cap.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelPrice:
    input_per_m: float  # USD per 1M input tokens
    output_per_m: float  # USD per 1M output tokens


@dataclass(frozen=True)
class Spend:
    estimated_usd: float
    priced_calls: int
    unknown_calls: int  # no usage recorded, or a model with no list price


def call_cost(
    tokens_in: int | None,
    tokens_out: int | None,
    provider_cost: float | None,
    model: str | None,
    prices: Mapping[str, ModelPrice],
) -> float | None:
    """USD for one call, or None when it cannot be known (never a silent zero)."""
    if provider_cost:
        return float(provider_cost)
    if tokens_in is None or tokens_out is None:
        return None
    price = prices.get(model or "")
    if price is None:
        return None
    return tokens_in / 1e6 * price.input_per_m + tokens_out / 1e6 * price.output_per_m


def story_spend(rows: Iterable[Mapping[str, Any]], prices: Mapping[str, ModelPrice]) -> Spend:
    """Total spend over agent_logs rows (tokens_in, tokens_out, cost_usd, model_name)."""
    total, priced, unknown = 0.0, 0, 0
    for row in rows:
        cost = call_cost(row.get("tokens_in"), row.get("tokens_out"), row.get("cost_usd"),
                         row.get("model_name"), prices)
        if cost is None:
            unknown += 1
        else:
            total += cost
            priced += 1
    return Spend(round(total, 6), priced, unknown)


def budget_refusal(spend: Spend, cap_usd: float) -> str | None:
    """Why no further model call may be made for this story, or None if it may."""
    if spend.estimated_usd < cap_usd:
        return None
    unknown = f" (+{spend.unknown_calls} call(s) with unknown usage)" if spend.unknown_calls else ""
    return (
        f"story budget spent: ~${spend.estimated_usd:.2f} of ${cap_usd:.2f}{unknown}, "
        "estimated at API list prices. Raise MAX_STORY_COST_USD in domain/gates.py to continue."
    )
