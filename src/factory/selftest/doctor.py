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

``passed`` is False iff a blocking check failed; the CLI turns that into a
non-zero exit.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from factory.adapters.opencode import run_agent
from factory.agent_config import tiers

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


def run_doctor(
    *, offline: bool = False, which: Callable[[str], str | None] = shutil.which
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

    return report
