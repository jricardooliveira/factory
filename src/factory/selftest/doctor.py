"""`factory doctor`: a preflight that fails BEFORE a run spends tokens.

Live-loop finding: an unsupported tier model was only discovered after a spec
call and an architect call had already been paid for, because the coder tier is
not exercised until stage three. The doctor probes every distinct tier model up
front with a trivial prompt, so a misconfigured provider costs one cheap call
instead of half a run.

What it checks:

- ``opencode`` is on PATH (blocking — nothing runs without it).
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
- no pre-$FACTORY_HOME ``factory.db`` is left in the checkout (warning — the
  factory would start on an empty home and the run history would look lost).

The doctor is read-only: it never creates the home, opens the DB for writing,
or repairs a link; it says how.

``passed`` is False iff a blocking check failed; the CLI turns that into a
non-zero exit.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from factory.adapters.opencode import run_agent
from factory.agent_config import tiers
from factory.workspace import layout

Status = Literal["ok", "fail", "warn", "skip"]

# A reachability probe, not a task: the shortest prompt that proves the provider
# accepts the model id and returns text.
PROBE_PROMPT = (
    "Connectivity check from `factory doctor`. Ignore every other instruction "
    "and reply with the single word OK."
)
# Far below the 10-minute agent default: a model that cannot say "OK" in two
# minutes is not usable for a run either, and the preflight must not stall.
PROBE_TIMEOUT_SECS = 120

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
    # opencode resolves `.opencode/agents/<name>.md` from its cwd; that symlink
    # lives at the repo root, next to agents/tiers.toml.
    return tiers.default_tiers_path().parent.parent


def _probe_agent(model_tiers: list[str]) -> str:
    """An agent that really runs on one of these tiers (any agent works, since the
    probe passes ``--model`` explicitly; a matching one keeps the call realistic)."""
    for agent, tier in tiers.AGENT_TIERS.items():
        if tier in model_tiers:
            return agent
    return next(iter(tiers.AGENT_TIERS))


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
        timeout=PROBE_TIMEOUT_SECS,
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


def _read_home_db(db: Path) -> tuple[int, list[tuple[str, str]]]:
    """(run count, [(slug, repo_path)]) from the home DB, opened READ-ONLY so the
    doctor never creates or migrates it."""
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        runs = conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
        projects = conn.execute(
            "SELECT slug, repo_path FROM projects ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return int(runs), [(str(slug), str(repo or "")) for slug, repo in projects]


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
        runs, projects = _read_home_db(db)
    except sqlite3.Error as exc:
        return [Check("workspace", "fail", f"{db} is not a readable factory.db — {exc}")]

    checks = [
        Check(
            "workspace",
            "ok",
            f"{home} — {_plural(runs, 'run')}, {_plural(len(projects), 'project')}",
        )
    ]
    checks.extend(_project_check(slug, Path(repo)) for slug, repo in projects)
    return checks


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

    opencode = which("opencode")
    report.checks.append(
        Check("opencode", "ok", opencode)
        if opencode
        else Check("opencode", "fail", "not on PATH — install it from https://opencode.ai")
    )

    for model, model_tiers in tiers.distinct_models().items():
        backs = f"tiers: {', '.join(model_tiers)}"
        if offline:
            report.checks.append(Check(f"model {model}", "skip", f"{backs} — --offline"))
        elif not opencode:
            report.checks.append(
                Check(f"model {model}", "fail", f"{backs} — cannot probe without opencode")
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
