"""The operator's settings file, ``factory.toml`` at the checkout root.

Everything an operator may reasonably tune without touching code: the per-story
budget, subprocess timeouts and two opt-in switches. Precedence is
``environment variable > factory.toml > built-in default``; ``FACTORY_CONFIG`` points
at a different file. A missing file is fine (a wheel install has no checkout around
it), but a malformed one raises :class:`SettingsError` — a typo'd budget key must not
quietly leave the cap at its default.

    [budget]
    max_story_cost_usd = 10.0   # env FACTORY_MAX_STORY_COST_USD

    [timeouts]
    probe = 120                 # seconds per `factory doctor` model probe; env FACTORY_PROBE_TIMEOUT

    [runner]
    agents = "opencode"         # or "claude" (the local Claude Code CLI); env FACTORY_RUNNER
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from factory.agent_config.location import checkout_root
from factory.domain import gates

CONFIG_ENV = "FACTORY_CONFIG"
FILENAME = "factory.toml"
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


class SettingsError(ValueError):
    """factory.toml is unreadable or says something the factory cannot honour."""

# What runs every agent call: `opencode run` with the tier models in agents/tiers.toml
# [tiers], or `claude -p` with the Claude models in [claude_tiers].
RUNNERS = ("opencode", "claude")


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
class Runner:
    agents: str


@dataclass(frozen=True)
class Settings:
    budget: Budget = Budget()
    timeouts: Timeouts = Timeouts()
    features: Features = Features()
    runner: Runner = Runner("opencode")


def settings_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = (env.get(CONFIG_ENV) or env.get("FACTORY_SETTINGS") or "").strip()
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
    unknown = sorted(set(data) - set(sections) - {"runner"})
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
    budget: Budget = built["budget"]  # type: ignore[assignment]
    # Preserve the established environment overrides while allowing every budget
    # and timeout field to be set in factory.toml.
    try:
        max_story_cost = float(env.get("FACTORY_MAX_STORY_COST_USD", budget.max_story_cost_usd))
        if max_story_cost <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise SettingsError("FACTORY_MAX_STORY_COST_USD must be a positive number") from None
    try:
        probe = int(env.get("FACTORY_PROBE_TIMEOUT", timeouts.probe))
        if probe <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise SettingsError("FACTORY_PROBE_TIMEOUT must be a positive integer") from None
    runner_data = data.get("runner", {})
    if not isinstance(runner_data, dict) or set(runner_data) - {"agents"}:
        raise SettingsError(f"{path}: [runner] must contain only agents")
    if "agents" in runner_data and not isinstance(runner_data["agents"], str):
        raise SettingsError(f"{path}: [runner] agents must be a string")
    runner = _choice(data, "runner", "agents", "FACTORY_RUNNER", RUNNERS, env, path)
    return Settings(
        budget=Budget(**{**budget.__dict__, "max_story_cost_usd": max_story_cost}),
        timeouts=Timeouts(**{**timeouts.__dict__, "agent": agent, "probe": probe}),
        features=Features(
            run_tests=_env_flag(env, "FACTORY_RUN_TESTS", features.run_tests),
            notify=_env_flag(env, "FACTORY_NOTIFY", features.notify),
        ),
        runner=Runner(agents=runner),
    )


def _choice(
    data: dict[str, Any], table: str, key: str, env_name: str,
    choices: tuple[str, ...], environ: Mapping[str, str], path: Path,
) -> str:
    raw, source = choices[0], "default"
    if key in (data.get(table) or {}):
        raw, source = data[table][key], f"{table}.{key}"
    if environ.get(env_name, "").strip():
        raw, source = environ[env_name].strip(), env_name
    if raw not in choices:
        raise SettingsError(f"{path}: {source} must be one of {', '.join(choices)}, got {raw!r}")
    return raw


def settings() -> Settings:
    """The live settings. Not cached: the file is tiny and tests flip env mid-run."""
    return load_settings()
