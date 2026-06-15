"""Model-tier registry: reserve frontier models for high-leverage thinking.

The factory allocates expensive models to judgment-heavy stages (planning,
architecture / design trade-offs, risk + security review) and a cheap model to
high-volume rote work (implementation). A failed implementation that re-enters
the coder is *escalated* to a frontier model — a retry is exactly the case where
cheap reasoning already proved insufficient.

Two source-of-truth maps, both env-overridable so the operator can re-point a
tier without editing code:

- ``AGENT_TIERS``  — which tier each stage runs at.
- ``TIER_DEFAULTS`` — which concrete opencode model backs each tier.

Each agent's ``.opencode/agents/<name>.md`` frontmatter carries a ``model_tier``
line that MUST match ``AGENT_TIERS`` (enforced by ``test_model_tiers``), so the
declared intent in the agent file and the orchestrator's behaviour can't drift.
"""

from __future__ import annotations

import os

# Tier → concrete opencode model id (provider/model). Mixed best-of-breed:
# Anthropic frontier for thinking, cheap OpenAI for typing.
TIER_DEFAULTS: dict[str, str] = {
    "frontier": "anthropic/claude-opus-4-8",
    "standard": "anthropic/claude-sonnet-4-6",
    "fast": "openai/gpt-5.4-mini",
}

# Which tier each agent runs at by default. The high-leverage thinking stages
# (planning, design trade-offs, risk/security review) get the frontier tier;
# implementation — the highest-volume call — gets the cheap tier.
AGENT_TIERS: dict[str, str] = {
    "spec-agent": "frontier",       # planning / decomposition — "is this the right work?"
    "architect-agent": "frontier",  # design trade-offs, risk, ADRs
    "tester-agent": "frontier",     # risk analysis + security / QA review (≈ PR review)
    "coder-agent": "fast",          # implementation — highest call volume
}

# Agents whose retry is escalated. A failed cheap attempt is the signal that the
# task needs more reasoning, not another cheap pass.
ESCALATE_ON_RETRY: dict[str, str] = {"coder-agent": "frontier"}

# Tier used for any agent not listed in AGENT_TIERS.
DEFAULT_TIER = "standard"


def model_for_tier(tier: str) -> str:
    """Concrete model id for a tier, honoring a ``FACTORY_TIER_<TIER>`` override."""
    override = os.environ.get(f"FACTORY_TIER_{tier.upper()}")
    if override:
        return override
    try:
        return TIER_DEFAULTS[tier]
    except KeyError:
        raise ValueError(f"Unknown model tier: {tier!r}") from None


def tier_for_agent(agent_name: str, attempt_number: int = 1) -> str:
    """The tier an agent runs at, applying retry-escalation past the first attempt."""
    if attempt_number > 1 and agent_name in ESCALATE_ON_RETRY:
        return ESCALATE_ON_RETRY[agent_name]
    return AGENT_TIERS.get(agent_name, DEFAULT_TIER)


def resolve_model(agent_name: str, attempt_number: int = 1) -> tuple[str, str]:
    """Return ``(model_id, tier)`` for an agent call (attempt 1-based)."""
    tier = tier_for_agent(agent_name, attempt_number)
    return model_for_tier(tier), tier
