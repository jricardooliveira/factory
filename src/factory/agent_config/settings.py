"""The operator's settings file, ``factory.toml`` at the checkout root.

Everything an operator may reasonably tune without touching code: the per-story
budget, subprocess timeouts and two opt-in switches. Precedence is
``environment variable > factory.toml > built-in default``; ``FACTORY_CONFIG`` points
at a different file. A missing file is fine (a wheel install has no checkout around
it), but a malformed one raises :class:`SettingsError` — a typo'd budget key must not
quietly leave the cap at its default.

Model choice stays in ``agents/tiers.toml``; this file never names a model.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from factory.agent_config.location import checkout_root
from factory.domain import gates

CONFIG_ENV = "FACTORY_CONFIG"
FILENAME = "factory.toml"
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


class SettingsError(ValueError):
    """factory.toml is unreadable or says something the factory cannot honour."""


@dataclass(frozen=True)
class Budget:
    max_story_cost_usd: float = gates.MAX_STORY_COST_USD
    max_coder_attempts: int = gates.MAX_CODER_ATTEMPTS
    max_tester_remediations: int = gates.MAX_TESTER_REMEDIATIONS
    max_rearchitect_loops: int = gates.MAX_REARCHITECT_LOOPS
    max_boundary_redesigns: int = gates.MAX_BOUNDARY_REDESIGNS


@dataclass(frozen=True)
class Timeouts:
    """Seconds."""

    command: int = 60
    test: int = 180
    build: int = 180
    agent: int = 600
    probe: int = 120
    stale_run: int = 3600


@dataclass(frozen=True)
class Features:
    run_tests: bool = False
    notify: bool = False


@dataclass(frozen=True)
class Settings:
    budget: Budget = Budget()
    timeouts: Timeouts = Timeouts()
    features: Features = Features()


def settings_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(CONFIG_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return checkout_root() / FILENAME


def _section(name: str, cls: type, raw: object, path: Path) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise SettingsError(f"{path}: [{name}] must be a table")
    known = {f.name: f for f in fields(cls)}
    unknown = sorted(set(raw) - set(known))
    if unknown:
        raise SettingsError(f"{path}: unknown key(s) in [{name}]: {', '.join(unknown)}")
    out: dict[str, object] = {}
    for key, value in raw.items():
        default = getattr(cls(), key)
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, float):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            ok = isinstance(value, int) and not isinstance(value, bool)
        if not ok:
            raise SettingsError(
                f"{path}: [{name}] {key} must be {type(default).__name__}, got {value!r}"
            )
        if not isinstance(value, bool) and value <= 0:
            raise SettingsError(f"{path}: [{name}] {key} must be positive, got {value!r}")
        out[key] = float(value) if isinstance(default, float) else value
    return out


def _env_flag(environ: Mapping[str, str], name: str, current: bool) -> bool:
    raw = environ.get(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return current


def load_settings(
    path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> Settings:
    """Parse ``factory.toml`` (if present) and apply environment overrides."""
    env = os.environ if environ is None else environ
    path = settings_path(env) if path is None else path
    data: dict[str, object] = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text())
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise SettingsError(f"{path}: {exc}") from exc
    sections = {"budget": Budget, "timeouts": Timeouts, "features": Features}
    unknown = sorted(set(data) - set(sections))
    if unknown:
        raise SettingsError(f"{path}: unknown section(s): {', '.join(unknown)}")
    built = {
        name: cls(**_section(name, cls, data[name], path)) if name in data else cls()
        for name, cls in sections.items()
    }
    timeouts: Timeouts = built["timeouts"]  # type: ignore[assignment]
    features: Features = built["features"]  # type: ignore[assignment]
    # Unparseable FACTORY_AGENT_TIMEOUT has always meant "ignore it", never a crash.
    try:
        agent = max(1, int(env["FACTORY_AGENT_TIMEOUT"]))
    except (KeyError, ValueError):
        agent = timeouts.agent
    return Settings(
        budget=built["budget"],  # type: ignore[arg-type]
        timeouts=Timeouts(**{**timeouts.__dict__, "agent": agent}),
        features=Features(
            run_tests=_env_flag(env, "FACTORY_RUN_TESTS", features.run_tests),
            notify=_env_flag(env, "FACTORY_NOTIFY", features.notify),
        ),
    )


def settings() -> Settings:
    """The live settings. Not cached: the file is tiny and tests flip env mid-run."""
    return load_settings()
