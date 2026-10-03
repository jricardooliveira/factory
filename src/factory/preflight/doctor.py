"""`factory doctor`: a preflight that fails BEFORE a run spends tokens.

Live-loop finding: an unsupported tier model was only discovered after a spec
call and an architect call had already been paid for, because the coder tier is
not exercised until stage three. The doctor probes every distinct tier model up
front with a trivial prompt, so a misconfigured provider costs one cheap call
instead of half a run.

What it checks:

- the runner (``opencode``, or ``claude`` under ``[runner] agents = "claude"``) is
  on PATH (blocking — nothing runs without it).
- each DISTINCT model in ``agents/tiers.toml`` (with ``FACTORY_TIER_*`` overrides
  applied) answers a one-line probe (blocking; skipped with ``offline=True``).
- ``go`` / ``node`` / ``tsc`` are on PATH (warning only — ``verification`` needs
  them for projects in that stack, and silently skipping a check is how a false
  PASS ships).
- ``$FACTORY_HOME`` (default ``~/.factory``) is usable and its ``factory.db``
  readable (blocking — every run writes there).
- each registered product's directory exists and its ``.opencode`` resolves to
  THIS checkout's agent definitions (warning — a stale link runs another
  checkout's agent configuration, not the one ``factory evals`` validated).
- each registered Python product has its own ``.venv`` (warning — without one its
  tests run with the factory's interpreter, which lacks the product's dependencies).
- no pre-$FACTORY_HOME ``factory.db`` is left in the checkout (warning — the
  factory would start on an empty home and the run history would look lost).

The doctor is read-only: it never creates the home, opens the DB for writing,
or repairs a link; it says how.

``passed`` is False iff a blocking check failed; the CLI turns that into a
non-zero exit.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from factory.adapters.opencode import run_agent
from factory.agent_config import tiers
from factory.agent_config.settings import settings
from factory.agent_config.location import checkout_root
from factory.state.reports import read_only_summary
from factory.workspace import layout

Status = Literal["ok", "fail", "warn", "skip"]

# A reachability probe, not a task: the shortest prompt that proves the provider
# accepts the model id and returns text.
PROBE_PROMPT = (
    "Connectivity check from `factory doctor`. Ignore every other instruction "
    "and reply with the single word OK."
)
# Toolchains `factory.verification` shells out to, and what each one verifies.
TOOLCHAINS: dict[str, str] = {
    "go": "go build/vet/test for Go projects",
    "node": "node --check for JavaScript projects",
    "tsc": "tsc -p for TypeScript projects",
}

# Where the pre-$FACTORY_HOME layout kept its DB, relative to the checkout: the
# old `mvp/` wrapper, and the repo root (a bare CWD-relative factory.db).
LEGACY_STATE_DIRS: tuple[str, ...] = ("mvp", ".")


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str = ""
    blocking: bool = True


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(c.blocking and c.status == "fail" for c in self.checks)


def repo_root() -> Path:
    # opencode resolves `.opencode/agents/<name>.md` from its cwd; that directory
    # lives at the checkout root, next to agents/.
    return checkout_root()


def _probe_agent(model_tiers: list[str]) -> str:
    """An agent that really runs on one of these tiers (any agent works, since the
    probe passes ``--model`` explicitly; a matching one keeps the call realistic)."""
    for agent, tier in tiers.AGENT_TIERS.items():
        if tier in model_tiers:
            return agent
    return next(iter(tiers.AGENT_TIERS))


def _escalation_check() -> Check:
    """Warn when escalate-on-retry lands on the model the first attempt used: the
    retry the policy pays a stronger model for would run the same model again."""
    cfg = tiers.config()
    flat = [
        f"{agent} ({cfg.agent_tiers.get(agent, cfg.default_tier)} -> {up}: "
        f"{tiers.model_for_tier(up)})"
        for agent, up in cfg.escalate_on_retry.items()
        if tiers.resolve_model(agent, 1)[0] == tiers.resolve_model(agent, 2)[0]
    ]
    if flat:
        return Check(
            "tier escalation", "warn",
            f"retry escalation is a no-op, both attempts use one model: {', '.join(flat)}",
            blocking=False,
        )
    return Check("tier escalation", "ok", "every escalation moves to a different model",
                 blocking=False)


def _prices_check() -> Check:
    """Warn when a tier's model has no list price: the $10-per-story cap estimates spend
    as tokens x price (a subscription login reports $0), so it would be spent blind."""
    prices = tiers.config().prices
    missing = sorted(m for m in tiers.distinct_models() if m not in prices)
    if missing:
        return Check(
            "price list", "warn",
            f"no list price in agents/tiers.toml [prices] for {', '.join(missing)} — "
            "their calls count as unknown toward the per-story budget",
            blocking=False,
        )
    return Check("price list", "ok", "every tier model has a list price", blocking=False)


def _first_line(text: str, limit: int = 200) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _probe(model: str, model_tiers: list[str]) -> Check:
    name = f"model {model}"
    backs = f"tiers: {', '.join(model_tiers)}"
    result = run_agent(
        _probe_agent(model_tiers),
        PROBE_PROMPT,
        cwd=str(repo_root()),
        model=model,
        timeout=settings().timeouts.probe,
    )
    output = result.output.strip()
    # opencode can exit 0 having streamed only an error event, so an empty answer
    # is not evidence the model works.
    if result.success and output and not output.startswith("ERROR"):
        return Check(name, "ok", f"{backs} — answered in {result.duration_secs}s")
    reason = _first_line(output) or f"no answer (exit {result.returncode})"
    return Check(name, "fail", f"{backs} — {reason}")


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _workspace_checks() -> list[Check]:
    home = layout.home()
    db = home / layout.DB_FILENAME
    if home.exists() and not home.is_dir():
        return [Check("workspace", "fail", f"{home} exists and is not a directory")]
    if home.exists() and not os.access(home, os.W_OK):
        return [Check("workspace", "fail", f"{home} is not writable")]
    if not db.exists():
        return [Check("workspace", "ok", f"{home} — no factory.db yet (created on first run)")]
    try:
        runs, projects = read_only_summary(db)
    except sqlite3.Error as exc:
        return [Check("workspace", "fail", f"{db} is not a readable factory.db — {exc}")]

    checks = [
        Check(
            "workspace",
            "ok",
            f"{home} — {_plural(runs, 'run')}, {_plural(len(projects), 'project')}",
        )
    ]
    for slug, repo in projects:
        path = layout.resolve_location(repo, db) or Path("")
        checks.append(_project_check(slug, path))
        venv = _venv_check(slug, path)
        if venv is not None:
            checks.append(venv)
    return checks


def _is_python_product(repo: Path) -> bool:
    if (repo / "requirements.txt").is_file() or (repo / "pyproject.toml").is_file():
        return True
    try:
        spec = json.loads((repo / "project-spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(spec, dict) and "python" in str(spec.get("language", "")).lower()


def _venv_check(slug: str, repo: Path) -> Check | None:
    """A Python product without its own .venv has its tests run by the FACTORY's
    interpreter (verification.python.product_python), which lacks its deps."""
    if not str(repo) or not repo.is_dir() or not _is_python_product(repo):
        return None
    if any((repo / ".venv" / p).is_file() for p in ("bin/python", "Scripts/python.exe")):
        return None
    # Advice must be followable: only name a manifest the product actually has.
    if (repo / "requirements.txt").is_file():
        fix = f"create one: cd {repo} && uv venv && uv pip install -r requirements.txt"
    elif (repo / "pyproject.toml").is_file():
        fix = f"create one: cd {repo} && uv venv && uv pip install -e ."
    else:
        fix = "create .venv once a story adds a dependency manifest"
    return Check(
        f"project {slug} python", "warn",
        f"no .venv — its tests would run with the factory's interpreter; {fix}",
        blocking=False,
    )


def _project_check(slug: str, repo: Path) -> Check:
    name = f"project {slug}"
    agents = repo_root() / ".opencode"
    if not str(repo) or not repo.is_dir():
        return Check(name, "warn", f"directory missing: {repo}", blocking=False)
    link = repo / ".opencode"
    fix = f"re-link: ln -sfn {agents} {link}"
    if not link.exists():
        target = f" -> {os.readlink(link)}" if link.is_symlink() else ""
        return Check(name, "warn", f".opencode missing or dangling{target}; {fix}",
                     blocking=False)
    if link.resolve() != agents.resolve():
        return Check(name, "warn", f".opencode is stale -> {link.resolve()}; {fix}",
                     blocking=False)
    return Check(name, "ok", str(repo), blocking=False)


def _legacy_state_check(checkout: Path) -> Check | None:
    found = [
        d for d in LEGACY_STATE_DIRS if (checkout / d / layout.DB_FILENAME).is_file()
    ]
    if not found:
        return None
    where = ", ".join(str((checkout / d / layout.DB_FILENAME).resolve()) for d in found)
    hint = f"factory workspace import-legacy {found[0]}"
    return Check(
        "legacy state",
        "warn",
        f"pre-$FACTORY_HOME state not imported: {where}; run `{hint}` from {checkout}",
        blocking=False,
    )


def run_doctor(
    *,
    offline: bool = False,
    which: Callable[[str], str | None] = shutil.which,
    checkout: Path | None = None,
) -> DoctorReport:
    report = DoctorReport()

    # The runner (factory.toml [runner] agents) is the one binary every agent call needs.
    try:
        runner = settings().runner.agents
    except ValueError as exc:
        report.checks.append(Check("settings", "fail", str(exc)))
        return report
    install = {"opencode": "https://opencode.ai", "claude": "https://claude.com/claude-code"}
    found = which(runner)
    report.checks.append(
        Check(runner, "ok", found)
        if found
        else Check(runner, "fail", f"not on PATH — install it from {install[runner]}")
    )

    try:
        tiers.config()
    except (OSError, ValueError) as exc:  # tomllib.TOMLDecodeError is a ValueError
        report.checks.append(
            Check("tiers", "fail", f"{tiers.default_tiers_path()}: {exc}")
        )
        models: dict[str, list[str]] = {}
    else:
        report.checks.append(Check("tiers", "ok", str(tiers.default_tiers_path())))
        report.checks.append(_escalation_check())
        report.checks.append(_prices_check())
        models = tiers.distinct_models()

    for model, model_tiers in models.items():
        backs = f"tiers: {', '.join(model_tiers)}"
        if offline:
            report.checks.append(Check(f"model {model}", "skip", f"{backs} — --offline"))
        elif not found:
            report.checks.append(
                Check(f"model {model}", "fail", f"{backs} — cannot probe without {runner}")
            )
        else:
            report.checks.append(_probe(model, model_tiers))

    for tool, purpose in TOOLCHAINS.items():
        path = which(tool)
        report.checks.append(
            Check(tool, "ok", path, blocking=False)
            if path
            else Check(tool, "warn", f"not on PATH — needed for {purpose}", blocking=False)
        )

    report.checks.extend(_workspace_checks())
    legacy = _legacy_state_check(checkout if checkout is not None else repo_root())
    if legacy is not None:
        report.checks.append(legacy)

    return report
