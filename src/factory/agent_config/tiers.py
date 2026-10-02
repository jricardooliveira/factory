"""Model-tier registry: reserve frontier models for high-leverage thinking.

The factory allocates expensive models to judgment-heavy stages (planning,
architecture / design trade-offs, risk + security review) and a cheap model to
high-volume rote work (implementation). A failed implementation that re-enters
the coder is *escalated* to a frontier model — a retry is exactly the case where
cheap reasoning already proved insufficient.

The policy is DATA: ``agents/tiers.toml`` (next to the agent definitions it
governs) is the single source of truth, loaded on FIRST USE (`config()`) into:

- ``AGENT_TIERS``       — which tier each stage runs at.
- ``TIER_DEFAULTS``     — which concrete opencode model backs each tier.
- ``ESCALATE_ON_RETRY`` — which agents move up a tier on attempt 2+.
- ``DEFAULT_TIER``      — the tier of any agent the file does not list.

Lazily, not at import: every CLI verb imports this module through the pipeline,
so an import-time load made a missing or invalid tiers.toml crash read-only verbs
(`factory list`, `factory --help`) and `factory doctor` — the very command meant
to diagnose it. The four names above stay readable as module attributes.

A ``FACTORY_TIER_<TIER>`` environment variable still wins over the file, so the
operator can re-point a tier for one run without editing anything.

Each agent's ``agents/<name>.md`` frontmatter carries a ``model_tier`` line that
MUST match ``AGENT_TIERS`` (enforced by ``test_tiers`` and ``factory evals``), so
the declared intent in the agent file and the orchestrator's behaviour can't drift.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from factory.agent_config.location import TIERS_FILENAME, agents_dir


@dataclass(frozen=True)
class TierConfig:
    """The parsed, validated contents of a tiers.toml."""

    tier_models: dict[str, str]
    agent_tiers: dict[str, str]
    default_tier: str
    escalate_on_retry: dict[str, str] = field(default_factory=dict)


def default_tiers_path() -> Path:
    """``<agents dir>/tiers.toml`` — the checkout's, else the packaged copy."""
    return agents_dir() / TIERS_FILENAME


def _str_table(data: dict, key: str, path: Path, *, required: bool = True) -> dict[str, str]:
    table = data.get(key)
    if table is None and not required:
        return {}
    if not isinstance(table, dict):
        raise ValueError(f"{path}: missing [{key}] table")
    for name, value in table.items():
        if not isinstance(value, str):
            raise ValueError(f"{path}: [{key}] {name} must be a string, got {value!r}")
    return dict(table)


def load_tiers(path: Path | None = None) -> TierConfig:
    """Parse and validate a tiers.toml.

    Validation is strict on purpose: an agent pointed at a tier that does not
    exist would otherwise surface as a ``ValueError`` mid-run, after earlier
    agents had already been paid for.
    """
    path = path or default_tiers_path()
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    tier_models = _str_table(data, "tiers", path)
    agent_tiers = _str_table(data, "agents", path)
    escalate = _str_table(data, "escalate_on_retry", path, required=False)
    default_tier = data.get("default_tier")

    for tier, model in tier_models.items():
        if "/" not in model:
            raise ValueError(
                f"{path}: tier {tier!r} model {model!r} is not a provider/model id"
            )
    for section, mapping in (("agents", agent_tiers), ("escalate_on_retry", escalate)):
        for agent, tier in mapping.items():
            if tier not in tier_models:
                raise ValueError(f"{path}: [{section}] {agent} uses undefined tier {tier!r}")
    if default_tier not in tier_models:
        raise ValueError(f"{path}: default_tier {default_tier!r} is not a defined tier")

    return TierConfig(
        tier_models=tier_models,
        agent_tiers=agent_tiers,
        default_tier=default_tier,
        escalate_on_retry=escalate,
    )


@cache
def config() -> TierConfig:
    """The default tiers.toml, parsed once on first use (see the module docstring)."""
    return load_tiers()


_LAZY_ATTRS = {
    "TIER_DEFAULTS": "tier_models",
    "AGENT_TIERS": "agent_tiers",
    "ESCALATE_ON_RETRY": "escalate_on_retry",
    "DEFAULT_TIER": "default_tier",
}


def __getattr__(name: str) -> Any:
    # PEP 562: `tiers.AGENT_TIERS` keeps working, but reading it is what loads the file.
    if name in _LAZY_ATTRS:
        return getattr(config(), _LAZY_ATTRS[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def model_for_tier(tier: str) -> str:
    """Concrete model id for a tier, honoring a ``FACTORY_TIER_<TIER>`` override."""
    override = os.environ.get(f"FACTORY_TIER_{tier.upper()}")
    if override:
        return override
    try:
        return config().tier_models[tier]
    except KeyError:
        raise ValueError(f"Unknown model tier: {tier!r}") from None


def tier_for_agent(agent_name: str, attempt_number: int = 1) -> str:
    """The tier an agent runs at, applying retry-escalation past the first attempt."""
    cfg = config()
    if attempt_number > 1 and agent_name in cfg.escalate_on_retry:
        return cfg.escalate_on_retry[agent_name]
    return cfg.agent_tiers.get(agent_name, cfg.default_tier)


def resolve_model(agent_name: str, attempt_number: int = 1) -> tuple[str, str]:
    """Return ``(model_id, tier)`` for an agent call (attempt 1-based)."""
    tier = tier_for_agent(agent_name, attempt_number)
    return model_for_tier(tier), tier


def distinct_models() -> dict[str, list[str]]:
    """Every model a run could call (env overrides applied) -> the tiers it backs.

    Two tiers backed by one model share an entry, so a preflight probes each
    model once.
    """
    models: dict[str, list[str]] = {}
    for tier in config().tier_models:
        models.setdefault(model_for_tier(tier), []).append(tier)
    return models
