"""Operator settings: `factory.toml` at the checkout root (or $FACTORY_SETTINGS).

Precedence is env > file > default. `settings()` re-reads on every call, never
cached: a changed cap must apply to the next model call, not the next process.
A value that is present but invalid is REFUSED (ValueError), never replaced by the
default — a typo'd budget silently becoming $10 would spend money nobody approved.

    [budget]
    max_story_cost_usd = 10.0   # env FACTORY_MAX_STORY_COST_USD

    [timeouts]
    probe = 120                 # seconds per `factory doctor` model probe; env FACTORY_PROBE_TIMEOUT
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.agent_config.location import checkout_root
from factory.domain.gates import MAX_STORY_COST_USD

SETTINGS_ENV = "FACTORY_SETTINGS"
SETTINGS_FILENAME = "factory.toml"

# Far below the 10-minute agent default: a model that cannot say "OK" in two
# minutes is not usable for a run either, and the preflight must not stall.
DEFAULT_PROBE_TIMEOUT_SECS = 120


@dataclass(frozen=True)
class Budget:
    max_story_cost_usd: float


@dataclass(frozen=True)
class Timeouts:
    probe: int


@dataclass(frozen=True)
class Settings:
    budget: Budget
    timeouts: Timeouts


def settings_path() -> Path:
    override = os.environ.get(SETTINGS_ENV, "").strip()
    return Path(override).expanduser() if override else checkout_root() / SETTINGS_FILENAME


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: not valid TOML: {exc}") from None


def _value(data: dict[str, Any], table: str, key: str, env: str, default: Any, kind: type) -> Any:
    raw, source = default, "default"
    if key in (data.get(table) or {}):
        raw, source = data[table][key], f"{table}.{key}"
    if os.environ.get(env, "").strip():
        raw, source = os.environ[env].strip(), env
    try:
        value = kind(raw)
        # bool is an int subclass, and a string like "ten" fails kind() above.
        if isinstance(raw, bool) or value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{source} must be a positive number, got {raw!r}") from None
    return value


def settings() -> Settings:
    data = _load(settings_path())
    return Settings(
        budget=Budget(
            max_story_cost_usd=_value(
                data, "budget", "max_story_cost_usd", "FACTORY_MAX_STORY_COST_USD",
                MAX_STORY_COST_USD, float,
            ),
        ),
        timeouts=Timeouts(
            probe=_value(
                data, "timeouts", "probe", "FACTORY_PROBE_TIMEOUT",
                DEFAULT_PROBE_TIMEOUT_SECS, int,
            ),
        ),
    )
